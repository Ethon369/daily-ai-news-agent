import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app.service import InstanceLock, Service
from app.storage import now_iso
from conftest import FixtureSource, ScriptedClient, calls, final_message, run


def client():
    return ScriptedClient([calls(('fetch_news',{'source_id':'huggingface'})),final_message])


class RecordingMailer:
    def __init__(self):self.sent=[]
    def send(self,recipient,brief,delivery_id):
        self.sent.append((recipient,brief,delivery_id))
        return 'accepted',None


def test_preview_never_sends_and_daily_does(settings,sub,store):
    mailer=RecordingMailer()
    service=Service(settings,client(),FixtureSource(),mailer)
    rid,_=store.create_run(sub,'preview','2026-10-18')
    run(service.execute_run(store.one('SELECT * FROM runs WHERE id=?',(rid,))))
    assert store.one('SELECT status FROM runs WHERE id=?',(rid,))['status']=='succeeded'
    assert mailer.sent==[]
    service.agent.client=client()
    rid,_=store.create_run(sub,'daily','2026-10-18')
    # Later editing the subscription does not silently retarget an existing run.
    store.save_subscription({**sub,'email':'changed@example.com'},sub['id'])
    run(service.execute_run(store.one('SELECT * FROM runs WHERE id=?',(rid,))))
    assert mailer.sent[0][0]=='reader@example.com'
    assert store.one('SELECT status FROM deliveries')['status']=='accepted'
    root=settings.data_dir/'runs'/rid
    assert 'reader@example.com' not in (root/'profile.json').read_text()


def test_due_date_disable_and_duplicate(settings,sub,store):
    service=Service(settings)
    assert service.tick(datetime(2026,10,18,0,59,tzinfo=timezone.utc))==[]
    ids=service.tick(datetime(2026,10,18,1,0,tzinfo=timezone.utc))
    assert len(ids)==1
    assert service.tick(datetime(2026,10,18,8,0,tzinfo=timezone.utc))==[]
    store.execute("UPDATE runs SET status='failed' WHERE id=?",(ids[0],))
    assert service.tick(datetime(2026,10,18,9,0,tzinfo=timezone.utc))==[]
    assert len(service.tick(datetime(2026,10,19,1,0,tzinfo=timezone.utc)))==1
    store.save_subscription({**sub,'enabled':False},sub['id'])
    assert service.tick(datetime(2026,10,20,1,0,tzinfo=timezone.utc))==[]
    assert len(store.all('SELECT * FROM runs'))==2


def test_concurrent_daily_uniqueness(store,sub):
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:store.create_run(sub,'daily','2026-10-18'),range(12)))
    assert sum(created for _,created in results)==1
    assert len(set(rid for rid,_ in results))==1


def test_delivery_claim_idempotence_recovery(store,sub):
    rid,_=store.create_run(sub,'preview','2026-10-18')
    bid=store.finish_run(rid,{'title':'x','overview':'x','items':[]})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:store.claim_delivery(bid),range(10)))
    assert sum(created for _,created in results)==1
    store.execute("UPDATE runs SET status='running' WHERE id=?",(rid,))
    store.recover()
    assert store.one('SELECT status FROM runs')['status']=='interrupted'
    assert store.one('SELECT status FROM deliveries')['status']=='uncertain'
    assert store.claim_delivery(bid)[1] is False
    store.execute("UPDATE deliveries SET status='failed'")
    delivery,created=store.claim_delivery(bid)
    assert created and delivery['attempts']==2


def test_instance_lock(settings):
    lock=InstanceLock(settings.data_dir/'test.lock');lock.acquire()
    second=InstanceLock(settings.data_dir/'test.lock')
    try:
        with pytest.raises(RuntimeError):second.acquire()
    finally:lock.release()
    second.acquire();second.release()


def test_worker_failure_is_recorded(settings,store,sub):
    class BadClient:
        async def complete(self,*args):raise RuntimeError('API-key-must-not-be-logged')
    service=Service(settings,BadClient(),FixtureSource())
    rid,_=store.create_run(sub,'preview','2026-10-18')
    run(service.execute_run(store.one('SELECT * FROM runs WHERE id=?',(rid,))))
    row=store.one('SELECT status,error FROM runs WHERE id=?',(rid,))
    assert row['status']=='failed' and 'API-key' not in row['error']


def test_preparation_failure_does_not_kill_worker(settings,store,sub):
    async def scenario():
        service=Service(settings)
        rid,_=store.create_run(sub,'preview','2026-10-18')
        async def broken(_):raise OSError('workspace unavailable')
        service.execute_run=broken
        worker=asyncio.create_task(service.worker())
        await asyncio.sleep(.05)
        assert not worker.done()
        assert store.one('SELECT status FROM runs WHERE id=?',(rid,))['status']=='failed'
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
    run(scenario())
