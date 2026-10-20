import asyncio

import pytest

from app.agent import Agent, AgentError, validate_final
from app.storage import dumps
from conftest import FixtureSource, ScriptedClient, calls, final_message, run


def test_dynamic_calls_recovery_and_repeats(tools,settings):
    client=ScriptedClient([calls(('read_file',{'path':'missing'})),calls(('list_dir',{'path':'.'})),
        calls(('fetch_news',{'source_id':'huggingface'})),calls(('read_file',{'path':'profile.json'})),
        calls(('read_file',{'path':'profile.json'})),final_message])
    brief=run(Agent(settings,client).run(tools,{}))
    assert len(brief['items'])==1
    names=[p['name'] for k,p in tools.events if k=='tool']
    assert names==['read_file','list_dir','fetch_news','read_file','read_file']
    assert tools.events[1][1]['ok'] is False
    assert any('"ok": false' in m['content'] for m in client.messages[-1] if m['role']=='tool')


def test_invalid_final_repaired(tools,settings):
    client=ScriptedClient([calls(('fetch_news',{'source_id':'huggingface'})),{'content':'not JSON'},final_message])
    assert run(Agent(settings,client).run(tools,{}))['items']


def test_invalid_final_exhausted(tools,settings):
    client=ScriptedClient([calls(('fetch_news',{'source_id':'huggingface'})),*([{'content':'{}'}]*3)])
    with pytest.raises(AgentError,match='两次'):run(Agent(settings,client).run(tools,{}))


def test_all_sources_fail_not_empty_brief(tools):
    tools.source=FixtureSource(failures={'huggingface','hackernews'})
    run(tools.fetch_news('huggingface'));run(tools.fetch_news('hackernews'))
    with pytest.raises(ValueError,match='全部失败'):validate_final(dumps({'title':'今日','overview':'没有','items':[]}),tools)


def test_empty_and_partial(tools):
    tools.source=FixtureSource(empty=True)
    run(tools.fetch_news('huggingface'))
    tools.outcomes['hackernews']={'status':'failure'}
    result=validate_final(dumps({'title':'今日','overview':'没有匹配新闻','items':[]}),tools)
    assert result['items']==[] and result['warnings']


def test_fabricated_and_duplicate_citations(tools):
    run(tools.fetch_news('huggingface'))
    value={'title':'日报','overview':'概要','items':[{'title':'新闻','summary':'摘要','relevance':'相关','source_ids':['fake']}]}
    with pytest.raises(ValueError,match='未采集'):validate_final(dumps(value),tools)
    value['items'][0]['source_ids']=[next(iter(tools.items))]
    value['items'].append(value['items'][0])
    with pytest.raises(ValueError,match='相同来源'):validate_final(dumps(value),tools)


def test_round_limit(tools,settings):
    settings.max_rounds=2
    client=ScriptedClient([calls(('list_dir',{'path':'.'}))]*2)
    with pytest.raises(AgentError,match='轮次'):run(Agent(settings,client).run(tools,{}))


def test_tool_limit(tools,settings):
    settings.max_tools=1
    client=ScriptedClient([calls(('list_dir',{'path':'.'}),('read_file',{'path':'profile.json'}))])
    with pytest.raises(AgentError,match='次数'):run(Agent(settings,client).run(tools,{}))
    assert not any(k=='tool' for k,p in tools.events)


def test_total_timeout(tools,settings):
    class Slow:
        async def complete(self,*_):await asyncio.sleep(1)
    settings.agent_timeout=.01
    with pytest.raises(AgentError,match='总运行时间'):run(Agent(settings,Slow()).run(tools,{}))


def test_body_and_excerpt_are_labelled_separately(tools):
    run(tools.fetch_news('huggingface'))
    item_id=next(iter(tools.items))
    raw=dumps({'title':'日报','overview':'概要','items':[{'title':'新闻','summary':'摘要','relevance':'相关','source_ids':[item_id]}]})
    assert validate_final(raw,tools)['items'][0]['evidence_level']=='仅来源摘要，未读取原网页正文'
    run(tools.read_article(item_id))
    assert validate_final(raw,tools)['items'][0]['evidence_level']=='已读取 1/1 个来源的原网页正文片段'
