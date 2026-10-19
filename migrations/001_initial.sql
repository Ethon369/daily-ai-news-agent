CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS subscriptions (
 id TEXT PRIMARY KEY, nickname TEXT NOT NULL, email TEXT NOT NULL,
 topics TEXT NOT NULL, keywords TEXT NOT NULL, send_time TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
 id TEXT PRIMARY KEY, subscription_id TEXT NOT NULL REFERENCES subscriptions(id),
 kind TEXT NOT NULL CHECK(kind IN ('preview','daily')), local_date TEXT NOT NULL,
 snapshot TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
 created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS daily_once ON runs(subscription_id, local_date) WHERE kind='daily';
CREATE INDEX IF NOT EXISTS run_queue ON runs(status, created_at);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
 kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS briefings (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),
 subscription_id TEXT NOT NULL REFERENCES subscriptions(id), content TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliveries (
 id TEXT PRIMARY KEY, briefing_id TEXT NOT NULL UNIQUE REFERENCES briefings(id),
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 1, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO schema_migrations VALUES (1);
