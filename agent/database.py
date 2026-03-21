#!/usr/bin/env python3
"""Ghost Brain State Database v4 — goals, observations, tasks, action log, confidence."""

import sqlite3
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path('/home/atom/pickle-agent/data/agent_state.db')


def get_db():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    return db


def init_db():
    db = get_db()
    db.executescript('''
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'active' CHECK(status IN ('active', 'achieved', 'paused', 'abandoned')),
            priority INTEGER DEFAULT 5,
            goal_type TEXT DEFAULT 'general',
            last_thought_at TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            severity TEXT DEFAULT 'info' CHECK(severity IN ('debug', 'info', 'warning', 'critical')),
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER REFERENCES goals(id),
            description TEXT NOT NULL,
            tool_name TEXT,
            tool_params TEXT,
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'completed', 'failed', 'skipped')),
            result TEXT,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phase TEXT NOT NULL CHECK(phase IN ('sense', 'think', 'act')),
            details TEXT NOT NULL,
            tokens_used INTEGER DEFAULT 0,
            model TEXT,
            duration_sec REAL DEFAULT 0,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS token_budget (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model TEXT NOT NULL,
            tokens_used INTEGER NOT NULL,
            window_start TEXT NOT NULL,
            created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE TABLE IF NOT EXISTS confidence_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_type TEXT NOT NULL UNIQUE,
            successes INTEGER DEFAULT 0,
            failures INTEGER DEFAULT 0,
            last_updated TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
        );

        CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status);
        CREATE INDEX IF NOT EXISTS idx_obs_source ON observations(source);
        CREATE INDEX IF NOT EXISTS idx_obs_severity ON observations(severity);
        CREATE INDEX IF NOT EXISTS idx_obs_created ON observations(created_at);
        CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_id);
        CREATE INDEX IF NOT EXISTS idx_log_phase ON action_log(phase);
        CREATE INDEX IF NOT EXISTS idx_budget_window ON token_budget(window_start);
        CREATE INDEX IF NOT EXISTS idx_confidence_type ON confidence_scores(goal_type);
    ''')
    db.commit()
    db.close()
    migrate_db()


def migrate_db():
    """Add columns to existing tables — safe to call multiple times."""
    db = get_db()
    migrations = [
        ('goals', 'last_thought_at', 'TEXT'),
        ('goals', 'goal_type', 'TEXT DEFAULT "general"'),
    ]
    for table, col, col_type in migrations:
        try:
            db.execute(f'ALTER TABLE {table} ADD COLUMN {col} {col_type}')
        except sqlite3.OperationalError:
            pass  # Column already exists
    db.commit()
    db.close()


# --- Goals ---
def create_goal(description, priority=5, goal_type='general'):
    db = get_db()
    c = db.execute('INSERT INTO goals (description, priority, goal_type) VALUES (?, ?, ?)',
                   (description, priority, goal_type))
    db.commit()
    gid = c.lastrowid
    db.close()
    return gid

def get_active_goals():
    db = get_db()
    rows = db.execute('SELECT * FROM goals WHERE status = "active" ORDER BY priority DESC').fetchall()
    db.close()
    return [dict(r) for r in rows]

def get_priority_goals(limit=3):
    """Smart goal selection: score by priority * staleness * urgency.

    Score = (priority * 2) + staleness_hours(capped 24) + urgency_boost
    - High-priority goals always rank well
    - Stale goals (not thought about recently) bubble up
    - Critical observations boost system health goals
    """
    db = get_db()

    # Count critical observations in last hour
    crit_count = db.execute('''
        SELECT COUNT(*) FROM observations
        WHERE severity = 'critical'
        AND created_at > datetime('now', '-1 hour')
    ''').fetchone()[0]

    rows = db.execute('''
        SELECT *,
            (priority * 2.0) +
            MIN(COALESCE(
                (julianday('now') - julianday(last_thought_at)) * 24.0,
                48.0
            ), 24.0) as score
        FROM goals
        WHERE status = 'active'
        ORDER BY score DESC
        LIMIT ?
    ''', (limit,)).fetchall()
    db.close()

    results = [dict(r) for r in rows]

    # Urgency boost: critical observations push health/monitor goals higher
    if crit_count > 0:
        urgency = min(crit_count * 3, 15)
        health_kw = ('health', 'monitor', 'service', 'uptime', 'running')
        for g in results:
            if any(kw in g['description'].lower() for kw in health_kw):
                g['score'] = (g.get('score') or 0) + urgency
        results.sort(key=lambda x: x.get('score') or 0, reverse=True)

    return results[:limit]

def update_goal_status(goal_id, status):
    db = get_db()
    db.execute('UPDATE goals SET status = ?, updated_at = ? WHERE id = ?',
               (status, datetime.now(timezone.utc).isoformat(), goal_id))
    db.commit()
    db.close()

def update_goal_thought_time(goal_id):
    """Mark when a goal was last reasoned about."""
    db = get_db()
    db.execute('UPDATE goals SET last_thought_at = ? WHERE id = ?',
               (datetime.now(timezone.utc).isoformat(), goal_id))
    db.commit()
    db.close()

def get_all_goals():
    db = get_db()
    rows = db.execute('SELECT * FROM goals ORDER BY status, priority DESC').fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Observations ---
def record_observation(source, content, severity='info'):
    db = get_db()
    if not isinstance(content, str):
        content = json.dumps(content)
    db.execute('INSERT INTO observations (source, content, severity) VALUES (?, ?, ?)',
               (source, content, severity))
    db.commit()
    db.close()

def get_recent_observations(limit=20, source=None, min_severity=None):
    db = get_db()
    q = 'SELECT * FROM observations'
    params = []
    clauses = []
    if source:
        clauses.append('source = ?')
        params.append(source)
    if min_severity:
        sev_map = {'debug': 0, 'info': 1, 'warning': 2, 'critical': 3}
        min_val = sev_map.get(min_severity, 0)
        sevs = [s for s, v in sev_map.items() if v >= min_val]
        placeholders = ','.join('?' * len(sevs))
        clauses.append(f'severity IN ({placeholders})')
        params.extend(sevs)
    if clauses:
        q += ' WHERE ' + ' AND '.join(clauses)
    q += ' ORDER BY id DESC LIMIT ?'
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Tasks ---
def create_task(goal_id, description, tool_name=None, tool_params=None):
    db = get_db()
    params_str = json.dumps(tool_params) if tool_params else None
    c = db.execute('INSERT INTO tasks (goal_id, description, tool_name, tool_params) VALUES (?, ?, ?, ?)',
                   (goal_id, description, tool_name, params_str))
    db.commit()
    tid = c.lastrowid
    db.close()
    return tid

def get_pending_tasks(goal_id=None):
    db = get_db()
    if goal_id:
        rows = db.execute('SELECT * FROM tasks WHERE status = "pending" AND goal_id = ? ORDER BY id', (goal_id,)).fetchall()
    else:
        rows = db.execute('SELECT * FROM tasks WHERE status = "pending" ORDER BY id').fetchall()
    db.close()
    return [dict(r) for r in rows]

def get_next_task():
    db = get_db()
    row = db.execute('''SELECT t.*, g.priority as goal_priority, g.description as goal_description,
                               g.goal_type as goal_type
                        FROM tasks t
                        JOIN goals g ON t.goal_id = g.id
                        WHERE t.status = 'pending' AND g.status = 'active'
                        ORDER BY g.priority DESC, t.id ASC LIMIT 1''').fetchone()
    db.close()
    return dict(row) if row else None

def update_task(task_id, status, result=None):
    db = get_db()
    completed = datetime.now(timezone.utc).isoformat() if status in ('completed', 'failed') else None
    db.execute('UPDATE tasks SET status = ?, result = ?, completed_at = ? WHERE id = ?',
               (status, result, completed, task_id))
    db.commit()
    db.close()

def has_pending_tasks(goal_id):
    db = get_db()
    count = db.execute('SELECT COUNT(*) FROM tasks WHERE goal_id = ? AND status = "pending"', (goal_id,)).fetchone()[0]
    db.close()
    return count > 0


# --- Action Log ---
def log_action(phase, details, tokens_used=0, model=None, duration_sec=0):
    db = get_db()
    db.execute('INSERT INTO action_log (phase, details, tokens_used, model, duration_sec) VALUES (?, ?, ?, ?, ?)',
               (phase, details, tokens_used, model, duration_sec))
    db.commit()
    db.close()

def get_recent_actions(limit=20):
    db = get_db()
    rows = db.execute('SELECT * FROM action_log ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Token Budget ---
def record_token_usage(model, tokens):
    db = get_db()
    now = datetime.now(timezone.utc)
    window = now.strftime('%Y-%m-%dT%H:00:00Z')
    db.execute('INSERT INTO token_budget (model, tokens_used, window_start) VALUES (?, ?, ?)',
               (model, tokens, window))
    db.commit()
    db.close()

def get_hourly_token_usage(model='claude'):
    db = get_db()
    now = datetime.now(timezone.utc)
    window = now.strftime('%Y-%m-%dT%H:00:00Z')
    row = db.execute('SELECT COALESCE(SUM(tokens_used), 0) FROM token_budget WHERE model = ? AND window_start = ?',
                     (model, window)).fetchone()
    db.close()
    return row[0]

def get_daily_token_usage(model='claude'):
    db = get_db()
    # Use local midnight boundaries converted to UTC so the daily counter
    # resets at midnight local time, not midnight UTC.
    local_now = datetime.now().astimezone()
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_tomorrow = local_midnight + timedelta(days=1)
    utc_start = local_midnight.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:00:00Z')
    utc_end = local_tomorrow.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:00:00Z')
    row = db.execute(
        'SELECT COALESCE(SUM(tokens_used), 0) FROM token_budget WHERE model = ? AND window_start >= ? AND window_start < ?',
        (model, utc_start, utc_end)).fetchone()
    db.close()
    return row[0]
# --- Confidence Scoring ---
def record_outcome(goal_type, success):
    """Record a success or failure for a goal type."""
    db = get_db()
    col = 'successes' if success else 'failures'
    db.execute(f'''
        INSERT INTO confidence_scores (goal_type, {col})
        VALUES (?, 1)
        ON CONFLICT(goal_type) DO UPDATE SET
            {col} = {col} + 1,
            last_updated = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
    ''', (goal_type,))
    db.commit()
    db.close()

def get_confidence(goal_type):
    """Get confidence percentage (0-100) for a goal type. Default 50."""
    db = get_db()
    row = db.execute(
        'SELECT successes, failures FROM confidence_scores WHERE goal_type = ?',
        (goal_type,)
    ).fetchone()
    db.close()
    if not row:
        return 50
    total = row['successes'] + row['failures']
    if total < 3:
        return 50  # Not enough data yet
    return round((row['successes'] / total) * 100)

def get_all_confidence():
    """Get all confidence scores for dashboard display."""
    db = get_db()
    rows = db.execute('''
        SELECT goal_type, successes, failures,
               CASE WHEN (successes + failures) < 3 THEN 50
                    ELSE ROUND(CAST(successes AS REAL) / (successes + failures) * 100)
               END as confidence,
               last_updated
        FROM confidence_scores ORDER BY goal_type
    ''').fetchall()
    db.close()
    return [dict(r) for r in rows]


# --- Cleanup ---
def prune_old_observations(days=7):
    db = get_db()
    db.execute("DELETE FROM observations WHERE created_at < datetime('now', ?)", (f'-{days} days',))
    db.commit()
    db.close()

def prune_old_actions(days=14):
    db = get_db()
    db.execute("DELETE FROM action_log WHERE created_at < datetime('now', ?)", (f'-{days} days',))
    db.commit()
    db.close()


if __name__ == '__main__':
    init_db()
    print('Ghost Brain state database v4 initialized.')
    print(f'DB: {DB_PATH}')
    scores = get_all_confidence()
    if scores:
        print(f'Confidence scores: {len(scores)} types tracked')
