#!/usr/bin/env python3
"""Vulnerability scanner sensor — npm audit / pip-audit on monitored repos.

Runs weekly (controlled by scheduler). On critical vulns, creates observations
and optionally triggers the code_commit actuator to open fix PRs.
"""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import subprocess
from pathlib import Path
from datetime import datetime
import database

REPOS_DIR = Path('/home/atom/repos')
OWNER = 'riomyers'

# Same repos as github sensor
MONITORED_REPOS = ['lumen', 'pickle-rick', 'atomancy', 'cura', 'flare', 'pulse', 'citadel', 'portal']


def _run(cmd, cwd=None, timeout=120):
    try:
        r = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, '', 'Timed out'
    except Exception as e:
        return -1, '', str(e)


def ensure_repo(repo_name):
    """Clone or pull repo into work directory."""
    REPOS_DIR.mkdir(parents=True, exist_ok=True)
    repo_path = REPOS_DIR / repo_name

    if repo_path.exists():
        # Pull latest
        _run(['git', 'fetch', 'origin'], cwd=str(repo_path))
        _run(['git', 'reset', '--hard', 'origin/HEAD'], cwd=str(repo_path))
        return repo_path
    else:
        code, out, err = _run(
            ['gh', 'repo', 'clone', f'{OWNER}/{repo_name}', str(repo_path)],
            timeout=180
        )
        return repo_path if code == 0 else None


def scan_npm(repo_path):
    """Run npm audit on a repo with package.json."""
    pkg = repo_path / 'package.json'
    if not pkg.exists():
        return []

    code, out, err = _run(['npm', 'audit', '--json'], cwd=str(repo_path), timeout=120)

    findings = []
    try:
        audit = json.loads(out)
        vulns = audit.get('vulnerabilities', {})
        for name, info in vulns.items():
            severity = info.get('severity', 'info')
            via = info.get('via', [])
            # Get advisory details
            title = name
            if via and isinstance(via[0], dict):
                title = via[0].get('title', name)

            fix_available = info.get('fixAvailable', False)

            findings.append({
                'package': name,
                'severity': severity,
                'title': title[:120],
                'fix_available': bool(fix_available),
                'range': info.get('range', ''),
            })
    except (json.JSONDecodeError, KeyError):
        # npm audit may not return valid JSON on some failures
        if 'found 0 vulnerabilities' not in (out + err):
            findings.append({
                'package': '?',
                'severity': 'info',
                'title': f'npm audit parse error: {(out + err)[:100]}',
                'fix_available': False,
                'range': '',
            })

    return findings


def scan_pip(repo_path):
    """Run pip-audit on a repo with requirements.txt."""
    reqs = repo_path / 'requirements.txt'
    if not reqs.exists():
        return []

    code, out, err = _run(
        ['pip-audit', '-r', str(reqs), '-f', 'json', '--desc'],
        cwd=str(repo_path), timeout=120
    )

    findings = []
    try:
        vulns = json.loads(out)
        for v in vulns:
            findings.append({
                'package': v.get('name', '?'),
                'severity': 'high' if 'CRITICAL' in v.get('description', '').upper() else 'moderate',
                'title': v.get('description', '')[:120],
                'fix_available': bool(v.get('fix_versions')),
                'range': v.get('version', ''),
            })
    except (json.JSONDecodeError, KeyError):
        pass

    return findings


def sense():
    """Main sensor entry — scan all repos, create observations."""
    total_findings = 0
    critical_repos = []

    for repo_name in MONITORED_REPOS:
        repo_path = ensure_repo(repo_name)
        if not repo_path or not repo_path.exists():
            continue

        findings = scan_npm(repo_path) + scan_pip(repo_path)
        if not findings:
            continue

        # Classify severities
        critical = [f for f in findings if f['severity'] in ('critical', 'high')]
        moderate = [f for f in findings if f['severity'] == 'moderate']
        low = [f for f in findings if f['severity'] in ('low', 'info')]

        total_findings += len(findings)

        if critical:
            critical_repos.append(repo_name)
            # Create critical observation for each high/critical vuln
            for f in critical[:5]:  # Cap at 5 per repo
                severity = 'critical' if f['severity'] == 'critical' else 'warning'
                fix_note = ' (fix available)' if f['fix_available'] else ''
                database.record_observation(
                    'vulns',
                    f'{repo_name}: {f["severity"].upper()} vuln in {f["package"]}: {f["title"]}{fix_note}',
                    severity
                )

        if moderate:
            database.record_observation(
                'vulns',
                f'{repo_name}: {len(moderate)} moderate vulnerabilities found',
                'info'
            )

    # Summary observation
    if total_findings > 0:
        database.record_observation(
            'vulns',
            f'Vuln scan complete: {total_findings} findings across {len(MONITORED_REPOS)} repos. '
            f'Critical repos: {", ".join(critical_repos) if critical_repos else "none"}',
            'warning' if critical_repos else 'info'
        )
    else:
        database.record_observation('vulns', 'Vuln scan complete: all repos clean', 'info')


if __name__ == '__main__':
    # Only run when explicitly triggered (scheduler or manual)
    # Kernel sensor sweep calls this without args — skip it
    if len(sys.argv) > 1 and sys.argv[1] == '--run':
        database.init_db()
        sense()
        print('Vulnerability scan complete')
    else:
        pass  # Silent skip during kernel sensor sweep
