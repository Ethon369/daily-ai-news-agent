import asyncio
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .agent import Agent, AgentError
from .mailer import Mailer
from .rendering import markdown
from .storage import Store, dumps, now_iso, redact
from .tools import Toolset

BEIJING = ZoneInfo('Asia/Shanghai')


class InstanceLock:
    def __init__(self, path):
        self.path, self.file = path, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, 'a+b')
        if self.path.stat().st_size == 0:
            self.file.write(b'0')
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('同一数据目录已有实例运行；请使用单进程、单 worker') from exc

    def release(self):
        if self.file:
            self.file.close()
            self.file = None


class Service:
    def __init__(self, settings, client=None, source=None, mailer=None):
        self.settings = settings
        self.store = Store(settings.data_dir)
        self.agent = Agent(settings, client)
        self.source = source
        self.mailer = mailer or Mailer(settings)
        self.lock = InstanceLock(settings.data_dir / 'instance.lock')
        self.tasks = set()
        self.wakeup = asyncio.Event()

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def start(self):
        self.lock.acquire()
        try:
            self.store.initialize()
            self.store.recover()
            self.spawn(self.worker())
            if self.settings.scheduler_enabled:
                self.spawn(self.scheduler())
        except BaseException:
            self.lock.release()
            raise

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.lock.release()

    def tick(self, current=None):
        current = (current or datetime.now(timezone.utc)).astimezone(BEIJING)
        created = []
        for sub in self.store.subscriptions():
            if sub['enabled'] and sub['send_time'] <= current.strftime('%H:%M'):
                run_id, is_new = self.store.create_run(sub, 'daily', current.date().isoformat())
                if is_new:
                    created.append(run_id)
        if created:
            self.wakeup.set()
        return created

    async def scheduler(self):
        while True:
            self.tick()
            await asyncio.sleep(60)

    async def worker(self):
        while True:
            self.wakeup.clear()
            run = self.store.one("SELECT * FROM runs WHERE status='queued' ORDER BY created_at LIMIT 1")
            if run:
                try:
                    await self.execute_run(run)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Preparation failures must not silently stop the queue worker.
                    self.store.execute("UPDATE runs SET status='failed',error=?,finished_at=? WHERE id=?",
                                       (f'任务准备失败 ({type(exc).__name__})', now_iso(), run['id']))
            else:
                await self.wakeup.wait()

    async def execute_run(self, run):
        run_id = run['id']
        self.store.execute("UPDATE runs SET status='running',started_at=? WHERE id=?", (now_iso(), run_id))
        sub = json.loads(run['snapshot'])
        root = self.settings.data_dir / 'runs' / run_id
        root.mkdir(parents=True, exist_ok=True)
        # No identity/email/credentials in any model context or tool workspace.
        profile = {'topics': sub['topics'], 'keywords': sub['keywords']}
        (root/'profile.json').write_text(dumps(profile), encoding='utf-8')
        history_rows = self.store.all('SELECT content FROM briefings WHERE subscription_id=? ORDER BY created_at DESC LIMIT 3', (sub['id'],))
        history = [{'title': b['title'], 'items': [i['title'] for i in b['items']]} for b in (json.loads(r['content']) for r in history_rows)]
        (root/'history.json').write_text(dumps(history), encoding='utf-8')
        secrets = (self.settings.api_key, self.settings.smtp_password, self.settings.smtp_username, self.settings.smtp_from, sub['email'])

        def event(kind, payload):
            self.store.event(run_id, kind, json.loads(redact(dumps(payload), secrets)))

        tools = Toolset(root, self.settings, datetime.now(timezone.utc), event, self.source)
        try:
            content = await self.agent.run(tools, profile)
            # Server-generated artifacts cannot be overwritten by the LLM.
            (root/'briefing.json').write_text(dumps(content), encoding='utf-8')
            (root/'briefing.md').write_text(markdown(content), encoding='utf-8')
            brief_id = self.store.finish_run(run_id, content)
        except asyncio.CancelledError:
            self.store.execute("UPDATE runs SET status='interrupted',error='服务停止，请重新生成预览',finished_at=? WHERE id=?", (now_iso(), run_id))
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, AgentError) else f'执行失败 ({type(exc).__name__})'
            error = redact(error, secrets)
            event('failed', {'message': error})
            self.store.execute("UPDATE runs SET status='failed',error=?,finished_at=? WHERE id=?", (error, now_iso(), run_id))
            return
        if run['kind'] == 'daily':
            delivery, is_new = self.store.claim_delivery(brief_id)
            if is_new:
                await self.deliver(delivery)

    async def deliver(self, delivery):
        row = self.store.one('''SELECT b.content,r.snapshot FROM briefings b JOIN runs r ON b.run_id=r.id WHERE b.id=?''', (delivery['briefing_id'],))
        try:
            status, error = await asyncio.to_thread(self.mailer.send, json.loads(row['snapshot'])['email'], json.loads(row['content']), delivery['id'])
        except asyncio.CancelledError:
            self.store.execute("UPDATE deliveries SET status='uncertain',error='服务停止，投递结果待核查',updated_at=? WHERE id=?", (now_iso(), delivery['id']))
            raise
        except Exception:
            status, error = 'uncertain', '邮件模块发生异常，结果待核查；不会自动重发'
        self.store.execute('UPDATE deliveries SET status=?,error=?,updated_at=? WHERE id=?', (status, error, now_iso(), delivery['id']))
