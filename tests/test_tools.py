import os
import json
import asyncio

import pytest

from conftest import run


def test_file_workflow(tools):
    assert run(tools.call('write_file', {'path':'drafts/plan.txt','content':'hello Agent\nnews'}))['ok']
    assert run(tools.call('read_file', {'path':'drafts/plan.txt'}))['data']['text']=='hello Agent\nnews'
    assert run(tools.call('search_content', {'keyword':'agent','dir':'drafts'}))['data']['matches'][0]['line']==1
    assert run(tools.call('list_dir', {'path':'drafts'}))['data'][0]['name']=='plan.txt'


@pytest.mark.parametrize('path',['../secret','.env/../../secret','/etc/passwd','C:/Windows/win.ini','a\\b','drafts/NUL.txt','drafts/test:stream','drafts/trailing.'])
@pytest.mark.parametrize('name',['list_dir','read_file','write_file'])
def test_path_escape(tools,path,name):
    args={'path':path}
    if name=='write_file':args['content']='x'
    assert not run(tools.call(name,args))['ok']


@pytest.mark.parametrize('name,args',[
 ('read_file',{}),('list_dir',{'path':'.','extra':True}),('search_content',{'keyword':'','dir':'.'}),
 ('search_content',{'keyword':'x','dir':'..'}),('write_file',{'path':'evidence/fake.json','content':'fake'}),
 ('write_file',{'path':'drafts/large','content':'x'*24001}),('bash',{'command':123}),('unknown',{})])
def test_invalid_arguments(tools,name,args):
    assert not run(tools.call(name,args))['ok']


@pytest.mark.parametrize('command',['env','cat ../secret','cat /etc/passwd','ls; whoami','ls | cat','echo $DEEPSEEK_API_KEY','cat $(pwd)','cat `pwd`','ls > output','ls -R','cat','head -n 101 profile.json','ls *','cat C:/Windows/win.ini'])
def test_bash_rejects(tools,command):
    assert not run(tools.call('bash',{'command':command}))['ok']


def test_bash_real_and_output_limit(tools):
    if not tools.settings.bash_path:pytest.skip('Bash not installed')
    (tools.root/'drafts').mkdir()
    (tools.root/'drafts/hello.txt').write_text('hello\nworld\n',encoding='utf-8')
    for command in ['pwd','ls','cat drafts/hello.txt','head -n 1 drafts/hello.txt','tail -n 1 drafts/hello.txt','wc -l drafts/hello.txt']:
        result=run(tools.call('bash',{'command':command}))
        assert result['ok'], result
        assert result['data']['exit_code']==0, result
    (tools.root/'drafts/long.txt').write_text('x'*20000,encoding='utf-8')
    result=run(tools.call('bash',{'command':'cat drafts/long.txt'}))
    assert result['ok'] and result['data']['truncated']
    assert len(result['data']['stdout'])==16000


def test_symlink(tools,tmp_path):
    outside=tmp_path/'outside.txt';outside.write_text('secret')
    try:(tools.root/'link').symlink_to(outside)
    except OSError:pytest.skip('OS does not allow creating symlinks')
    assert not run(tools.call('read_file',{'path':'link'}))['ok']
    assert not run(tools.call('bash',{'command':'cat link'}))['ok']


def test_news_tools_and_evidence(tools):
    result=run(tools.call('fetch_news',{'source_id':'huggingface'}))
    assert result['data']['status']=='success'
    item=result['data']['items'][0]['id']
    assert run(tools.call('read_article',{'item_id':item}))['ok']
    assert not run(tools.call('read_article',{'item_id':'not-fetched'}))['ok']
    assert run(tools.call('fetch_news',{'source_id':'huggingface'}))['data']['cached']


def test_redacted_events(tools):
    tools.secrets=('very-private-secret',)
    run(tools.call('write_file',{'path':'drafts/test','content':'very-private-secret reader@example.com'}))
    text=json.dumps(tools.events)
    assert 'very-private-secret' not in text and 'reader@example.com' not in text


def test_bash_does_not_load_inherited_startup_file(tools,monkeypatch):
    if not tools.settings.bash_path:pytest.skip('Bash not installed')
    poison=tools.root/'poison.sh'
    poison.write_text('echo inherited-startup-was-run\n',encoding='utf-8')
    monkeypatch.setenv('BASH_ENV',str(poison))
    result=run(tools.call('bash',{'command':'pwd'}))
    assert result['ok']
    assert 'inherited-startup-was-run' not in result['data']['stdout']


def test_bash_timeout_kills_process(tools,monkeypatch):
    if not tools.settings.bash_path:pytest.skip('Bash not installed')
    original_create=asyncio.create_subprocess_exec
    original_wait=asyncio.wait_for
    processes=[]
    async def slow_process(*args,**kwargs):
        proc=await original_create(tools.settings.bash_path,'--noprofile','--norc','-c','exec sleep 10',**kwargs)
        processes.append(proc)
        return proc
    async def short_wait(awaitable,timeout):
        return await original_wait(awaitable,timeout=.02)
    monkeypatch.setattr('app.tools.asyncio.create_subprocess_exec',slow_process)
    monkeypatch.setattr('app.tools.asyncio.wait_for',short_wait)
    result=run(tools.call('bash',{'command':'pwd'}))
    assert not result['ok'] and processes[0].returncode is not None
