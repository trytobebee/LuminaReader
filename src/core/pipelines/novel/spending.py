"""Persistent Reader accounting. Only returned usage moves the money counter.

Each execution attempt has its own id; carried checkpoint usage is excluded from
new attempts. Historical answer/checkpoint pairs share one import key. Cache
replays and feedback/deletion never charge or erase historical spending.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
from threading import Lock
import time

FIELDS = ("api_requests", "reported_requests", "input_tokens", "output_tokens", "total_tokens",
          "cached_input_tokens", "cache_write_input_tokens", "reasoning_tokens", "unpriced_requests")
FILE = re.compile(r"([0-9a-f]{32})(?:\.checkpoint)?\.json$")
IMPORT_LOCK = Lock()


def root_for(database):
    path = Path(database).resolve()
    return path.parent.parent.parent if path.parent.parent.name == "library" else path.parent


class Spending:
    def __init__(self, database):
        self.root = root_for(database)
        self.path = self.root / "reader_spending.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS spending (
                    id TEXT PRIMARY KEY, document TEXT NOT NULL, job TEXT NOT NULL,
                    source TEXT NOT NULL, status TEXT NOT NULL, usage TEXT NOT NULL,
                    updated REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS spending_job ON spending(document,job,source);
                CREATE TABLE IF NOT EXISTS spend_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                    tokens INTEGER NOT NULL, cost REAL NOT NULL, source TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (path TEXT PRIMARY KEY, stamp TEXT NOT NULL);
            """)

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def update(self, identity, document, job, usage, status="running", source="live"):
        usage = dict(usage)
        usage.setdefault("reported_requests", usage.get("api_requests", 0) if usage.get("usage_complete") else 0)
        for key in FIELDS:
            usage[key] = max(0, int(usage.get(key) or 0))
        if source == "live":
            usage["_pid"] = os.getpid()
        usage["known_cost_usd"] = max(0, float(usage.get("known_cost_usd", usage.get("estimated_cost_usd")) or 0))
        now = time.time()
        with closing(self.connect()) as conn, conn:
            # A write transaction serializes concurrent cumulative snapshots.
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute("SELECT usage FROM spending WHERE id=?", (identity,)).fetchone()
            old = json.loads(previous[0]) if previous else {}
            # An older poll/checkpoint must never subtract a later snapshot.
            for key in FIELDS:
                usage[key] = max(usage[key], old.get(key, 0))
            usage["known_cost_usd"] = max(usage["known_cost_usd"], old.get("known_cost_usd", 0))
            delta_tokens = usage["total_tokens"] - old.get("total_tokens", 0)
            delta_cost = round(usage["known_cost_usd"] - old.get("known_cost_usd", 0), 6)
            conn.execute("INSERT OR REPLACE INTO spending VALUES (?,?,?,?,?,?,?)", (
                identity, str(Path(document).resolve()), job, source, status,
                json.dumps(usage, ensure_ascii=False), now))
            if delta_tokens > 0 or delta_cost > 0:
                conn.execute("INSERT INTO spend_events(timestamp,tokens,cost,source) VALUES (?,?,?,?)",
                             (now, delta_tokens, delta_cost, source))

    def import_history(self):
        """Import saved Reader usage only, never benchmarks or extraction caches."""
        with IMPORT_LOCK:
            parents = [self.root, *sorted((self.root / "library").glob("*/"))]
            for parent in parents:
                directories = [parent / "reader_qa", *sorted((parent / "reader_harness").glob("*/"))]
                for directory in directories:
                    for file in sorted(directory.glob("*.json")):
                        match = FILE.fullmatch(file.name)
                        if not match:
                            continue
                        try:
                            stat = file.stat()
                        except OSError:
                            continue
                        stamp = f"v2:{stat.st_mtime_ns}:{stat.st_size}"
                        with closing(self.connect()) as conn:
                            imported = conn.execute("SELECT stamp FROM imports WHERE path=?", (str(file),)).fetchone()
                            live = conn.execute("SELECT 1 FROM spending WHERE source='live' AND job=? AND document LIKE ? LIMIT 1",
                                                (match[1], str(parent.resolve()) + "/%" )).fetchone()
                        if imported and imported[0] == stamp:
                            continue
                        try:
                            value = json.loads(file.read_text(encoding="utf-8"))
                        except (ValueError, OSError):
                            continue
                        if not isinstance(value, dict):
                            continue
                        usage = value.get("usage")
                        # checkpoint snapshots contain usage but no answer; both
                        # resolve to the same directory + job identity.
                        if isinstance(usage, dict) and not value.get("cache_hit") and not live:
                            document = parent / ("book.db" if parent != self.root else "reader_novel.db")
                            normalized = dict(usage)
                            normalized["_report_count_unknown"] = "reported_requests" not in usage and not usage.get("usage_complete", False)
                            normalized.setdefault("reported_requests", normalized.get("api_requests", 0) if normalized.get("usage_complete") else 0)
                            if normalized.get("estimated_cost_usd") is None and normalized.get("api_requests") and normalized.get("usage_complete"):
                                normalized.setdefault("unpriced_requests", normalized["api_requests"])
                            self.update("history:" + str(directory / match[1]), document, match[1], normalized,
                                        status="historical", source="history")
                        with closing(self.connect()) as conn, conn:
                            conn.execute("INSERT OR REPLACE INTO imports VALUES (?,?)", (str(file), stamp))

    def snapshot(self):
        self.import_history()
        result = {key: 0 for key in FIELDS}
        result.update(known_cost_usd=0.0, active_requests=0, missing_usage_requests=0,
                      historical_records=0, live_runs=0, active_runs=0, interrupted_runs=0,
                      incomplete_history_records=0)
        with closing(self.connect()) as conn:
            for row in conn.execute("SELECT usage,status,source FROM spending"):
                usage = json.loads(row["usage"])
                for key in FIELDS:
                    result[key] += usage.get(key, 0)
                result["known_cost_usd"] += usage.get("known_cost_usd", 0)
                pending = max(0, usage.get("api_requests", 0) - usage.get("reported_requests", 0))
                running = row["status"] == "running"
                if running and usage.get("_pid"):
                    try:
                        os.kill(usage["_pid"], 0)
                    except ProcessLookupError:
                        running = False
                        result["interrupted_runs"] += 1
                    except PermissionError:
                        pass
                if running:
                    result["active_runs"] += 1
                    result["active_requests"] += pending
                elif usage.get("_report_count_unknown"):
                    result["incomplete_history_records"] += 1
                else:
                    result["missing_usage_requests"] += pending
                result["historical_records" if row["source"] == "history" else "live_runs"] += 1
            events = [dict(row) for row in conn.execute("SELECT * FROM spend_events ORDER BY id DESC LIMIT 18")][::-1]
        result["known_cost_usd"] = round(result["known_cost_usd"], 6)
        result.update(currency="USD", scope="reader_all_documents", events=events,
                      last_event_id=events[-1]["id"] if events else 0,
                      history_complete=False, updated_at=time.time())
        return result
