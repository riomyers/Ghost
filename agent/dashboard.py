#!/usr/bin/env python3
"""Ghost Dashboard v9 — goal-oriented, Ghost Feed timeline, Explain This."""

import json
import sqlite3
import subprocess
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template_string, jsonify, request, redirect

app = Flask(__name__)
DB_PATH = '/home/atom/pickle-agent/data/agent_state.db'
LOG_DIR = Path('/home/atom/pickle-agent/logs')

HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ghost</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#e0e0e0;font-family:"JetBrains Mono","Fira Code",monospace;font-size:13px;padding:16px;max-width:1000px;margin:0 auto}
h1{font-size:24px;color:#00ff41;text-shadow:0 0 8px #00ff41;margin-bottom:4px;display:inline}
.header{display:flex;align-items:baseline;gap:12px;margin-bottom:4px}
.ver{color:#333;font-size:11px}
.sub{color:#555;font-size:11px;margin-bottom:16px}
.card{background:#111;border:1px solid #1a1a1a;border-radius:8px;padding:14px;margin-bottom:14px}
.card h2{color:#0af;font-size:14px;margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #1a1a1a;display:flex;justify-content:space-between;align-items:center}
.card h2 .count{color:#444;font-size:11px;font-weight:normal}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:14px}
.stat{background:#111;border:1px solid #1a1a1a;border-radius:8px;text-align:center;padding:10px}
.stat .v{font-size:18px;color:#fff;font-weight:bold}
.stat .l{font-size:10px;color:#555;margin-top:3px}
.up{color:#0f0}.down{color:#f44}
.term{background:#000;border:1px solid #00ff41;border-radius:4px;padding:10px;min-height:180px;max-height:300px;overflow-y:auto;font-size:11px;line-height:1.4;white-space:pre-wrap;color:#00ff41;text-shadow:0 0 1px #00ff41}
.th{display:flex;justify-content:space-between;align-items:center}
.live{color:#f00;font-size:11px;animation:b 1s infinite}
@keyframes b{0%,100%{opacity:1}50%{opacity:.3}}
.acts{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:6px;margin-top:8px}
.btn{background:#1a1a1a;border:1px solid #333;color:#0f0;padding:8px;border-radius:6px;cursor:pointer;font-family:inherit;font-size:11px;text-align:center}
.btn:hover{background:#0f0;color:#000}
.btn.r{border-color:#f44}.btn.r:hover{background:#f44}
.btn.b{border-color:#0af}.btn.b:hover{background:#0af;color:#000}
.btn.sm{padding:3px 8px;font-size:10px;border-radius:3px}
.alert{background:#1a0a0a;border:1px solid #f44;border-radius:4px;padding:10px;margin-bottom:14px;color:#f88;font-size:12px}
.ok{background:#0a1a0a;border:1px solid #0f0;border-radius:4px;padding:10px;margin-bottom:14px;color:#8f8;font-size:12px}
input[type=text]{width:100%;background:#0a0a0a;border:1px solid #333;color:#0f0;padding:8px;border-radius:4px;font-family:inherit;font-size:12px;margin-top:6px}
.cmd-bar{position:relative;margin-bottom:6px}
.cmd-bar input{padding-left:28px}
.cmd-bar::before{content:">";position:absolute;left:10px;top:14px;color:#0af;font-weight:bold}
.cmd-res{background:#050510;border:1px solid #0af;border-radius:4px;padding:10px;margin-top:8px;font-size:12px;color:#8af;max-height:200px;overflow-y:auto;white-space:pre-wrap;display:none}

/* Goal cards */
.goal-card{background:#0a0a0a;border:1px solid #1a1a1a;border-radius:6px;padding:10px;margin-bottom:8px}
.goal-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.goal-title{font-size:12px;color:#e0e0e0;flex:1}
.goal-meta{display:flex;gap:8px;align-items:center}
.priority{background:#1a1a2a;color:#88f;padding:2px 6px;border-radius:3px;font-size:10px;font-weight:bold}
.conf-badge{padding:2px 6px;border-radius:3px;font-size:10px}
.conf-high{background:#0a2a0a;color:#0f0}
.conf-med{background:#2a2a0a;color:#ff0}
.conf-low{background:#2a0a0a;color:#f44}
.goal-detail{font-size:10px;color:#555;display:flex;gap:12px}
.goal-type{color:#0af}

/* Ghost Feed timeline */
.feed-item{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid #0a0a0a;font-size:12px;position:relative}
.feed-time{color:#333;font-size:10px;min-width:55px;text-align:right}
.feed-icon{width:20px;height:20px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;flex-shrink:0}
.feed-icon.sense{background:#1a2a2a;color:#8cc}
.feed-icon.think{background:#1a1a3a;color:#88f}
.feed-icon.act{background:#1a3a1a;color:#8f8}
.feed-icon.fail{background:#3a1a1a;color:#f88}
.feed-icon.reflect{background:#2a1a2a;color:#c8f}
.feed-body{flex:1;line-height:1.4}
.feed-label{font-size:10px;color:#555}
.feed-content{color:#ccc}
.explain-btn{background:none;border:1px solid #333;color:#888;padding:2px 6px;border-radius:3px;font-size:9px;cursor:pointer;margin-left:6px}
.explain-btn:hover{border-color:#0af;color:#0af}
.explain-result{background:#050510;border:1px solid #0af;border-radius:4px;padding:8px;margin-top:6px;font-size:11px;color:#8af;display:none}

/* Filters */
.filters{display:flex;gap:6px;margin-bottom:10px;flex-wrap:wrap}
.filter{background:#1a1a1a;border:1px solid #222;color:#888;padding:3px 8px;border-radius:3px;font-size:10px;cursor:pointer}
.filter.active{border-color:#0af;color:#0af}
.filter:hover{border-color:#333}
.res{background:#050505;border:1px solid #1a1a1a;border-radius:4px;padding:8px;margin-top:8px;font-size:11px;max-height:120px;overflow-y:auto;white-space:pre-wrap}
</style>
</head>
<body>
<div class="header"><h1>Ghost</h1><span class="ver">v4 · Dashboard v9</span></div>
<p class="sub">Autonomous Agent · Smart Routing · Confidence Gating · {{goals|length}} goals · {{total_actions}} actions</p>

{% if alerts %}
{% for a in alerts %}<div class="alert">{{ a }}</div>{% endfor %}
{% else %}
<div class="ok">All systems nominal.</div>
{% endif %}

<div class="card">
<h2>Command Bar</h2>
<div class="cmd-bar">
<input type="text" id="cmd" placeholder="Tell Ghost what to do..." autocomplete="off">
</div>
<div class="cmd-res" id="cmd-res"></div>
</div>

<div class="grid">
<div class="stat"><div class="v {{ 'up' if alive else 'down' }}">{{ 'ON' if alive else 'OFF' }}</div><div class="l">Agent</div></div>
<div class="stat"><div class="v">{{ uptime }}</div><div class="l">Uptime</div></div>
<div class="stat"><div class="v">{{ '%.0f'|format(temp) }}C</div><div class="l">Temp</div></div>
<div class="stat"><div class="v">{{ '%.0f'|format(mem) }}%</div><div class="l">Mem</div></div>
<div class="stat"><div class="v">{{ '%.0f'|format(disk) }}%</div><div class="l">Disk</div></div>
<div class="stat"><div class="v">{{ load }}</div><div class="l">Load</div></div>
<div class="stat"><div class="v">{{ daily_tokens }}</div><div class="l">Nexus/day</div></div>
</div>

<div class="card">
<h2>Goals <span class="count">{{ goals|length }} active</span></h2>
{% for g in goals %}
<div class="goal-card">
<div class="goal-header">
<div class="goal-title">{{ g.description[:90] }}</div>
<div class="goal-meta">
<span class="priority">P{{ g.priority }}</span>
{% if g.confidence is not none %}
<span class="conf-badge {{ 'conf-high' if g.confidence >= 80 else ('conf-low' if g.confidence < 50 else 'conf-med') }}">{{ g.confidence }}%</span>
{% endif %}
</div>
</div>
<div class="goal-detail">
<span class="goal-type">{{ g.goal_type or 'general' }}</span>
<span>{{ g.last_thought }}</span>
{% if g.last_action %}<span>Last: {{ g.last_action }}</span>{% endif %}
</div>
</div>
{% endfor %}
<form action="/goal" method="post">
<input type="text" name="desc" placeholder="Add a new goal...">
</form>
</div>

<div class="card">
<h2>Ghost Feed <span class="count">sense → think → act → reflect</span></h2>
<div class="filters">
<span class="filter active" data-f="all">All</span>
<span class="filter" data-f="think">Think</span>
<span class="filter" data-f="act">Act</span>
<span class="filter" data-f="sense">Sense</span>
<span class="filter" data-f="fail">Fail</span>
</div>
<div id="feed">
{% for f in feed %}
<div class="feed-item" data-type="{{ f.type }}">
<div class="feed-time">{{ f.time }}</div>
<div class="feed-icon {{ f.type }}">{{ f.icon }}</div>
<div class="feed-body">
<div class="feed-content">{{ f.text }}</div>
<div class="feed-label">{{ f.label }}{% if f.details_id %} <button class="explain-btn" onclick="explain({{ f.details_id }})">Explain</button>{% endif %}</div>
<div class="explain-result" id="explain-{{ f.details_id }}"></div>
</div>
</div>
{% endfor %}
</div>
</div>

<div class="card">
<div class="th"><h2>Live Terminal</h2><span class="live" id="ld">LIVE</span></div>
<div class="term" id="t">Loading...</div>
</div>

<div class="card">
<h2>Quick Actions</h2>
<div class="acts">
<form action="/action" method="post" style="display:contents">
<button class="btn b" name="a" value="status">Status</button>
<button class="btn b" name="a" value="services">Services</button>
<button class="btn" name="a" value="confidence">Confidence</button>
<button class="btn" name="a" value="sops">SOPs</button>
<button class="btn" name="a" value="logs">Logs</button>
<button class="btn r" name="a" value="reboot">Reboot</button>
</form>
</div>
{% if result %}<div class="res">{{ result }}</div>{% endif %}
</div>

<script>
// Terminal
const t=document.getElementById('t'),d=document.getElementById('ld');
let f=0;
async function p(){try{const r=await fetch('/api/terminal');const j=await r.json();t.textContent=j.output;t.scrollTop=t.scrollHeight;d.textContent='LIVE';d.style.color='#f00';f=0}catch(e){f++;if(f>3){d.textContent='OFFLINE';d.style.color='#666'}}}
p();setInterval(p,5000);

// Command bar
const cmd=document.getElementById('cmd'),res=document.getElementById('cmd-res');
cmd.addEventListener('keydown',async(e)=>{
  if(e.key==='Enter'&&cmd.value.trim()){
    const q=cmd.value.trim();cmd.value='';
    res.style.display='block';res.textContent='Thinking...';
    try{
      const r=await fetch('/api/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({command:q})});
      const j=await r.json();
      res.textContent=j.response||j.error||'No response';
    }catch(err){res.textContent='Error: '+err.message}
  }
});

// Feed filters
document.querySelectorAll('.filter').forEach(btn=>{
  btn.addEventListener('click',()=>{
    document.querySelectorAll('.filter').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    const f=btn.dataset.f;
    document.querySelectorAll('.feed-item').forEach(item=>{
      item.style.display=(f==='all'||item.dataset.type===f)?'flex':'none';
    });
  });
});

// Explain This
async function explain(id){
  const el=document.getElementById('explain-'+id);
  if(el.style.display==='block'){el.style.display='none';return}
  el.style.display='block';el.textContent='Asking Ghost...';
  try{
    const r=await fetch('/api/explain/'+id);
    const j=await r.json();
    el.textContent=j.explanation||j.error||'No explanation';
  }catch(err){el.textContent='Error: '+err.message}
}

setTimeout(()=>location.reload(),180000);
</script>
</body></html>'''

ACTIONS = {
    'status': 'echo "CPU: $(lscpu | grep "Model name" | sed "s/.*: *//")" && echo "Mem: $(free -h | awk "/Mem:/{print \\$3\\"/\\"\\$2}")" && echo "Disk: $(df -h / | awk "NR==2{print \\$3\\"/\\"\\$2}")" && echo "Temp: $(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null | awk "{printf \\"%.1fC\\", \\$1/1000}")"',
    'services': 'for s in pickle-agent ghost-worker ghost-tunnel pickle-dashboard ghost-scheduler ollama; do printf "%-20s %s\\n" "$s" "$(systemctl is-active $s)"; done',
    'confidence': 'cd /home/atom/pickle-agent && python3 -c "import sys; sys.path.insert(0,\\"src\\"); import database; database.init_db(); [print(f\\"{s[\\"goal_type\\"]}: {s[\\"confidence\\"]}% ({s[\\"successes\\"]}W/{s[\\"failures\\"]}L)\\") for s in database.get_all_confidence()] or print(\\"No data yet\\")"',
    'sops': 'ls -la /home/atom/pickle-agent/sops/*.md 2>/dev/null || echo "No SOPs yet"',
    'logs': 'tail -25 /home/atom/pickle-agent/logs/$(date +%Y-%m-%d).log 2>/dev/null || echo "No logs today"',
    'reboot': 'echo "Rebooting..." && sleep 3 && sudo reboot',
}


def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def get_vitals():
    v = {}
    try:
        up = int(float(open('/proc/uptime').read().split()[0]))
        v['uptime'] = f'{up // 3600}h {(up % 3600) // 60}m'
    except:
        v['uptime'] = '?'
    try:
        m = {}
        for l in open('/proc/meminfo'):
            p = l.split()
            if p[0] in ('MemTotal:', 'MemAvailable:'):
                m[p[0].rstrip(':')] = int(p[1])
        v['mem'] = (1 - m.get('MemAvailable', 0) / max(m.get('MemTotal', 1), 1)) * 100
    except:
        v['mem'] = 0
    try:
        s = os.statvfs('/')
        v['disk'] = (1 - s.f_bavail / max(s.f_blocks, 1)) * 100
    except:
        v['disk'] = 0
    try:
        v['temp'] = int(open('/sys/class/thermal/thermal_zone0/temp').read().strip()) / 1000
    except:
        v['temp'] = 0
    try:
        v['load'] = open('/proc/loadavg').read().split()[0]
    except:
        v['load'] = '?'
    try:
        subprocess.run(['systemctl', 'is-active', 'pickle-agent'], capture_output=True, check=True)
        v['alive'] = True
    except:
        v['alive'] = False
    return v


def get_goal_confidence(goal_type):
    db = get_db()
    row = db.execute(
        'SELECT successes, failures FROM confidence_scores WHERE goal_type = ?',
        (goal_type,)
    ).fetchone()
    db.close()
    if not row:
        return None
    total = row['successes'] + row['failures']
    if total < 3:
        return None
    return round((row['successes'] / total) * 100)


def get_enriched_goals():
    """Goals with confidence, last thought time, and last action."""
    db = get_db()
    goals = [dict(r) for r in db.execute(
        'SELECT * FROM goals WHERE status="active" ORDER BY priority DESC'
    ).fetchall()]

    for g in goals:
        gt = g.get('goal_type') or 'general'
        g['confidence'] = get_goal_confidence(gt)

        lt = g.get('last_thought_at', '')
        if lt:
            g['last_thought'] = f'thought {lt[11:16]}'
        else:
            g['last_thought'] = 'never checked'

        # Last completed task for this goal
        last_task = db.execute('''
            SELECT tool_name, result, completed_at FROM tasks
            WHERE goal_id = ? AND status IN ('completed', 'failed')
            ORDER BY id DESC LIMIT 1
        ''', (g['id'],)).fetchone()

        if last_task:
            tool = last_task['tool_name'] or '?'
            result = (last_task['result'] or '')[:60]
            g['last_action'] = f'{tool}: {result}'
        else:
            g['last_action'] = ''

    db.close()
    return goals


def get_feed(limit=30):
    """Ghost Feed — unified timeline of sense/think/act/reflect events."""
    db = get_db()
    feed = []

    goal_names = {}
    for r in db.execute('SELECT id, description FROM goals'):
        goal_names[str(r['id'])] = r['description'][:50]

    # Think events
    for r in db.execute("""SELECT id, phase, details, created_at FROM action_log
                          WHERE phase = 'think' AND details NOT LIKE '%Sensors complete%'
                          ORDER BY id DESC LIMIT ?""", (limit,)):
        time_str = r['created_at'][11:16] if r['created_at'] else ''
        details = r['details'] or ''
        goal_id = details.split('goal=')[1].split(' ')[0] if 'goal=' in details else '?'
        goal_name = goal_names.get(goal_id, f'#{goal_id}')

        is_action = 'no_action' not in details
        ftype = 'think'
        icon = '🧠'
        label = 'Thought about'
        text = goal_name

        if 'conf=' in details:
            try:
                conf = details.split('conf=')[1].split(' ')[0]
                text += f' ({conf}% conf)'
            except:
                pass

        if is_action:
            for tool in ('execute_bash', 'send_notification', 'review_pr', 'propose_code_change', 'research_web'):
                if tool in details:
                    text += f' → {tool}'
                    break

        feed.append({'type': ftype, 'icon': icon, 'label': label, 'text': text,
                     'time': time_str, 'details_id': r['id']})

    # Act events (completed tasks)
    for r in db.execute("""SELECT t.id, t.tool_name, t.status, t.result, t.completed_at,
                                  g.description as goal_desc
                          FROM tasks t
                          LEFT JOIN goals g ON t.goal_id = g.id
                          WHERE t.status IN ('completed', 'failed')
                          ORDER BY t.id DESC LIMIT ?""", (limit,)):
        time_str = r['completed_at'][11:16] if r['completed_at'] else ''
        tool = r['tool_name'] or '?'
        result = (r['result'] or '')[:80]
        failed = r['status'] == 'failed'

        feed.append({
            'type': 'fail' if failed else 'act',
            'icon': '❌' if failed else '⚡',
            'label': 'Failed' if failed else 'Executed',
            'text': f'{tool}: {result}',
            'time': time_str,
            'details_id': r['id']
        })

    # Sense events (just the latest per sensor sweep)
    for r in db.execute("""SELECT id, details, created_at FROM action_log
                          WHERE phase = 'sense'
                          ORDER BY id DESC LIMIT 5"""):
        time_str = r['created_at'][11:16] if r['created_at'] else ''
        feed.append({
            'type': 'sense',
            'icon': '👁',
            'label': 'Sensed',
            'text': (r['details'] or '')[:80],
            'time': time_str,
            'details_id': None
        })

    # Sort by time descending
    feed.sort(key=lambda x: x['time'] or '', reverse=True)
    db.close()
    return feed[:limit]


def get_alerts():
    alerts = []
    db = get_db()

    crits = db.execute('''SELECT COUNT(*) FROM observations
                         WHERE severity = 'critical'
                         AND created_at > datetime('now', '-1 hour')''').fetchone()[0]
    if crits:
        alerts.append(f'{crits} critical observations in the last hour')

    fails = db.execute('''SELECT COUNT(*) FROM tasks
                         WHERE status = 'failed'
                         AND completed_at > datetime('now', '-1 hour')''').fetchone()[0]
    if fails:
        alerts.append(f'{fails} failed tasks in the last hour')

    db.close()
    return alerts


# --- Routes ---

@app.route('/')
def index():
    v = get_vitals()
    db = get_db()
    think_count = db.execute("SELECT COUNT(*) FROM action_log WHERE phase='think'").fetchone()[0]
    total_actions = db.execute("SELECT COUNT(*) FROM action_log").fetchone()[0]

    today = datetime.now().strftime('%Y-%m-%d')
    daily_tokens = db.execute(
        "SELECT COALESCE(SUM(tokens_used), 0) FROM token_budget WHERE created_at LIKE ?",
        (f'{today}%',)
    ).fetchone()[0]
    db.close()

    goals = get_enriched_goals()
    feed = get_feed()
    alerts = get_alerts()
    result = request.args.get('r', '')

    return render_template_string(HTML, goals=goals, feed=feed, alerts=alerts,
                                  think_count=think_count, total_actions=total_actions,
                                  daily_tokens=daily_tokens,
                                  result=urllib.parse.unquote(result), **v)


@app.route('/action', methods=['POST'])
def action():
    a = request.form.get('a', '')
    if a in ACTIONS:
        try:
            r = subprocess.run(['bash', '-c', ACTIONS[a]], capture_output=True, text=True, timeout=120)
            result = (r.stdout + r.stderr).strip()[:400]
        except subprocess.TimeoutExpired:
            result = 'Timed out'
        except Exception as e:
            result = str(e)[:200]
        return redirect(f'/?r={urllib.parse.quote(result[:300])}')
    return redirect('/')


@app.route('/goal', methods=['POST'])
def add_goal():
    desc = request.form.get('desc', '').strip()
    if desc:
        db = get_db()
        db.execute('INSERT INTO goals (description, priority) VALUES (?, 8)', (desc,))
        db.commit()
        db.close()
    return redirect('/')


@app.route('/api/terminal')
def terminal():
    try:
        r = subprocess.run(['tmux', 'capture-pane', '-t', 'rick', '-p', '-S', '-35'],
                          capture_output=True, text=True, timeout=5)
        return jsonify(output=r.stdout.strip() if r.returncode == 0 else 'tmux not available')
    except:
        return jsonify(output='terminal error')


@app.route('/api/status')
def api_status():
    return jsonify(get_vitals())


@app.route('/api/command', methods=['POST'])
def api_command():
    """Natural language command bar."""
    data = request.get_json(force=True)
    command = data.get('command', '').strip()
    if not command:
        return jsonify(error='Empty command'), 400
    if len(command) > 1000:
        return jsonify(error='Command too long'), 400

    db = get_db()
    cursor = db.execute('INSERT INTO goals (description, priority, goal_type) VALUES (?, 10, ?)',
                        (command, 'communication'))
    goal_id = cursor.lastrowid
    db.commit()
    db.close()

    try:
        import sys
        sys.path.insert(0, '/home/atom/pickle-agent/src')
        import nexus_client
        import database as db_mod

        prompt = f"""You are Ghost, an autonomous AI agent. Rio sent this via the dashboard:
"{command}"
Respond helpfully and concisely (under 200 words). Be direct."""

        response, model, provider = nexus_client.chat(prompt, model='haiku', timeout=30)
        db_mod.record_token_usage('nexus', 1)
        db_mod.log_action('think', f'command_bar goal={goal_id}: {response[:200]}', model='nexus')
        return jsonify(response=response, goal_id=goal_id)
    except Exception as e:
        return jsonify(response=f'Queued as Goal #{goal_id}. Processing next cycle.',
                      goal_id=goal_id, error=str(e)[:100])


@app.route('/api/text', methods=['POST'])
def api_text():
    """Bidirectional iMessage API."""
    data = request.get_json(force=True)
    message = data.get('message', '').strip()
    sender = data.get('sender', 'unknown')
    if not message:
        return jsonify(error='Empty message'), 400

    db = get_db()
    db.execute('INSERT INTO goals (description, priority, goal_type, status) VALUES (?, 10, ?, ?)',
               (f'Reply to text from {sender}: {message[:100]}', 'communication', 'achieved'))
    db.commit()
    db.close()

    try:
        import sys
        sys.path.insert(0, '/home/atom/pickle-agent/src')
        import nexus_client
        import database as db_mod

        prompt = f"""You are Ghost, an AI agent texting Rio. SHORT reply (1-3 sentences).
Be helpful, direct. You run on a Linux server with a brain daemon and dashboard.
Rio's message: {message}
Your reply:"""

        response, _, _ = nexus_client.chat(prompt, model='haiku', timeout=30)
        db_mod.record_token_usage('nexus', 1)
        db_mod.log_action('act', f'text_reply to={sender}: {response[:200]}', model='nexus')
        return jsonify(response=response)
    except Exception as e:
        return jsonify(response="Brain busy. Try again in a minute.", error=str(e)[:100])


@app.route('/api/explain/<int:action_id>')
def api_explain(action_id):
    """Explain This button — ask Nexus why Ghost did something."""
    db = get_db()
    row = db.execute('SELECT * FROM action_log WHERE id = ?', (action_id,)).fetchone()
    db.close()

    if not row:
        return jsonify(error='Action not found'), 404

    details = row['details'] or ''
    phase = row['phase'] or '?'

    try:
        import sys
        sys.path.insert(0, '/home/atom/pickle-agent/src')
        import nexus_client
        import database as db_mod

        prompt = f"""You are Ghost, explaining your own actions to Rio in plain English.

Action type: {phase}
Details: {details[:400]}

Explain in 2-3 simple sentences: what you did, why, and what happened.
No jargon. Like explaining to a friend."""

        explanation, _, _ = nexus_client.chat(prompt, model='haiku', timeout=20)
        db_mod.record_token_usage('nexus', 1)
        return jsonify(explanation=explanation)
    except Exception as e:
        return jsonify(explanation=f'[{phase}] {details[:200]}', error=str(e)[:100])


@app.route('/api/goals')
def api_goals():
    goals = get_enriched_goals()
    return jsonify(goals=goals)


@app.route('/api/feed')
def api_feed():
    feed = get_feed(limit=50)
    return jsonify(feed=feed)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
