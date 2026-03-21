"""
T21: Offline request queue for Ghost agent.

SQLite-backed FIFO queue that stores failed HTTP requests when offline,
then replays them (drain) when connectivity is restored.

Features:
- SHA256 content_hash dedup (url + body)
- Max 500 entries, 24h TTL auto-prune
- Thread-safe (all operations use a shared lock)
- Stdlib only (sqlite3, urllib, threading, hashlib)
"""

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

DB_DIR = os.path.expanduser("~/.config/ghost")
DB_PATH = os.path.join(DB_DIR, "offline_queue.db")
MAX_ENTRIES = 500
TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _content_hash(url: str, body: str | None) -> str:
    """SHA256 of url + body for dedup."""
    raw = url + (body or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class OfflineQueue:
    """SQLite-backed offline request queue.

    Usage:
        q = OfflineQueue()
        q.enqueue("https://example.com/api", "POST", {"Content-Type": "application/json"}, '{"k":"v"}', "sensor")
        count = q.pending_count()
        results = q.drain()  # replays all queued requests
    """

    def __init__(self, db_path: str = DB_PATH):
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        """Create the queue table if it doesn't exist."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        with self._lock:
            conn = sqlite3.connect(self._db_path)
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS queue (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        url TEXT NOT NULL,
                        method TEXT NOT NULL DEFAULT 'GET',
                        headers TEXT,
                        body TEXT,
                        source TEXT,
                        content_hash TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        UNIQUE(content_hash)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_created ON queue(created_at)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_hash ON queue(content_hash)")
                conn.commit()
            finally:
                conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a new connection (must be called with _lock held)."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _prune(self, conn: sqlite3.Connection) -> int:
        """Remove expired entries (>24h) and enforce max 500. Returns count removed."""
        now = time.time()
        cutoff = now - TTL_SECONDS

        # Remove expired
        cursor = conn.execute("DELETE FROM queue WHERE created_at < ?", (cutoff,))
        expired = cursor.rowcount

        # Enforce max entries — keep newest MAX_ENTRIES
        cursor = conn.execute(
            "DELETE FROM queue WHERE id NOT IN (SELECT id FROM queue ORDER BY created_at DESC LIMIT ?)",
            (MAX_ENTRIES,),
        )
        overflow = cursor.rowcount

        total = expired + overflow
        if total > 0:
            conn.commit()
            logger.debug("Pruned %d entries (expired=%d, overflow=%d)", total, expired, overflow)
        return total

    def enqueue(
        self,
        url: str,
        method: str = "GET",
        headers: dict | None = None,
        body: str | None = None,
        source: str = "unknown",
    ) -> bool:
        """Store a failed request in the queue.

        Returns True if enqueued, False if duplicate (same content_hash).
        """
        ch = _content_hash(url, body)
        headers_json = json.dumps(headers) if headers else None
        now = time.time()

        with self._lock:
            conn = self._get_conn()
            try:
                self._prune(conn)
                try:
                    conn.execute(
                        "INSERT INTO queue (url, method, headers, body, source, content_hash, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (url, method.upper(), headers_json, body, source, ch, now),
                    )
                    conn.commit()
                    logger.info("Enqueued request: %s %s (source=%s, hash=%s)", method, url, source, ch[:12])
                    return True
                except sqlite3.IntegrityError:
                    logger.debug("Duplicate request skipped: %s %s (hash=%s)", method, url, ch[:12])
                    return False
            finally:
                conn.close()

    def pending_count(self) -> int:
        """Return the number of queued items (after pruning expired)."""
        with self._lock:
            conn = self._get_conn()
            try:
                self._prune(conn)
                row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
                return row[0]
            finally:
                conn.close()

    def drain(self) -> dict:
        """Replay all queued requests in FIFO order.

        Removes entries that succeed (2xx/3xx). Keeps entries that fail.
        Returns {"replayed": int, "failed": int, "remaining": int}.
        """
        # Fetch all entries under lock, then release lock for HTTP calls
        with self._lock:
            conn = self._get_conn()
            try:
                self._prune(conn)
                rows = conn.execute(
                    "SELECT id, url, method, headers, body, source, content_hash "
                    "FROM queue ORDER BY created_at ASC"
                ).fetchall()
            finally:
                conn.close()

        if not rows:
            return {"replayed": 0, "failed": 0, "remaining": 0}

        succeeded_ids: list[int] = []
        failed_count = 0

        for row in rows:
            row_id = row["id"]
            url = row["url"]
            method = row["method"]
            headers_raw = row["headers"]
            body = row["body"]
            source = row["source"]

            headers = json.loads(headers_raw) if headers_raw else {}

            try:
                data = body.encode("utf-8") if body else None
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if 200 <= resp.status < 400:
                        succeeded_ids.append(row_id)
                        logger.info("Drained OK: %s %s (source=%s)", method, url, source)
                    else:
                        failed_count += 1
                        logger.warning("Drain failed (status=%d): %s %s", resp.status, method, url)
            except Exception as exc:
                failed_count += 1
                logger.warning("Drain failed (%s): %s %s", exc, method, url)

        # Remove succeeded entries under lock
        if succeeded_ids:
            with self._lock:
                conn = self._get_conn()
                try:
                    placeholders = ",".join("?" for _ in succeeded_ids)
                    conn.execute(f"DELETE FROM queue WHERE id IN ({placeholders})", succeeded_ids)
                    conn.commit()
                finally:
                    conn.close()

        remaining = len(rows) - len(succeeded_ids)
        result = {"replayed": len(succeeded_ids), "failed": failed_count, "remaining": remaining}
        logger.info("Drain complete: %s", result)
        return result

    def clear(self) -> int:
        """Remove all entries. Returns count removed."""
        with self._lock:
            conn = self._get_conn()
            try:
                cursor = conn.execute("DELETE FROM queue")
                conn.commit()
                count = cursor.rowcount
                logger.info("Cleared %d entries from offline queue", count)
                return count
            finally:
                conn.close()

    def list_pending(self) -> list[dict]:
        """Return all pending entries as dicts (for debugging/dashboard)."""
        with self._lock:
            conn = self._get_conn()
            try:
                self._prune(conn)
                rows = conn.execute(
                    "SELECT id, url, method, source, content_hash, created_at "
                    "FROM queue ORDER BY created_at ASC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()


# Module-level singleton
_queue: OfflineQueue | None = None
_queue_lock = threading.Lock()


def get_queue() -> OfflineQueue:
    """Get or create the singleton OfflineQueue instance."""
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = OfflineQueue()
        return _queue


def enqueue(url: str, method: str = "GET", headers: dict | None = None,
            body: str | None = None, source: str = "unknown") -> bool:
    """Convenience: enqueue on the singleton."""
    return get_queue().enqueue(url, method, headers, body, source)


def drain() -> dict:
    """Convenience: drain the singleton queue."""
    return get_queue().drain()


def pending_count() -> int:
    """Convenience: pending count from the singleton."""
    return get_queue().pending_count()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    q = OfflineQueue()
    print(f"Pending: {q.pending_count()}")

    # Demo enqueue
    q.enqueue("https://httpbin.org/post", "POST", {"Content-Type": "application/json"}, '{"test": true}', "demo")
    q.enqueue("https://httpbin.org/get", "GET", None, None, "demo")
    # Duplicate should be skipped
    q.enqueue("https://httpbin.org/post", "POST", {"Content-Type": "application/json"}, '{"test": true}', "demo")

    print(f"Pending after enqueue: {q.pending_count()}")

    for entry in q.list_pending():
        print(f"  [{entry['id']}] {entry['method']} {entry['url']} (source={entry['source']})")

    result = q.drain()
    print(f"Drain result: {result}")
    print(f"Pending after drain: {q.pending_count()}")
