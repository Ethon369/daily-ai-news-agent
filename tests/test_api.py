import time

from fastapi.testclient import TestClient

from app.main import create_app
from conftest import FixtureSource, ScriptedClient, calls, final_message
from test_service import RecordingMailer

HEADERS={'X-Requested-With':'DailyBrief'}
PAYLOAD={'nickname':'读者','email':'reader@example.com','topics':['AI'],'keywords':['Agent'],'enabled':False}


def test_api_complete_flow_and_restart(settings):
    mailer=RecordingMailer()
    client=ScriptedClient([calls(('fetch_news',{'source_id':'huggingface'})),final_message])
    app=create_app(settings,client,FixtureSource(),mailer)
    with TestClient(app,base_url='http://localhost',headers=HEADERS) as web:
        assert web.get('/').status_code==200
        assert web.get('/static/app.js').status_code==200
        assert web.get('/api/health').json()['data']['status']=='ok'
        result=web.post('/api/subscriptions',json=PAYLOAD)
        assert result.status_code==201,result.text
        sub=result.json()['data']
        assert web.patch(f'/api/subscriptions/{sub["id"]}',json={'send_time':'08:30'}).json()['data']['send_time']=='08:30'
        result=web.post(f'/api/subscriptions/{sub["id"]}/runs')
        assert result.status_code==202
        rid=result.json()['data']['id']
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            row=web.get(f'/api/runs/{rid}').json()['data']
            if row['status'] not in ('running','queued'):break
            time.sleep(.02)
        assert row['status']=='succeeded',row
        assert row['events'] and 'snapshot' not in row
        assert mailer.sent==[]
        bid=row['briefing_id']
        brief=web.get(f'/api/briefings/{bid}').json()['data']
        assert brief['content']['items'][0]['sources'][0]['url']=='https://example.com/ai'
        assert web.post(f'/api/briefings/{bid}/deliveries').status_code==202
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            if web.get(f'/api/briefings/{bid}').json()['data']['delivery']['status']=='accepted':break
            time.sleep(.02)
        web.post(f'/api/briefings/{bid}/deliveries')
        assert len(mailer.sent)==1
    with TestClient(create_app(settings),base_url='http://localhost') as web:
        assert len(web.get('/api/subscriptions').json()['data'])==1
        assert len(web.get(f'/api/subscriptions/{sub["id"]}/briefings').json()['data'])==1
        assert web.get(f'/api/briefings/{bid}').json()['data']['delivery']['status']=='accepted'


def test_api_validation_and_local_guard(settings):
    with TestClient(create_app(settings),base_url='http://localhost') as web:
        assert web.post('/api/subscriptions',json=PAYLOAD).status_code==403
        assert web.post('/api/subscriptions',json=PAYLOAD,headers={**HEADERS,'Origin':'https://evil.test'}).status_code==403
        assert web.get('/api/health',headers={'Host':'evil.test'}).status_code==403
        assert web.post('/api/subscriptions',json={**PAYLOAD,'email':'bad'},headers=HEADERS).status_code==422
        assert web.post('/api/subscriptions',json={**PAYLOAD,'send_time':'25:00'},headers=HEADERS).status_code==422
        assert web.post('/api/subscriptions',json={**PAYLOAD,'topics':[],'keywords':[]},headers=HEADERS).status_code==422
        result=web.post('/api/subscriptions',json=PAYLOAD,headers=HEADERS).json()['data']
        assert web.patch('/api/subscriptions/'+result['id'],json={'email':None},headers=HEADERS).status_code==422
        assert web.get('/api/runs/missing').status_code==404
        assert 'error' in web.get('/api/unknown').json()
