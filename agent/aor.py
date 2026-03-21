#!/usr/bin/env python3
"""AOR v2 — Action-Outcome-Reflection with SOP refinement.

When Ghost fails at something, it reflects. When it fails REPEATEDLY,
it writes a playbook (SOP) so it never makes the same mistake.
"""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import sqlite3
from pathlib import Path
import database
import nexus_client

DB_PATH = Path('/home/atom/pickle-agent/data/agent_state.db')
SOP_DIR = Path('/home/atom/pickle-agent/sops')
FAILURE_THRESHOLD = 3  # failures before generating SOP


def init_aor():
    SOP_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(str(DB_PATH))
    db.execute('''CREATE TABLE IF NOT EXISTS reflections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER,
        command TEXT,
        exit_code INTEGER,
        output TEXT,
        reflection TEXT,
        lesson TEXT,
        category TEXT DEFAULT 'general',
        created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    )''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_reflections_lesson ON reflections(lesson)')
    # Migration: add category column if missing
    try:
        db.execute('ALTER TABLE reflections ADD COLUMN category TEXT DEFAULT "general"')
    except sqlite3.OperationalError:
        pass
    db.commit()
    db.close()


def _categorize_command(command):
    """Classify a command into a category for SOP grouping."""
    cmd = command.lower()
    if any(kw in cmd for kw in ('git', 'gh ', 'pull', 'push', 'clone')):
        return 'git-operations'
    if any(kw in cmd for kw in ('npm', 'pip', 'install', 'build')):
        return 'package-management'
    if any(kw in cmd for kw in ('curl', 'wget', 'http', 'api')):
        return 'network-requests'
    if any(kw in cmd for kw in ('systemctl', 'service', 'restart')):
        return 'service-management'
    if any(kw in cmd for kw in ('test', 'pytest', 'jest')):
        return 'testing'
    if any(kw in cmd for kw in ('deploy', 'release', 'ship')):
        return 'deployment'
    return 'general'


def reflect_on_action(task_id, command, exit_code, output):
    """Reflect via Nexus (not Claude CLI) and check for SOP trigger."""
    prompt = f"""You executed a command as an autonomous agent. Reflect briefly.

COMMAND: {command[:200]}
EXIT CODE: {exit_code}
OUTPUT: {output[:300]}

JSON only:
{{"reflection": "what happened and why (1 sentence)", "lesson": "what to do differently next time (1 sentence)"}}"""

    reflection = ''
    lesson = ''

    try:
        result, _, _ = nexus_client.chat(prompt, model='haiku', timeout=20)
        database.record_token_usage('nexus', 1)
        start = result.find('{')
        end = result.rfind('}') + 1
        if start >= 0 and end > start:
            data = json.loads(result[start:end])
            reflection = data.get('reflection', '')
            lesson = data.get('lesson', '')
    except Exception:
        reflection = f'Exit {exit_code}'
        lesson = 'Reflection failed'

    category = _categorize_command(command)
    save_reflection(task_id, command, exit_code, output, reflection, lesson, category)

    # SOP trigger: check if this category has hit the failure threshold
    if exit_code != 0:
        _check_sop_trigger(category)

    return {'reflection': reflection, 'lesson': lesson}


def save_reflection(task_id, command, exit_code, output, reflection, lesson, category='general'):
    db = sqlite3.connect(str(DB_PATH))
    db.execute(
        'INSERT INTO reflections (task_id, command, exit_code, output, reflection, lesson, category) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (task_id, command[:500], exit_code, output[:500], reflection[:300], lesson[:300], category)
    )
    db.commit()
    db.close()


def _check_sop_trigger(category):
    """If enough failures in a category, generate or update an SOP."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row

    # Count recent failures in this category
    recent_failures = db.execute('''
        SELECT command, output, lesson FROM reflections
        WHERE exit_code != 0 AND category = ?
        ORDER BY id DESC LIMIT 10
    ''', (category,)).fetchall()
    db.close()

    if len(recent_failures) < FAILURE_THRESHOLD:
        return

    failures = [dict(r) for r in recent_failures]
    sop_path = SOP_DIR / f'{category}.md'

    # Load existing SOP if any
    existing_sop = ''
    if sop_path.exists():
        existing_sop = sop_path.read_text()

    # Ask Nexus to generate/update the SOP
    failure_text = '\n'.join([
        f'- CMD: {f["command"][:80]} → LESSON: {f["lesson"]}'
        for f in failures[:5]
    ])

    prompt = f"""You are Ghost, an autonomous agent that learns from failures.
Category "{category}" has {len(failures)} recent failures:

{failure_text}

{"EXISTING SOP:\n" + existing_sop[:500] if existing_sop else "No existing SOP."}

Write a concise SOP (Standard Operating Procedure) for this category.
Format:
# SOP: {category}
## When to use
(1 sentence)
## Steps
1. ...
2. ...
## Common pitfalls
- ...
## Learned from failures
- ...

Keep it under 300 words. Be specific and actionable."""

    try:
        sop_content, _, _ = nexus_client.chat(prompt, model='haiku', timeout=30)
        database.record_token_usage('nexus', 1)

        # Validate it looks like a reasonable SOP
        if len(sop_content) > 50 and '#' in sop_content:
            sop_path.write_text(sop_content)
            database.log_action('act',
                f'SOP {"updated" if existing_sop else "created"}: {category} ({len(failures)} failures)')
            database.record_observation('aor',
                f'SOP {"updated" if existing_sop else "generated"} for {category} after {len(failures)} failures',
                'info')
    except Exception as e:
        database.log_action('act', f'SOP generation failed for {category}: {e}')


def get_relevant_lessons(context, limit=5):
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        'SELECT lesson, command, reflection FROM reflections ORDER BY id DESC LIMIT ?',
        (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_failure_lessons(limit=5):
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    rows = db.execute(
        'SELECT lesson, command, reflection FROM reflections WHERE exit_code != 0 ORDER BY id DESC LIMIT ?',
        (limit,)
    ).fetchall()
    db.close()
    return [dict(r) for r in rows]


def get_sops():
    """List all SOPs for dashboard display."""
    sops = []
    if SOP_DIR.exists():
        for sop in sorted(SOP_DIR.glob('*.md')):
            content = sop.read_text()
            sops.append({
                'name': sop.stem,
                'size': len(content),
                'preview': content[:200]
            })
    return sops


if __name__ == '__main__':
    init_aor()
    sops = get_sops()
    print(f'AOR v2 initialized. {len(sops)} SOPs in {SOP_DIR}')
    for s in sops:
        print(f'  {s["name"]}: {s["size"]} chars')
