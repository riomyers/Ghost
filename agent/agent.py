#!/usr/bin/env python3
"""Pickle Rick Autonomous Agent — the brain that never sleeps."""

import json
import time
import sqlite3
import subprocess
import os
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path('/home/atom/pickle-agent')
DATA_DIR = AGENT_DIR / 'data'
LOG_DIR = AGENT_DIR / 'logs'
DB_PATH = DATA_DIR / 'agent.db'
OLLAMA_URL = 'http://localhost:11434'

DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)


def log(msg: str, level: str = 'INFO'):
    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{ts}] [{level}] {msg}'
    print(line)
    log_file = LOG_DIR / f'{datetime.now().strftime("%Y-%m-%d")}.log'
    with open(log_file, 'a') as f:
        f.write(line + '\n')


def init_db():
    db = sqlite3.connect(str(DB_PATH))
    db.execute('''CREATE TABLE IF NOT EXISTS heartbeats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        uptime_sec INTEGER,
        load_avg TEXT,
        mem_used_pct REAL,
        disk_used_pct REAL,
        temp_c REAL,
        ollama_status TEXT
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS thoughts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        trigger TEXT,
        prompt TEXT,
        response TEXT,
        model TEXT,
        duration_sec REAL
    )''')
    db.execute('''CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created TEXT NOT NULL,
        type TEXT NOT NULL,
        payload TEXT,
        status TEXT DEFAULT 'pending',
        result TEXT,
        completed TEXT
    )''')
    db.commit()
    return db


def collect_vitals():
    uptime = int(float(open('/proc/uptime').read().split()[0]))
    load = open('/proc/loadavg').read().split()[:3]
    mem = {}
    for line in open('/proc/meminfo'):
        parts = line.split()
        if parts[0] in ('MemTotal:', 'MemAvailable:'):
            mem[parts[0].rstrip(':')] = int(parts[1])
    mem_pct = round((1 - mem.get('MemAvailable', 0) / max(mem.get('MemTotal', 1), 1)) * 100, 1)
    
    stat = os.statvfs('/')
    disk_pct = round((1 - stat.f_bavail / max(stat.f_blocks, 1)) * 100, 1)
    
    try:
        temp = round(int(open('/sys/class/thermal/thermal_zone0/temp').read().strip()) / 1000, 1)
    except:
        temp = 0.0
    
    try:
        r = subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=5)
        ollama = 'running' if r.returncode == 0 else 'error'
    except:
        ollama = 'down'
    
    return {
        'uptime_sec': uptime,
        'load_avg': ' '.join(load),
        'mem_used_pct': mem_pct,
        'disk_used_pct': disk_pct,
        'temp_c': temp,
        'ollama_status': ollama
    }


def record_heartbeat(db):
    vitals = collect_vitals()
    ts = datetime.now(timezone.utc).isoformat()
    db.execute(
        'INSERT INTO heartbeats (timestamp, uptime_sec, load_avg, mem_used_pct, disk_used_pct, temp_c, ollama_status) VALUES (?, ?, ?, ?, ?, ?, ?)',
        (ts, vitals['uptime_sec'], vitals['load_avg'], vitals['mem_used_pct'], vitals['disk_used_pct'], vitals['temp_c'], vitals['ollama_status'])
    )
    db.commit()
    return vitals


def think(prompt: str, model: str = 'gemma4:e4b'):
    """Ask the local brain a question."""
    try:
        import urllib.request
        data = json.dumps({'model': model, 'prompt': prompt, 'stream': False}).encode()
        req = urllib.request.Request(f'{OLLAMA_URL}/api/generate', data=data, headers={'Content-Type': 'application/json'})
        start = time.time()
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
        duration = round(time.time() - start, 1)
        return result.get('response', ''), duration
    except Exception as e:
        return f'Error: {e}', 0.0


def main_loop():
    log('🥒 Pickle Rick Agent starting up...')
    db = init_db()
    
    vitals = record_heartbeat(db)
    log(f'System: load={vitals["load_avg"]} mem={vitals["mem_used_pct"]}% disk={vitals["disk_used_pct"]}% temp={vitals["temp_c"]}°C ollama={vitals["ollama_status"]}')
    
    cycle = 0
    while True:
        try:
            cycle += 1
            
            # Heartbeat every cycle (60s)
            vitals = record_heartbeat(db)
            
            if cycle % 5 == 0:  # Every 5 minutes
                log(f'Heartbeat #{cycle}: load={vitals["load_avg"]} mem={vitals["mem_used_pct"]}% ollama={vitals["ollama_status"]}')
            
            # Check for pending jobs
            row = db.execute('SELECT id, type, payload FROM jobs WHERE status = "pending" ORDER BY created LIMIT 1').fetchone()
            if row:
                job_id, job_type, payload = row
                log(f'Processing job #{job_id}: {job_type}')
                db.execute('UPDATE jobs SET status = "running" WHERE id = ?', (job_id,))
                db.commit()
                
                if job_type == 'think':
                    used_model = 'gemma4:e4b'
                    response, duration = think(payload, model=used_model)
                    ts = datetime.now(timezone.utc).isoformat()
                    db.execute('INSERT INTO thoughts (timestamp, trigger, prompt, response, model, duration_sec) VALUES (?, ?, ?, ?, ?, ?)',
                               (ts, 'job', payload, response, used_model, duration))
                    db.execute('UPDATE jobs SET status = "done", result = ?, completed = ? WHERE id = ?',
                               (response[:1000], ts, job_id))
                    db.commit()
                    log(f'Job #{job_id} done in {duration}s')
            
            time.sleep(60)
            
        except KeyboardInterrupt:
            log('Agent shutting down.')
            break
        except Exception as e:
            log(f'Error in main loop: {e}', 'ERROR')
            time.sleep(10)


if __name__ == '__main__':
    main_loop()
