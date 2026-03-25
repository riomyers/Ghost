#!/usr/bin/env python3
"""Service uptime sensor — HTTP checks on deployed services."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
from pathlib import Path
import database

CONFIG_PATH = Path('/home/atom/pickle-agent/config.json')

DEFAULT_SERVICES = [
    {'name': 'ghost-dashboard', 'url': 'http://localhost:8180', 'expected_status': 200},
]

def sense():
    # Load config
    services = DEFAULT_SERVICES
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            services = cfg.get('monitored_services', DEFAULT_SERVICES)
        except:
            pass

    for svc in services:
        name = svc['name']
        url = svc['url']
        expected = svc.get('expected_status', 200)

        try:
            r = subprocess.run(
                ['curl', '-s', '-o', '/dev/null', '-w', '%{http_code} %{time_total}',
                 '--max-time', '10', url],
                capture_output=True, text=True, timeout=15
            )
            parts = r.stdout.strip().split()
            status_code = int(parts[0]) if parts else 0
            latency = float(parts[1]) if len(parts) > 1 else 0

            if status_code == expected:
                severity = 'info'
                content = f'{name}: UP status={status_code} latency={latency:.2f}s'
            elif status_code == 0:
                severity = 'critical'
                content = f'{name}: UNREACHABLE url={url}'
            else:
                severity = 'warning'
                content = f'{name}: UNEXPECTED status={status_code} expected={expected} latency={latency:.2f}s'

        except Exception as e:
            severity = 'critical'
            content = f'{name}: ERROR {str(e)[:100]}'

        database.record_observation('uptime_check', content, severity)

if __name__ == '__main__':
    database.init_db()
    sense()
