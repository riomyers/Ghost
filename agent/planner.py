#!/usr/bin/env python3
"""Multi-step planner — breaks goals into sequenced task chains."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import subprocess
import database
import aor


def plan_multi_step(goal, observations, max_steps=5):
    """Ask Claude to break a goal into multiple ordered steps."""
    obs_lines = []
    for o in observations[:8]:
        obs_lines.append(f"[{o['severity']}] {o['source']}: {o['content'][:500]}")
    obs_text = '\n'.join(obs_lines) if obs_lines else '(none)'

    lessons = aor.get_failure_lessons(limit=3)
    lesson_text = '\n'.join([f"- {l['lesson']}" for l in lessons]) if lessons else '(none)'

    from datetime import datetime
    now = datetime.now().strftime('%I:%M %p %Z on %A')

    prompt = f"""You are Ghost, an autonomous agent. Break this goal into 1-{max_steps} concrete steps.

CURRENT TIME: {now}
GOAL: {goal['description']}

OBSERVATIONS:
{obs_text}

LESSONS FROM PAST FAILURES:
{lesson_text}

AVAILABLE TOOLS:
- send_notification: Push notify. Params: {{"title": "...", "message": "..."}}
- execute_bash: Run command. Params: {{"command": "..."}}

Respond with ONLY a JSON array of steps. Each step has tool_name and tool_params.
Example: [{{"tool_name": "execute_bash", "tool_params": {{"command": "uptime"}}}}, {{"tool_name": "send_notification", "tool_params": {{"title": "Status", "message": "Uptime checked"}}}}]

JSON array only, no other text:"""

    try:
        r = subprocess.run(
            ['claude', '-p', '--output-format', 'text'],
            input=prompt, capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return []

        database.record_token_usage('claude', 1)
        text = r.stdout.strip()

        # Find JSON array
        start = text.find('[')
        end = text.rfind(']') + 1
        if start >= 0 and end > start:
            steps = json.loads(text[start:end])
            if isinstance(steps, list):
                return steps[:max_steps]
    except:
        pass

    return []


def create_task_chain(goal_id, steps):
    """Create a sequence of tasks from planned steps."""
    created = 0
    for step in steps:
        tool_name = step.get('tool_name', '')
        tool_params = step.get('tool_params', {})
        if tool_name and tool_name != 'no_action':
            database.create_task(
                goal_id,
                f'{tool_name}: {json.dumps(tool_params)[:200]}',
                tool_name,
                tool_params
            )
            created += 1
    return created


if __name__ == '__main__':
    database.init_db()
    aor.init_aor()
    goals = database.get_active_goals()
    if goals:
        obs = database.get_recent_observations(limit=10)
        steps = plan_multi_step(goals[0], obs)
        print(f'Planned {len(steps)} steps for goal: {goals[0]["description"]}')
        for s in steps:
            print(f'  {s["tool_name"]}: {json.dumps(s.get("tool_params", {}))}')
