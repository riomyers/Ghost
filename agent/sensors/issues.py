#!/usr/bin/env python3
"""Issue sensor v2 — single GraphQL query, no Claude classification (use Nexus instead)."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
from pathlib import Path
import database

OWNER = 'riomyers'
REPOS = ['lumen', 'pickle-rick', 'atomancy', 'cura', 'flare', 'pulse', 'citadel', 'portal']
TRIAGED_FILE = Path('/home/atom/pickle-agent/data/triaged_issues.json')

QUERY = """
query {
  %REPOS%
}
"""

REPO_FRAGMENT = """
  %ALIAS%: repository(owner: "%OWNER%", name: "%NAME%") {
    name
    issues(states: OPEN, first: 3, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number
        title
        labels(first: 5) { nodes { name } }
        createdAt
      }
    }
  }
"""


def load_triaged():
    try:
        return json.loads(TRIAGED_FILE.read_text())
    except Exception:
        return []


def save_triaged(triaged):
    TRIAGED_FILE.write_text(json.dumps(triaged[-500:]))


def sense():
    fragments = []
    for i, repo in enumerate(REPOS):
        frag = REPO_FRAGMENT.replace('%ALIAS%', f'r{i}')
        frag = frag.replace('%OWNER%', OWNER).replace('%NAME%', repo)
        fragments.append(frag)

    query = QUERY.replace('%REPOS%', '\n'.join(fragments))

    try:
        r = subprocess.run(
            ['gh', 'api', 'graphql', '-f', f'query={query}'],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0:
            return
        data = json.loads(r.stdout).get('data', {})
    except Exception:
        return

    triaged = load_triaged()

    for i, repo in enumerate(REPOS):
        info = data.get(f'r{i}')
        if not info:
            continue
        issues = (info.get('issues') or {}).get('nodes', [])
        for issue in issues:
            issue_id = f'{repo}#{issue["number"]}'
            if issue_id in triaged:
                continue
            labels = [l['name'] for l in (issue.get('labels') or {}).get('nodes', [])]
            if not labels:
                database.record_observation(
                    'github_issue',
                    f'NEW ISSUE: {repo}#{issue["number"]} "{issue["title"]}" (unlabeled)',
                    'info'
                )
            triaged.append(issue_id)

    save_triaged(triaged)


if __name__ == '__main__':
    database.init_db()
    sense()
