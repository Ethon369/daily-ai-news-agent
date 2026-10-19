import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from .config import ROOT


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def new_id():
    return uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, default=str)


def redact(value, secrets=()):
    text = value if isinstance(value, str) else dumps(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, '[REDACTED]')
    return re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[EMAIL]', text)


class Store:
    def __init__(self, data_dir):
        self.root = data_dir
        self.path = data_dir / 'news.db'

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA busy_timeout=10000')
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript((ROOT / 'migrations/001_initial.sql').read_text(encoding='utf-8'))

    def one(self, sql, args=()):
        with self.connection() as conn:
            row = conn.execute(sql, args).fetchone()
            return dict(row) if row else None

    def all(self, sql, args=()):
        with self.connection() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def execute(self, sql, args=()):
        with self.connection() as conn:
            return conn.execute(sql, args).rowcount

    def subscription(self, sub_id):
        row = self.one('SELECT * FROM subscriptions WHERE id=?', (sub_id,))
        return self.decode_subscription(row) if row else None

    @staticmethod
    def decode_subscription(row):
        return {**row, 'topics': json.loads(row['topics']), 'keywords': json.loads(row['keywords']),
                'enabled': bool(row['enabled'])}

    def subscriptions(self):
        return [self.decode_subscription(r) for r in self.all('SELECT * FROM subscriptions ORDER BY created_at')]

    def save_subscription(self, data, sub_id=None):
        stamp = now_iso()
        sub_id = sub_id or new_id()
        with self.connection() as conn:
            conn.execute('''INSERT INTO subscriptions VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET nickname=excluded.nickname,email=excluded.email,
                topics=excluded.topics,keywords=excluded.keywords,send_time=excluded.send_time,
                enabled=excluded.enabled,updated_at=excluded.updated_at''',
                (sub_id, data['nickname'], data['email'], dumps(data['topics']), dumps(data['keywords']),
                 data['send_time'], int(data['enabled']), stamp, stamp))
        return self.subscription(sub_id)

    def create_run(self, sub, kind, local_date):
        run_id = new_id()
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Prevent accidental double-click previews; daily uniqueness is also enforced by SQL.
            existing = conn.execute("SELECT id FROM runs WHERE subscription_id=? AND kind=? AND status IN ('queued','running')", (sub['id'], kind)).fetchone()
            if existing:
                return existing['id'], False
            try:
                conn.execute('INSERT INTO runs (id,subscription_id,kind,local_date,snapshot,status,created_at) VALUES (?,?,?,?,?,?,?)',
                             (run_id, sub['id'], kind, local_date, dumps(sub), 'queued', now_iso()))
            except sqlite3.IntegrityError:
                row = conn.execute("SELECT id FROM runs WHERE subscription_id=? AND local_date=? AND kind='daily'", (sub['id'], local_date)).fetchone()
                if row:
                    return row['id'], False
                raise
        return run_id, True

    def event(self, run_id, kind, payload):
        self.execute('INSERT INTO events(run_id,kind,payload,created_at) VALUES (?,?,?,?)',
                     (run_id, kind, dumps(payload), now_iso()))

    def finish_run(self, run_id, content):
        brief_id = new_id()
        with self.connection() as conn:
            run = conn.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            conn.execute('INSERT INTO briefings VALUES (?,?,?,?,?)',
                         (brief_id, run_id, run['subscription_id'], dumps(content), now_iso()))
            conn.execute("UPDATE runs SET status='succeeded',finished_at=? WHERE id=?", (now_iso(), run_id))
        return brief_id

    def recover(self):
        self.execute("UPDATE runs SET status='interrupted',error='服务中断，请重新生成预览',finished_at=? WHERE status='running'", (now_iso(),))
        self.execute("UPDATE deliveries SET status='uncertain',error='服务中断，提交结果不确定，请先核查收件箱',updated_at=? WHERE status='sending'", (now_iso(),))

    def claim_delivery(self, briefing_id):
        with self.connection() as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM deliveries WHERE briefing_id=?', (briefing_id,)).fetchone()
            if row and row['status'] != 'failed':
                return dict(row), False
            if row:
                conn.execute("UPDATE deliveries SET status='sending',error=NULL,attempts=attempts+1,updated_at=? WHERE id=?", (now_iso(), row['id']))
            else:
                conn.execute('INSERT INTO deliveries VALUES (?,?,?,?,?,?,?)',
                             (new_id(), briefing_id, 'sending', 1, None, now_iso(), now_iso()))
            result = conn.execute('SELECT * FROM deliveries WHERE briefing_id=?', (briefing_id,)).fetchone()
            return dict(result), True
