"""SQLite time-series store for Ghost-Intel (stdlib-only, local-first).

Honest scope: the store is LOCAL SQLite. It keeps:
  * targets  — the monitored URLs + their config
  * snapshots — one row per successful fetch (content hash, prices, text)
  * changes   — detected deltas between consecutive snapshots (with timestamps)

A remote warehouse (Supabase/Postgres) needs an HTTP client, not
sqlite3.connect(); like Nexus-Zero we FAIL LOUDLY on a remote DSN instead of
pretending. Persist the local file, or bring your own adapter behind Storage.

The schema is a time-series on purpose: every fact carries a `ts` so you can
chart a competitor's price over weeks/months.
"""
import os
import re
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  kind TEXT DEFAULT 'page',        -- page | price | social | leads
  keywords TEXT DEFAULT '',        -- comma-separated
  added REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  status INTEGER,
  status_class TEXT,
  content_hash TEXT,
  prices TEXT,                      -- json array
  text_len INTEGER,
  text TEXT,                        -- extracted readable text (for diffing)
  note TEXT
);
CREATE INDEX IF NOT EXISTS snapshots_target_ts ON snapshots(target_id, ts);
CREATE TABLE IF NOT EXISTS changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  target_id INTEGER NOT NULL,
  ts REAL NOT NULL,
  kind TEXT,                        -- content | price | status
  detail TEXT                       -- json
);
CREATE INDEX IF NOT EXISTS changes_ts ON changes(ts);
"""

DEFAULT_DB = os.environ.get("GHOST_DB", "ghost-intel.db")
_URL_DSN = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")


def _clamp(limit):
    try:
        return max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        return 50


class Storage(object):
    def __init__(self, path=None):
        self.path = os.path.expanduser(path or DEFAULT_DB)
        if _URL_DSN.match(self.path):
            raise ValueError(
                "GHOST_DB must be a local SQLite path, not a URL/DSN; a remote "
                "warehouse needs an HTTP client (not sqlite3.connect).")
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass
        self.conn.executescript(SCHEMA)

    # ----------------------------------------------------------- targets
    def add_target(self, name, url, kind="page", keywords=""):
        self.conn.execute(
            "INSERT INTO targets (name, url, kind, keywords, added) "
            "VALUES (?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET "
            "name=excluded.name, kind=excluded.kind, keywords=excluded.keywords",
            (name, url, kind, keywords, time.time()))
        return self.target_id(url)

    def target_id(self, url):
        row = self.conn.execute("SELECT id FROM targets WHERE url=?",
                                (url,)).fetchone()
        return row[0] if row else None

    def list_targets(self):
        rows = self.conn.execute(
            "SELECT id, name, url, kind, keywords FROM targets ORDER BY id").fetchall()
        return [{"id": r[0], "name": r[1], "url": r[2], "kind": r[3],
                 "keywords": r[4]} for r in rows]

    # --------------------------------------------------------- snapshots
    # Cap the stored readable text: unbounded text would grow ~hundreds of MB
    # per target per year on a cron, and it's only needed for diffing the most
    # recent couple of versions (finding: resource exhaustion).
    MAX_STORED_TEXT = 200 * 1024         # 200 KB of extracted text per snapshot

    def add_snapshot(self, target_id, status, status_class, content_hash,
                     prices, text_len, text="", note=""):
        import json as _json
        if text and len(text) > self.MAX_STORED_TEXT:
            text = text[:self.MAX_STORED_TEXT]
        self.conn.execute(
            "INSERT INTO snapshots (target_id, ts, status, status_class, "
            "content_hash, prices, text_len, text, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (target_id, time.time(), status, status_class, content_hash,
             _json.dumps(prices or []), int(text_len or 0), text, note))

    def latest_snapshot(self, target_id):
        import json as _json
        row = self.conn.execute(
            "SELECT ts, status, status_class, content_hash, prices, text_len, text "
            "FROM snapshots WHERE target_id=? ORDER BY ts DESC LIMIT 1",
            (target_id,)).fetchone()
        if not row:
            return None
        return {"ts": row[0], "status": row[1], "status_class": row[2],
                "content_hash": row[3], "prices": _json.loads(row[4] or "[]"),
                "text_len": row[5], "text": row[6] or ""}

    def price_history(self, target_id, limit=100):
        """Time-series of (ts, prices) for charting — oldest first."""
        import json as _json
        rows = self.conn.execute(
            "SELECT ts, prices FROM snapshots WHERE target_id=? AND status=200 "
            "ORDER BY ts DESC LIMIT ?", (target_id, _clamp(limit))).fetchall()
        out = [(r[0], _json.loads(r[1] or "[]")) for r in rows]
        return list(reversed(out))

    # ----------------------------------------------------------- changes
    def add_change(self, target_id, kind, detail):
        import json as _json
        self.conn.execute(
            "INSERT INTO changes (target_id, ts, kind, detail) VALUES (?,?,?,?)",
            (target_id, time.time(), kind, _json.dumps(detail, ensure_ascii=False)))

    def recent_changes(self, since_ts=0.0, limit=50):
        import json as _json
        rows = self.conn.execute(
            "SELECT c.target_id, t.name, c.ts, c.kind, c.detail FROM changes c "
            "LEFT JOIN targets t ON t.id=c.target_id WHERE c.ts>? "
            "ORDER BY c.ts DESC LIMIT ?", (since_ts, _clamp(limit))).fetchall()
        return [{"target_id": r[0], "name": r[1], "ts": r[2], "kind": r[3],
                 "detail": _json.loads(r[4] or "{}")} for r in rows]

    def counts(self):
        t = self.conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
        s = self.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
        c = self.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0]
        return {"targets": t, "snapshots": s, "changes": c}

    def close(self):
        self.conn.close()
