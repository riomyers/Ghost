#!/usr/bin/env python3
"""Ghost Brain Kernel v4 — smart goal routing, confidence gating, no hard rate limit."""

import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import time
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path

import database
import planner
import aor
import nexus_client

CYCLE_INTERVAL = 60
THINK_INTERVAL = 10
THINK_INTERVAL_DEFAULT = 10
THINK_INTERVAL_THROTTLED = 30
DAILY_CALL_LIMIT = 150
NO_ACTION_COUNTS = {}  # goal_id -> consecutive no_action count
NO_ACTION_SKIP_THRESHOLD = 3  # skip goal after 3 consecutive no_actions
NO_ACTION_SKIP_CYCLES = 60  # skip for 60 cycles (~1 hour)
GOAL_SKIP_UNTIL = {}  # goal_id -> cycle number to resume
DIAG_SUPPRESSED = {}  # issue_key -> timestamp, don't re-notify for 1hr after auto-fix
MAX_GOALS_PER_CYCLE = 3
NTFY_TOPIC = 'https://ntfy.sh/ghost-pickle-rick'
LOG_DIR = Path('/home/atom/pickle-agent/logs')
LOG_DIR.mkdir(exist_ok=True)


def log(msg, level='INFO'):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {msg}'
    print(line, flush=True)
    log_file = LOG_DIR / f'{datetime.now().strftime("%Y-%m-%d")}.log'
    with open(log_file, 'a') as f:
        f.write(line + '\n')


LAST_NOTIFY = {}
# ntfy priority levels: 1=min, 2=low, 3=default, 4=high, 5=urgent
# Only 'urgent' (5) triggers aggressive phone behavior (vibrate, text forwarding)
# Use 'urgent' for critical issues only — warnings stay as quiet push notifications
NTFY_PRIORITIES = {'min': '1', 'low': '2', 'default': '3', 'high': '4', 'urgent': '5'}

def notify(title, message, priority='default'):
    import hashlib
    key = hashlib.md5((title + message[:100]).encode()).hexdigest()
    now = time.time()
    if key in LAST_NOTIFY and now - LAST_NOTIFY[key] < 600:
        return True
    LAST_NOTIFY[key] = now
    pri = NTFY_PRIORITIES.get(priority, '3')
    try:
        subprocess.run(
            ['curl', '-s', '-d', message[:4000],
             '-H', f'Title: {title}',
             '-H', f'Priority: {pri}',
             NTFY_TOPIC],
            capture_output=True, timeout=15
        )
        log(f'NOTIFY [{priority}]: {title}')
        return True
    except Exception as e:
        log(f'NOTIFY FAILED: {e}', 'ERROR')
        return False


SENSOR_COOLDOWN = {}  # track sensors that time out

def run_sensors():
    sensors_dir = Path('/home/atom/pickle-agent/src/sensors')
    if not sensors_dir.exists():
        return
    now = time.time()
    for sensor_file in sensors_dir.glob('*.py'):
        if sensor_file.name == '__init__.py':
            continue
        # Skip sensors in cooldown (timed out recently)
        last_fail = SENSOR_COOLDOWN.get(sensor_file.name, 0)
        if now - last_fail < 300:  # 5 min cooldown after timeout
            continue
        try:
            subprocess.run(
                [sys.executable, str(sensor_file)],
                capture_output=True, text=True, timeout=20,
                cwd='/home/atom/pickle-agent'
            )
        except subprocess.TimeoutExpired:
            SENSOR_COOLDOWN[sensor_file.name] = now
            log(f'Sensor {sensor_file.name} timed out — cooling down 5min', 'WARN')
        except Exception as e:
            log(f'Sensor {sensor_file.name}: {e}', 'WARN')
    database.log_action('sense', 'Sensors complete')


TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_name": {
            "type": "string",
            "enum": ["send_notification", "execute_bash", "review_pr",
                     "propose_code_change", "research_web", "no_action"]
        },
        "tool_params": {"type": "object"}
    },
    "required": ["tool_name", "tool_params"]
}

def think_with_nexus(prompt, model="haiku", system_prompt=None, use_schema=False,
                     priority="normal"):
    """Reason via Nexus AI Gateway."""
    try:
        result, used_model, provider = nexus_client.chat(
            prompt, model=model, system_prompt=system_prompt,
            json_schema=TOOL_SCHEMA if use_schema else None, timeout=60,
            priority=priority)
        database.record_token_usage('nexus', 1)
        log(f'Nexus: model={used_model} provider={provider}')
        return result
    except Exception as e:
        return f'nexus error: {e}'


# --- Goal Classification ---

GOAL_TYPE_KEYWORDS = {
    'system_health': ('health', 'monitor', 'uptime', 'service', 'running', 'disk', 'memory', 'cpu', 'load'),
    'code_review': ('pr', 'review', 'pull request', 'code review'),
    'security': ('vuln', 'security', 'audit', 'cve', 'dependency'),
    'testing': ('test', 'coverage', 'spec', 'unit test'),
    'communication': ('text', 'message', 'reply', 'respond', 'notify'),
    'research': ('research', 'investigate', 'look into', 'find out'),
    'deployment': ('deploy', 'release', 'ship', 'push'),
}

def classify_goal_type(description):
    """Classify a goal into a type for confidence tracking."""
    desc = description.lower()
    for goal_type, keywords in GOAL_TYPE_KEYWORDS.items():
        if any(kw in desc for kw in keywords):
            return goal_type
    return 'general'


# --- Prompt Building ---

AVAILABLE_TOOLS = """TOOLS (pick exactly one):
- send_notification: Push notify Rio. Params: {"title": "...", "message": "..."}
- execute_bash: Run a command safely. Params: {"command": "..."}
- review_pr: Review a GitHub PR. Params: {"repo": "owner/name", "number": 123}
- propose_code_change: Open a PR with code changes. Params: {"repo": "owner/name", "branch": "fix/name", "description": "what and why"}
- research_web: Search the web and summarize findings. Params: {"query": "..."}
- no_action: Nothing to do right now. Params: {}"""

def build_prompt(goal, observations, confidence):
    obs_lines = []
    for o in observations[:10]:
        obs_lines.append(f"[{o['severity']}] {o['source']}: {o['content'][:500]}")
    obs_text = '\n'.join(obs_lines) if obs_lines else '(none)'

    lessons = aor.get_failure_lessons(limit=3)
    lesson_text = '\n'.join([f"- LESSON: {l['lesson']} (from: {l['command'][:200]})" for l in lessons]) if lessons else '(none)'

    # Load relevant SOP
    sop_text = ""
    sop_dir = Path("/home/atom/pickle-agent/sops")
    if sop_dir.exists():
        for sop in sop_dir.glob("*.md"):
            if any(kw in goal["description"].lower() for kw in sop.stem.replace("-", " ").split()):
                sop_text = f"\nPLAYBOOK ({sop.name}):\n{sop.read_text()[:1500]}\n"
                break

    now = datetime.now().strftime('%I:%M %p %Z on %A')
    goal_type = classify_goal_type(goal['description'])

    confidence_note = ""
    if confidence >= 80:
        confidence_note = "HIGH CONFIDENCE — you may act autonomously."
    elif confidence < 50:
        confidence_note = "LOW CONFIDENCE — prefer send_notification over direct action. Ask before doing."

    system = "You are a tool-selection assistant for a server monitoring system. Given a goal and observations, select the best tool and parameters. Always respond with ONLY a JSON object. No markdown, no explanation, no commentary — just the JSON object. IMPORTANT: Never send introductions, greetings, or 'hello' messages via send_notification. Only notify about real problems or actionable information. If a goal seems already done or redundant, pick no_action."

    user = f"""Select the best tool for this goal.

Time: {now}
Goal: {goal['description']}
Type: {goal_type} (confidence: {confidence}%)
{confidence_note}

Observations:
{obs_text}

Lessons:
{lesson_text}
{sop_text}
Available tools:
- send_notification: Push notification. ONLY for failures or issues requiring attention. NEVER notify when healthy/normal. Params: {{"title": "...", "message": "..."}}
- execute_bash: Run a standard Linux shell command. ONLY use commands that exist on Ubuntu (systemctl, apt, curl, ping, df, free, uptime, journalctl, docker, git, etc). NEVER invent commands — if you are unsure a command exists, use no_action instead. Params: {{"command": "..."}}
- review_pr: Review a GitHub PR. Params: {{"repo": "owner/name", "number": 123}}
- propose_code_change: Open a PR. Params: {{"repo": "owner/name", "branch": "fix/name", "description": "..."}}
- research_web: Web search. Params: {{"query": "..."}}
- no_action: Nothing needed right now. Params: {{}}

Return JSON: {{"tool_name": "...", "tool_params": {{...}}}}"""

    return system, user


# --- Smart Goal Routing (T10) ---

def run_planner():
    """Think about top-priority goals only — max {MAX_GOALS_PER_CYCLE} per cycle."""
    goals = database.get_priority_goals(limit=MAX_GOALS_PER_CYCLE)
    if not goals:
        return

    hourly = database.get_hourly_token_usage('nexus')
    daily = database.get_daily_token_usage('nexus')
    log(f'Budget: hourly={hourly} daily={daily}/{DAILY_CALL_LIMIT} goals_selected={len(goals)}')

    # Hard block background thinking when over daily limit
    if daily >= DAILY_CALL_LIMIT:
        log(f'DAILY LIMIT HIT ({daily}/{DAILY_CALL_LIMIT}) — skipping think cycle', 'WARN')
        return

    current_cycle = int(time.time() / CYCLE_INTERVAL)  # approximate cycle number

    for goal in goals:
        if database.has_pending_tasks(goal['id']):
            continue

        # Skip goals that keep returning no_action
        skip_until = GOAL_SKIP_UNTIL.get(goal['id'], 0)
        if current_cycle < skip_until:
            continue

        goal_type = classify_goal_type(goal['description'])
        confidence = database.get_confidence(goal_type)

        observations = database.get_recent_observations(limit=20)
        system, user_prompt = build_prompt(goal, observations, confidence)

        start = time.time()
        response = think_with_nexus(user_prompt, system_prompt=system, use_schema=True)
        duration = round(time.time() - start, 1)

        # Mark this goal as recently thought about
        database.update_goal_thought_time(goal['id'])

        score = goal.get('score', 0)
        log(f'Think goal={goal["id"]} type={goal_type} conf={confidence}% score={score:.1f}: {response[:200]}')
        database.log_action('think',
            f'goal={goal["id"]} type={goal_type} conf={confidence} score={score:.1f} response={response}',
            model='nexus', duration_sec=duration)

        try:
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            if start_idx >= 0 and end_idx > start_idx:
                plan = json.loads(response[start_idx:end_idx])
                tool_name = plan.get('tool_name', 'no_action')
                tool_params = plan.get('tool_params', {})

                if tool_name == 'no_action' or not tool_name:
                    # Track consecutive no_actions — skip goal if it keeps idling
                    NO_ACTION_COUNTS[goal['id']] = NO_ACTION_COUNTS.get(goal['id'], 0) + 1
                    if NO_ACTION_COUNTS[goal['id']] >= NO_ACTION_SKIP_THRESHOLD:
                        GOAL_SKIP_UNTIL[goal['id']] = current_cycle + NO_ACTION_SKIP_CYCLES
                        NO_ACTION_COUNTS[goal['id']] = 0
                        log(f'Goal {goal["id"]} returned no_action {NO_ACTION_SKIP_THRESHOLD}x — sleeping 1hr')
                else:
                    # Real action — reset no_action counter
                    NO_ACTION_COUNTS[goal['id']] = 0

                    # Confidence gate: low confidence = notify first
                    if confidence < 50 and tool_name not in ('send_notification',):
                        log(f'LOW CONFIDENCE ({confidence}%) — gating with notification')
                        database.create_task(
                            goal['id'],
                            f'[CONFIRM] Low confidence ({confidence}%): {tool_name}',
                            'send_notification',
                            {'title': f'Ghost: Confirm? ({confidence}% conf)',
                             'message': f'Goal: {goal["description"]}\nAction: {tool_name}\nParams: {json.dumps(tool_params)[:500]}',
                             'priority': 'default'}
                        )

                    database.create_task(
                        goal['id'],
                        f'{tool_name}: {json.dumps(tool_params)[:200]}',
                        tool_name,
                        tool_params
                    )
                    log(f'Planned: {tool_name} for goal {goal["id"]} (conf={confidence}%)')
        except (json.JSONDecodeError, KeyError) as e:
            log(f'Parse failed: {e}', 'WARN')


# --- Task Execution ---

def execute_tasks():
    task = database.get_next_task()
    if not task:
        return

    database.update_task(task['id'], 'running')
    tool = task.get('tool_name', '')
    params = json.loads(task['tool_params']) if task.get('tool_params') else {}
    goal_type = task.get('goal_type', 'general')

    log(f'ACT: {tool} (task {task["id"]}, goal_type={goal_type})')

    success = False
    try:
        if tool == 'send_notification':
            title = params.get('title', 'Ghost')
            message = params.get('message', '')
            pri = params.get('priority', 'default')
            # Suppress healthy/no-action notifications — only alert on problems
            msg_lower = (title + ' ' + message).lower()
            if any(w in msg_lower for w in ('healthy', 'no action', 'no issues', 'all clear', 'no intervention')):
                log(f'NOTIFY SUPPRESSED (healthy): {title}')
                success = True
                result = 'Suppressed (healthy status)'
                database.update_task(task['id'], 'completed', result)
            else:
                success = notify(title, message, priority=pri)
                result = 'Sent' if success else 'Failed'
                database.update_task(task['id'], 'completed' if success else 'failed', result)

        elif tool == 'execute_bash':
            cmd = params.get('command', '')
            blocked = ['rm -rf /', 'mkfs', 'dd if=', ':(){', '> /dev/sd',
                       'chmod -R 777 /', 'shutdown', 'reboot', 'halt', 'poweroff',
                       'curl.*|.*sh', 'wget.*|.*sh']
            if any(b in cmd.lower() for b in blocked):
                result = f'BLOCKED: {cmd[:100]}'
                database.update_task(task['id'], 'failed', result)
                notify('Ghost: Blocked', f'Refused: {cmd[:200]}', priority='high')
            else:
                r = subprocess.run(['bash', '-c', cmd],
                    capture_output=True, text=True, timeout=30)
                output = (r.stdout + r.stderr).strip()[:2000]
                success = r.returncode == 0
                status = 'completed' if success else 'failed'
                database.update_task(task['id'], status, output)

                # Only reflect on failures — reflecting on success wastes API calls
                if r.returncode != 0:
                    aor.reflect_on_action(task['id'], cmd, r.returncode, output)
                    # Don't notify on "command not found" (exit 127) — that's the AI
                    # hallucinating commands, not a real system problem
                    if r.returncode != 127 and 'command not found' not in output:
                        notify('Ghost: Failed', f'$ {cmd}\n{output}', priority='low')

        elif tool == 'review_pr':
            repo = params.get('repo', '')
            number = params.get('number', 0)
            if repo and number:
                from pr_reviewer import review_pr as do_review
                result = do_review(repo, int(number))
                success = True
                database.update_task(task['id'], 'completed', result)
                notify('Ghost: PR Reviewed', f'{repo}#{number}: {result}')
                aor.reflect_on_action(task['id'], f'review_pr {repo}#{number}', 0, result)
            else:
                database.update_task(task['id'], 'failed', 'Missing repo or number')

        elif tool == 'propose_code_change':
            try:
                from actuators.code_commit import propose_code_change
                result = propose_code_change(params)
                success = 'error' not in result.lower()
                database.update_task(task['id'], 'completed' if success else 'failed', result)
                if success:
                    notify('Ghost: PR Opened', result)
            except ImportError:
                database.update_task(task['id'], 'failed', 'code_commit actuator not installed')
            except Exception as e:
                database.update_task(task['id'], 'failed', str(e)[:200])

        elif tool == 'research_web':
            try:
                from actuators.research import research
                result = research(params.get('query', ''))
                success = bool(result)
                database.update_task(task['id'], 'completed' if success else 'failed',
                                   result[:2000] if result else 'No results')
            except ImportError:
                database.update_task(task['id'], 'failed', 'research actuator not installed')
            except Exception as e:
                database.update_task(task['id'], 'failed', str(e)[:200])

        elif tool == 'no_action':
            database.update_task(task['id'], 'skipped', 'No action needed')
            success = True

        else:
            database.update_task(task['id'], 'failed', f'Unknown tool: {tool}')

    except subprocess.TimeoutExpired:
        database.update_task(task['id'], 'failed', 'Timed out')
        notify('Ghost: Timeout', f'Timed out: {params.get("command", "?")}', priority='low')
    except Exception as e:
        database.update_task(task['id'], 'failed', str(e)[:200])

    # Record confidence outcome
    database.record_outcome(goal_type, success)

    database.log_action('act', f'task={task["id"]} tool={tool} success={success}')


def self_diagnose():
    """Self-diagnosis — Ghost checks its own health every 10 cycles."""
    issues = []

    # Check Nexus connectivity via recent action log (don't burn an API call)
    db = database.get_db()
    recent_thinks = db.execute('''SELECT details FROM action_log
        WHERE phase = 'think' AND created_at > datetime('now', '-15 minutes')
        ORDER BY id DESC LIMIT 3''').fetchall()
    db.close()
    nexus_errors = sum(1 for t in recent_thinks if 'nexus error' in (t['details'] or '').lower())
    if nexus_errors >= 2:
        issues.append(f"Nexus failing: {nexus_errors}/3 recent thinks had errors")
    elif not recent_thinks:
        issues.append("No think cycles in last 15 min — kernel may be stalled")

    # Check sensor health
    stale_sensors = [name for name, ts in SENSOR_COOLDOWN.items() if time.time() - ts < 600]
    if stale_sensors:
        issues.append(f"Sensors in cooldown: {', '.join(stale_sensors)}")

    # Check task failure rate (last 20 tasks)
    db = database.get_db()
    recent = db.execute('''SELECT status, COUNT(*) as cnt FROM tasks
                          WHERE completed_at > datetime('now', '-1 hour')
                          GROUP BY status''').fetchall()
    db.close()
    status_map = {r['status']: r['cnt'] for r in recent}
    failed = status_map.get('failed', 0)
    completed = status_map.get('completed', 0)
    total = failed + completed
    if total > 0 and failed / total > 0.5:
        issues.append(f"High failure rate: {failed}/{total} tasks failed in last hour")

    # Auto-remediate what we can
    remediated = []

    # Check token usage — auto-throttle if too high
    global THINK_INTERVAL
    daily = database.get_daily_token_usage('nexus')
    if daily > DAILY_CALL_LIMIT * 0.8:
        if THINK_INTERVAL < THINK_INTERVAL_THROTTLED:
            THINK_INTERVAL = THINK_INTERVAL_THROTTLED
            remediated.append(f"Throttled think interval: {THINK_INTERVAL_DEFAULT} → {THINK_INTERVAL_THROTTLED} cycles")
        issues.append(f"High API usage: {daily}/{DAILY_CALL_LIMIT} calls — throttled to every {THINK_INTERVAL_THROTTLED} cycles")
    elif THINK_INTERVAL > THINK_INTERVAL_DEFAULT:
        THINK_INTERVAL = THINK_INTERVAL_DEFAULT
        remediated.append(f"Restored think interval to {THINK_INTERVAL_DEFAULT} cycles")

    if stale_sensors:
        for name in stale_sensors:
            SENSOR_COOLDOWN.pop(name, None)
        remediated.append(f"Cleared cooldown for {len(stale_sensors)} sensors")

    # Filter out issues that were already reported and auto-fixed
    now = time.time()
    new_issues = []
    for issue in issues:
        key = issue.split(':')[0]  # e.g. "High token usage"
        suppressed_until = DIAG_SUPPRESSED.get(key, 0)
        if now > suppressed_until:
            new_issues.append(issue)

    # Suppress re-notification for remediated issues (1 hour)
    if remediated:
        for issue in issues:
            key = issue.split(':')[0]
            DIAG_SUPPRESSED[key] = now + 3600

    # Only notify on genuinely new issues — and only ONCE per issue
    # Critical issues (nexus down, kernel stalled) get urgent priority (triggers texts)
    # Warnings (high failure rate, cooldowns, token throttle) stay low priority
    CRITICAL_KEYWORDS = ('nexus failing', 'kernel may be stalled')
    if new_issues:
        report = "SELF-DIAGNOSIS:\n" + "\n".join(f"- {i}" for i in new_issues)
        if remediated:
            report += "\nAUTO-FIX:\n" + "\n".join(f"- {r}" for r in remediated)
        has_critical = any(any(kw in i.lower() for kw in CRITICAL_KEYWORDS) for i in new_issues)
        severity = 'critical' if has_critical else 'warning'
        log(f'DIAG: {len(new_issues)} new issues ({severity}), {len(remediated)} auto-fixed', 'WARN')
        database.record_observation('self_diag', report, severity)
        # Use fixed dedup key so changing numbers don't bypass dedup
        diag_key = 'self-diag-' + '-'.join(sorted(set(i.split(':')[0].strip() for i in new_issues)))
        if diag_key not in LAST_NOTIFY or (time.time() - LAST_NOTIFY.get(diag_key, 0)) > 7200:
            pri = 'urgent' if has_critical else 'low'
            notify('Ghost: Self-Diagnosis', report, priority=pri)
            LAST_NOTIFY[diag_key] = time.time()
    elif remediated:
        log(f'DIAG: {len(remediated)} auto-fixed, no new issues')
    else:
        log('DIAG: all clear')


def startup():
    log('Ghost Brain Kernel v4 starting — smart routing, confidence gating, self-diagnosis')
    database.init_db()
    aor.init_aor()

    if not database.get_active_goals():
        database.create_goal('Monitor system health and notify if anything is wrong',
                           priority=8, goal_type='system_health')
        database.create_goal('Keep all Ghost services running',
                           priority=9, goal_type='system_health')

    goals = database.get_active_goals()
    scores = database.get_all_confidence()
    score_text = ', '.join([f'{s["goal_type"]}={s["confidence"]}%' for s in scores]) if scores else 'none yet'
    notify('Ghost Online',
           f'Kernel v4. {len(goals)} goals. Smart routing (max {MAX_GOALS_PER_CYCLE}/cycle). Confidence: {score_text}',
           priority='low')


def main():
    startup()
    cycle = 0
    while True:
        try:
            cycle += 1
            if cycle % 3 == 0:
                run_sensors()
            if cycle % THINK_INTERVAL == 0:
                run_planner()
            execute_tasks()
            if cycle % 10 == 0:
                self_diagnose()
            if cycle % 60 == 0:
                database.prune_old_observations(days=7)
                database.prune_old_actions(days=14)
            if cycle % 5 == 0:
                goals = database.get_active_goals()
                hourly = database.get_hourly_token_usage('nexus')
                daily = database.get_daily_token_usage('nexus')
                log(f'Cycle {cycle}: {len(goals)} goals, nexus_calls_1h={hourly} nexus_calls_today={daily}')
            time.sleep(CYCLE_INTERVAL)
        except KeyboardInterrupt:
            log('Kernel stopped.')
            notify('Ghost Offline', 'Kernel stopped.', priority='high')
            break
        except Exception as e:
            log(f'Kernel error: {e}', 'ERROR')
            time.sleep(10)


if __name__ == '__main__':
    main()
