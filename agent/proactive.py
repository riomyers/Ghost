#!/usr/bin/env python3
"""Proactive Speech Engine — event queue that makes Ghost speak unprompted.

Push events from any module. process_events() checks priority, quiet hours,
cooldown, then speaks the highest-priority event via ElevenLabs TTS.

SQLite-backed at ~/.config/ghost/events.db. Thread-safe.
"""

import os
import sys
import sqlite3
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Voice module lives at ~/ghost/voice/lib/
sys.path.insert(0, os.path.expanduser('~/ghost/voice/lib'))

DB_DIR = Path.home() / '.config' / 'ghost'
DB_PATH = DB_DIR / 'events.db'
EXPIRE_SECONDS = 3600  # 1 hour
COOLDOWN_SECONDS = 120  # 2 minutes between proactive speech

_lock = threading.Lock()
_last_spoke_at = 0.0  # epoch timestamp of last proactive speech


def _get_db():
    """Thread-local DB connection with WAL mode."""
    DB_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


def init_db():
    """Create events table if it doesn't exist."""
    db = _get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT NOT NULL,
        message TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 5,
        source TEXT NOT NULL DEFAULT 'system',
        created_at TEXT NOT NULL,
        spoken_at TEXT,
        expired INTEGER NOT NULL DEFAULT 0
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_events_pending ON events(expired, spoken_at)')
    db.execute('CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)')
    db.commit()
    db.close()


# Auto-init on import
init_db()


def push_event(event_type: str, message: str, priority: int = 5, source: str = 'system'):
    """Queue a proactive speech event. Thread-safe.

    Args:
        event_type: One of deploy_complete, ci_fail, calendar_reminder,
                    daily_digest, anomaly, alert, info
        message: What Ghost should say (keep it short — spoken aloud)
        priority: 1-10, 1=highest. <=3 overrides quiet hours.
        source: Which module pushed this event
    """
    priority = max(1, min(10, priority))
    now = datetime.now(timezone.utc).isoformat()

    with _lock:
        db = _get_db()
        db.execute(
            'INSERT INTO events (type, message, priority, source, created_at) VALUES (?, ?, ?, ?, ?)',
            (event_type, message, priority, source, now)
        )
        db.commit()
        db.close()


def _get_quiet_hours():
    """Return (start_hour, end_hour) from env vars. Default 22-7 CDT."""
    start = int(os.environ.get('GHOST_QUIET_START', '22'))
    end = int(os.environ.get('GHOST_QUIET_END', '7'))
    return start, end


def is_quiet_hours() -> bool:
    """Check if current local time is within quiet hours."""
    start, end = _get_quiet_hours()
    hour = datetime.now().hour

    if start < end:
        # e.g., 1am-6am
        return start <= hour < end
    else:
        # e.g., 22pm-7am (wraps midnight)
        return hour >= start or hour < end


def _cooldown_active() -> bool:
    """True if Ghost spoke proactively within the last COOLDOWN_SECONDS."""
    global _last_spoke_at
    return (time.time() - _last_spoke_at) < COOLDOWN_SECONDS


def should_speak(priority: int) -> bool:
    """Determine if Ghost should speak right now.

    Priority <= 3 overrides quiet hours (critical events).
    Cooldown always applies unless priority == 1 (emergency).
    """
    if priority == 1:
        return True  # Emergency — always speak

    if _cooldown_active():
        return False

    if priority <= 3:
        return True  # Critical — overrides quiet hours, respects cooldown

    if is_quiet_hours():
        return False

    return True


def _expire_old_events(db):
    """Mark events older than EXPIRE_SECONDS as expired."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=EXPIRE_SECONDS)).isoformat()
    db.execute(
        'UPDATE events SET expired = 1 WHERE expired = 0 AND spoken_at IS NULL AND created_at < ?',
        (cutoff,)
    )
    db.commit()


def _get_next_event(db):
    """Get highest-priority unspoken, unexpired event."""
    row = db.execute(
        '''SELECT id, type, message, priority, source, created_at
           FROM events
           WHERE spoken_at IS NULL AND expired = 0
           ORDER BY priority ASC, id ASC
           LIMIT 1'''
    ).fetchone()
    return dict(row) if row else None


def _mark_spoken(db, event_id: int):
    """Mark an event as spoken."""
    now = datetime.now(timezone.utc).isoformat()
    db.execute('UPDATE events SET spoken_at = ? WHERE id = ?', (now, event_id))
    db.commit()


def process_events():
    """Process the event queue. Call this periodically (e.g., every 30-60s).

    1. Expire old events
    2. Check for pending events
    3. Check quiet hours + cooldown
    4. Speak the highest-priority event
    """
    global _last_spoke_at

    with _lock:
        db = _get_db()
        _expire_old_events(db)
        event = _get_next_event(db)

        if not event:
            db.close()
            return

        if not should_speak(event['priority']):
            db.close()
            return

        # Speak it
        _mark_spoken(db, event['id'])
        db.close()

    # Voice call outside the lock — it blocks while audio plays
    message = event['message']
    try:
        import voice
        voice.say(message)
    except Exception:
        # Voice unavailable (no API key, no speakers, etc.)
        # Log to stderr but don't crash
        import traceback
        print(f'[proactive] Voice failed for event #{event["id"]}: {traceback.format_exc()}',
              file=sys.stderr)

    _last_spoke_at = time.time()


def get_pending() -> list:
    """Get all pending (unspoken, unexpired) events for dashboard display."""
    with _lock:
        db = _get_db()
        _expire_old_events(db)
        rows = db.execute(
            '''SELECT id, type, message, priority, source, created_at
               FROM events
               WHERE spoken_at IS NULL AND expired = 0
               ORDER BY priority ASC, id ASC'''
        ).fetchall()
        db.close()
    return [dict(r) for r in rows]


def get_recent(limit: int = 20) -> list:
    """Get recently spoken events (for history/dashboard)."""
    with _lock:
        db = _get_db()
        rows = db.execute(
            '''SELECT id, type, message, priority, source, created_at, spoken_at
               FROM events
               WHERE spoken_at IS NOT NULL
               ORDER BY spoken_at DESC
               LIMIT ?''',
            (limit,)
        ).fetchall()
        db.close()
    return [dict(r) for r in rows]


def clear_expired():
    """Delete all expired events from the database."""
    with _lock:
        db = _get_db()
        db.execute('DELETE FROM events WHERE expired = 1')
        db.commit()
        db.close()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ghost Proactive Speech Engine')
    sub = parser.add_subparsers(dest='cmd')

    push_p = sub.add_parser('push', help='Push an event')
    push_p.add_argument('type', help='Event type')
    push_p.add_argument('message', help='Message to speak')
    push_p.add_argument('--priority', type=int, default=5, help='Priority 1-10')
    push_p.add_argument('--source', default='cli', help='Event source')

    sub.add_parser('pending', help='Show pending events')
    sub.add_parser('recent', help='Show recent spoken events')
    sub.add_parser('process', help='Process one event from queue')
    sub.add_parser('quiet', help='Check if quiet hours active')

    args = parser.parse_args()

    if args.cmd == 'push':
        push_event(args.type, args.message, args.priority, args.source)
        print(f'Queued: [{args.type}] p{args.priority} — {args.message[:60]}')

    elif args.cmd == 'pending':
        events = get_pending()
        if not events:
            print('No pending events.')
        for e in events:
            print(f'  #{e["id"]} [{e["type"]}] p{e["priority"]} ({e["source"]}) — {e["message"][:80]}')

    elif args.cmd == 'recent':
        events = get_recent()
        if not events:
            print('No recent events.')
        for e in events:
            print(f'  #{e["id"]} [{e["type"]}] p{e["priority"]} spoke:{e["spoken_at"][:16]} — {e["message"][:60]}')

    elif args.cmd == 'process':
        process_events()
        print('Processed.')

    elif args.cmd == 'quiet':
        q = is_quiet_hours()
        start, end = _get_quiet_hours()
        print(f'Quiet hours: {start}:00-{end}:00 — currently {"ACTIVE" if q else "inactive"}')

    else:
        parser.print_help()
