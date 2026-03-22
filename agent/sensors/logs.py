#!/usr/bin/env python3
"""Log anomaly detection sensor — reads kernel + app logs, flags suspicious patterns."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import re
from datetime import datetime
from pathlib import Path
import database
import nexus_client

LOG_DIR = Path('/home/atom/pickle-agent/logs')
SYSLOG = Path('/var/log/syslog')
JOURNAL_UNITS = ['pickle-agent', 'pickle-dashboard', 'ghost-scheduler', 'ghost-tunnel', 'ollama']

# Known patterns that are ALWAYS suspicious
CRITICAL_PATTERNS = [
    (r'OOM|Out of memory|oom-killer', 'critical', 'OOM kill detected'),
    (r'segfault|segmentation fault', 'critical', 'Segfault detected'),
    (r'disk.*full|No space left', 'critical', 'Disk full'),
    (r'FATAL|panic|PANIC', 'critical', 'Fatal error'),
]

WARNING_PATTERNS = [
    (r'error|ERROR|Error', 'warning', 'Error in logs'),
    (r'timeout|Timeout|TIMEOUT', 'warning', 'Timeout detected'),
    (r'refused|REFUSED|connection refused', 'warning', 'Connection refused'),
    (r'permission denied|Permission denied', 'warning', 'Permission denied'),
    (r'killed|KILLED', 'warning', 'Process killed'),
]


def _read_recent_log(path, lines=50):
    """Read last N lines of a log file."""
    if not path.exists():
        return ''
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, lines * 200)
            f.seek(max(0, size - read_size))
            content = f.read().decode('utf-8', errors='replace')
        return '\n'.join(content.splitlines()[-lines:])
    except Exception:
        return ''


def _check_journal(unit, lines=30):
    """Read recent journalctl output for a systemd unit."""
    import subprocess
    try:
        r = subprocess.run(
            ['journalctl', '-u', unit, '-n', str(lines), '--no-pager', '-q'],
            capture_output=True, text=True, timeout=10
        )
        return r.stdout if r.returncode == 0 else ''
    except Exception:
        return ''


def _pattern_scan(text, source):
    """Scan text for known suspicious patterns."""
    findings = []

    for pattern, severity, label in CRITICAL_PATTERNS + WARNING_PATTERNS:
        matches = re.findall(pattern, text, re.IGNORECASE)
        if matches:
            # Get the line containing the match for context
            for line in text.splitlines():
                if re.search(pattern, line, re.IGNORECASE):
                    findings.append({
                        'severity': severity,
                        'source': source,
                        'message': f'{label}: {line.strip()[:500]}',
                    })
                    break  # One finding per pattern per source

    return findings


def sense():
    """Main sensor — scan all log sources for anomalies."""
    all_findings = []

    # 1. Kernel logs (today)
    today_log = LOG_DIR / f'{datetime.now().strftime("%Y-%m-%d")}.log'
    kernel_text = _read_recent_log(today_log, lines=100)
    if kernel_text:
        all_findings.extend(_pattern_scan(kernel_text, 'kernel_log'))

    # 2. Systemd journal for each Ghost service
    for unit in JOURNAL_UNITS:
        journal = _check_journal(unit, lines=30)
        if journal:
            all_findings.extend(_pattern_scan(journal, f'journal:{unit}'))

    # 3. Syslog (if readable)
    syslog_text = _read_recent_log(SYSLOG, lines=50)
    if syslog_text:
        all_findings.extend(_pattern_scan(syslog_text, 'syslog'))

    # Deduplicate by message
    seen = set()
    unique = []
    for f in all_findings:
        key = f['message']
        if key not in seen:
            seen.add(key)
            unique.append(f)

    # Record observations
    for f in unique:
        database.record_observation('logs', f'{f["source"]}: {f["message"]}', f['severity'])

    # If there are multiple warnings/criticals, send a batch to Nexus for analysis
    criticals = [f for f in unique if f['severity'] == 'critical']
    warnings = [f for f in unique if f['severity'] == 'warning']

    if len(criticals) >= 1 or len(warnings) >= 3:
        findings_text = '\n'.join([f'[{f["severity"]}] {f["source"]}: {f["message"]}'
                                   for f in unique[:10]])
        try:
            prompt = f"""You are Ghost, an autonomous agent monitoring its own logs.
Analyze these log anomalies and determine if any require immediate action:

{findings_text}

Respond with 1-2 sentences: what's happening and what (if anything) to do about it."""

            analysis, _, _ = nexus_client.chat(prompt, model='haiku', timeout=20,
                                                 priority='low')
            database.record_token_usage('nexus', 1)
            database.record_observation('logs',
                f'Log analysis: {analysis}',
                'critical' if criticals else 'warning')
        except Exception:
            pass

    if not unique:
        database.record_observation('logs', 'Log scan: all clear', 'debug')


if __name__ == '__main__':
    database.init_db()
    sense()
    print('Log anomaly scan complete')
