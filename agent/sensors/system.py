#!/usr/bin/env python3
"""System health sensor — CPU, memory, disk, temp, services."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import os
import subprocess
import database

def sense():
    # CPU load
    load = open('/proc/loadavg').read().split()[:3]
    load_1m = float(load[0])

    # Memory
    mem = {}
    for line in open('/proc/meminfo'):
        parts = line.split()
        if parts[0] in ('MemTotal:', 'MemAvailable:'):
            mem[parts[0].rstrip(':')] = int(parts[1])
    mem_pct = round((1 - mem.get('MemAvailable', 0) / max(mem.get('MemTotal', 1), 1)) * 100, 1)

    # Disk
    stat = os.statvfs('/')
    disk_pct = round((1 - stat.f_bavail / max(stat.f_blocks, 1)) * 100, 1)

    # Temperature
    try:
        temp = round(int(open('/sys/class/thermal/thermal_zone0/temp').read().strip()) / 1000, 1)
    except:
        temp = 0

    # Services
    services = ['pickle-agent', 'ghost-worker', 'ghost-tunnel', 'pickle-dashboard', 'ollama']
    down = []
    for svc in services:
        try:
            r = subprocess.run(['systemctl', 'is-active', svc], capture_output=True, text=True, timeout=5)
            if r.stdout.strip() != 'active':
                down.append(svc)
        except:
            down.append(svc)

    # Determine severity
    severity = 'info'
    if load_1m > 3 or mem_pct > 85 or disk_pct > 80 or temp > 80:
        severity = 'warning'
    if load_1m > 4 or mem_pct > 95 or disk_pct > 95 or temp > 90 or down:
        severity = 'critical'

    content = f'load={" ".join(load)} mem={mem_pct}% disk={disk_pct}% temp={temp}C'
    if down:
        content += f' DOWN_SERVICES={",".join(down)}'

    database.record_observation('system_health', content, severity)

if __name__ == '__main__':
    database.init_db()
    sense()
