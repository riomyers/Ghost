#!/usr/bin/env python3
"""Ghost scheduler v2 — time-based goal triggers using APScheduler."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
from datetime import datetime
import database
import nexus_client

NTFY_TOPIC = 'https://ntfy.sh/ghost-pickle-rick'


def notify(title, message):
    try:
        subprocess.run(
            ['curl', '-s', '-d', message[:500], '-H', f'Title: {title}', NTFY_TOPIC],
            capture_output=True, timeout=15
        )
    except:
        pass


def hourly_health_check():
    """Run every hour — check system health and notify if anything is off."""
    import os
    load = open('/proc/loadavg').read().split()[0]
    mem = {}
    for line in open('/proc/meminfo'):
        p = line.split()
        if p[0] in ('MemTotal:', 'MemAvailable:'):
            mem[p[0].rstrip(':')] = int(p[1])
    mem_pct = round((1 - mem.get('MemAvailable', 0) / max(mem.get('MemTotal', 1), 1)) * 100, 1)
    stat = os.statvfs('/')
    disk_pct = round((1 - stat.f_bavail / max(stat.f_blocks, 1)) * 100, 1)

    if float(load) > 3 or mem_pct > 80 or disk_pct > 80:
        notify('Ghost: Health Warning',
               f'Load: {load}\nMemory: {mem_pct}%\nDisk: {disk_pct}%')


def check_deployed_services():
    """Run every 30 min — check if ghost.riomyers.com is up."""
    try:
        r = subprocess.run(
            ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code}', '--max-time', '10',
             'http://localhost:8080'],
            capture_output=True, text=True, timeout=15
        )
        code = r.stdout.strip()
        if code != '200':
            notify('Ghost: Dashboard Down', f'ghost.riomyers.com returned {code}')
            subprocess.run(['sudo', 'systemctl', 'restart', 'pickle-dashboard'], timeout=10)
            subprocess.run(['sudo', 'systemctl', 'restart', 'ghost-tunnel'], timeout=10)
            notify('Ghost: Auto-Recovery', 'Restarted dashboard and tunnel')
    except:
        notify('Ghost: Dashboard Unreachable', 'Could not reach ghost.riomyers.com')


def weekly_vuln_scan():
    """Run weekly — scan all repos for dependency vulnerabilities."""
    try:
        from sensors.vulns import sense
        sense()
        notify('Ghost: Vuln Scan', 'Weekly vulnerability scan complete. Check dashboard for results.')
    except Exception as e:
        notify('Ghost: Vuln Scan Failed', str(e)[:300])


def end_of_day_digest():
    """Run at 10pm CDT — summarize the day's work and send to Rio."""
    database.init_db()

    # Gather day's data
    actions = database.get_recent_actions(limit=50)
    today = datetime.now().strftime('%Y-%m-%d')
    today_actions = [a for a in actions if a.get('created_at', '').startswith(today)]

    observations = database.get_recent_observations(limit=50)
    today_obs = [o for o in observations if o.get('created_at', '').startswith(today)]

    # Count by type
    thinks = len([a for a in today_actions if a['phase'] == 'think'])
    acts = len([a for a in today_actions if a['phase'] == 'act'])
    senses = len([a for a in today_actions if a['phase'] == 'sense'])
    criticals = len([o for o in today_obs if o['severity'] == 'critical'])
    warnings = len([o for o in today_obs if o['severity'] == 'warning'])

    # Get token usage
    daily_tokens = database.get_daily_token_usage('nexus')

    # Get confidence scores
    scores = database.get_all_confidence()
    conf_text = ', '.join([f'{s["goal_type"]}: {s["confidence"]}%' for s in scores]) if scores else 'no data yet'

    # Build summary prompt for Nexus
    summary_data = f"""Summarize Ghost's day in 3-5 bullet points for Rio.

Today's stats:
- {thinks} think cycles, {acts} actions taken, {senses} sensor sweeps
- {criticals} critical observations, {warnings} warnings
- {daily_tokens} Nexus API calls
- Confidence scores: {conf_text}

Notable actions today:
"""
    for a in today_actions[:10]:
        if a['phase'] in ('think', 'act') and 'no_action' not in a.get('details', ''):
            summary_data += f"- [{a['phase']}] {a['details'][:100]}\n"

    summary_data += "\nBe concise. Focus on what Ghost actually DID, not what it thought about."

    try:
        summary, _, _ = nexus_client.chat(summary_data, model='haiku', timeout=30)
        database.record_token_usage('nexus', 1)
    except Exception:
        summary = f'Ghost EOD: {thinks} thoughts, {acts} actions, {criticals} critical alerts, {daily_tokens} API calls.'

    notify('Ghost: End of Day', summary[:500])
    database.log_action('act', f'EOD digest sent: {summary[:200]}')


def setup_scheduler():
    """Create and return configured scheduler."""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone='America/Chicago')

    scheduler.add_job(hourly_health_check, 'interval', hours=1, id='health_check')
    scheduler.add_job(check_deployed_services, 'interval', minutes=30, id='service_check')
    scheduler.add_job(weekly_vuln_scan, 'cron', day_of_week='mon', hour=6, id='vuln_scan')
    scheduler.add_job(end_of_day_digest, 'cron', hour=22, minute=0, id='eod_digest')

    return scheduler


if __name__ == '__main__':
    s = setup_scheduler()
    s.start()
    print('Scheduler v2 running with jobs:')
    for job in s.get_jobs():
        print(f'  {job.id}: {job.trigger}')
    import time
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        s.shutdown()
