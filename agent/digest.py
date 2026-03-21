#!/usr/bin/env python3
"""Daily Digest — spoken system summary in Pickle Rick voice.

Compiles time, weather, system health, and recent events into a 3-5 sentence
briefing. Pushes to the proactive speech queue for delivery.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

# Sibling module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _get_time_greeting(hour: int) -> str:
    """Rick-style time awareness."""
    if hour < 12:
        return 'morning'
    elif hour < 17:
        return 'afternoon'
    else:
        return 'evening'


def _get_weather() -> str:
    """Fetch weather if API key available. Returns empty string on failure."""
    api_key = os.environ.get('OPENWEATHER_API_KEY', '')
    if not api_key:
        return ''

    try:
        import json
        import urllib.request
        # Default to Houston TX (Rio's area)
        lat = os.environ.get('GHOST_WEATHER_LAT', '29.76')
        lon = os.environ.get('GHOST_WEATHER_LON', '-95.37')
        url = (f'https://api.openweathermap.org/data/2.5/weather'
               f'?lat={lat}&lon={lon}&appid={api_key}&units=imperial')
        req = urllib.request.Request(url, headers={'User-Agent': 'Ghost/1.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        temp = round(data['main']['temp'])
        desc = data['weather'][0]['description']
        return f'{temp}F, {desc}'
    except Exception:
        return ''


def _get_system_health() -> dict:
    """Read system vitals from /proc and /sys (Linux only)."""
    health = {}

    # CPU load
    try:
        load = open('/proc/loadavg').read().split()[0]
        health['load'] = load
    except Exception:
        health['load'] = '?'

    # Memory
    try:
        mem = {}
        for line in open('/proc/meminfo'):
            parts = line.split()
            if parts[0] in ('MemTotal:', 'MemAvailable:'):
                mem[parts[0].rstrip(':')] = int(parts[1])
        total = mem.get('MemTotal', 1)
        avail = mem.get('MemAvailable', 0)
        health['mem_pct'] = round((1 - avail / max(total, 1)) * 100)
    except Exception:
        health['mem_pct'] = -1

    # Disk
    try:
        stat = os.statvfs('/')
        health['disk_pct'] = round((1 - stat.f_bavail / max(stat.f_blocks, 1)) * 100)
    except Exception:
        health['disk_pct'] = -1

    # Temperature
    try:
        temp = round(int(open('/sys/class/thermal/thermal_zone0/temp').read().strip()) / 1000)
        health['temp_c'] = temp
    except Exception:
        health['temp_c'] = None

    return health


def _get_recent_event_summary() -> str:
    """Summarize events from the last 24 hours."""
    try:
        import proactive

        db = proactive._get_db()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        # Count by type
        rows = db.execute(
            '''SELECT type, COUNT(*) as cnt FROM events
               WHERE created_at > ? GROUP BY type ORDER BY cnt DESC''',
            (cutoff,)
        ).fetchall()
        db.close()

        if not rows:
            return ''

        parts = []
        for r in rows:
            parts.append(f'{r["cnt"]} {r["type"].replace("_", " ")}')
        return ', '.join(parts[:4])
    except Exception:
        return ''


def compile_digest() -> str:
    """Build a spoken digest. 3-5 sentences, Rick voice, no markdown."""
    now = datetime.now()
    greeting = _get_time_greeting(now.hour)
    day = now.strftime('%A')
    time_str = now.strftime('%-I:%M %p')

    lines = []

    # Opening
    lines.append(f"Good {greeting}. It's {time_str} on {day}.")

    # Weather
    weather = _get_weather()
    if weather:
        lines.append(f"Outside it's {weather}. Not that I care.")
    else:
        lines.append("Weather data unavailable, because apparently that's too much to ask.")

    # System health
    health = _get_system_health()
    problems = []
    if health['mem_pct'] > 80:
        problems.append(f"memory at {health['mem_pct']}%")
    if health['disk_pct'] > 85:
        problems.append(f"disk at {health['disk_pct']}%")
    if health.get('temp_c') and health['temp_c'] > 75:
        problems.append(f"running hot at {health['temp_c']}C")
    load_val = float(health['load']) if health['load'] != '?' else 0
    if load_val > 3.0:
        problems.append(f"load average {health['load']}")

    if problems:
        lines.append(f"Systems are stressed: {', '.join(problems)}. Might wanna look at that.")
    else:
        lines.append(f"Systems nominal. Load {health['load']}, memory {health['mem_pct']}%, disk {health['disk_pct']}%.")

    # Recent events
    event_summary = _get_recent_event_summary()
    if event_summary:
        lines.append(f"Last 24 hours: {event_summary}.")

    return ' '.join(lines)


def morning_digest():
    """Push morning digest to proactive queue. Called by scheduler."""
    import proactive
    text = compile_digest()
    proactive.push_event(
        'daily_digest',
        text,
        priority=4,
        source='digest'
    )


def evening_digest():
    """Push evening digest to proactive queue. Called by scheduler."""
    import proactive
    digest = compile_digest()
    text = f"End of day report. {digest} That's the damage. Get some sleep, genius."
    proactive.push_event(
        'daily_digest',
        text,
        priority=4,
        source='digest'
    )


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Ghost Daily Digest')
    sub = parser.add_subparsers(dest='cmd')

    sub.add_parser('compile', help='Print compiled digest')
    sub.add_parser('morning', help='Push morning digest to queue')
    sub.add_parser('evening', help='Push evening digest to queue')

    args = parser.parse_args()

    if args.cmd == 'compile':
        print(compile_digest())
    elif args.cmd == 'morning':
        morning_digest()
        print('Morning digest queued.')
    elif args.cmd == 'evening':
        evening_digest()
        print('Evening digest queued.')
    else:
        # Default: just print
        print(compile_digest())
