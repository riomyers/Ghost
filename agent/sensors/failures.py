#!/usr/bin/env python3
"""Failure sensor — feeds recent task failures into observations so the planner can self-heal."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')
import sqlite3
import database

database.init_db()
db = sqlite3.connect('/home/atom/pickle-agent/data/agent_state.db')
db.row_factory = sqlite3.Row

failures = db.execute(
    "SELECT * FROM tasks WHERE status = 'failed' ORDER BY completed_at DESC LIMIT 5"
).fetchall()

for f in failures:
    tool = f['tool_name'] or '?'
    result = f['result'] or 'no result'
    params = f['tool_params'] or '{}'
    content = f'FAILED TASK: tool={tool} result={result[:200]} params={params[:150]}'
    database.record_observation('task_failure', content, 'warning')

if failures:
    print(f'Recorded {len(failures)} failure observations')

db.close()
