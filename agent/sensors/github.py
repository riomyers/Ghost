#!/usr/bin/env python3
"""GitHub sensor v2 — single GraphQL query for all repos. Fast."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
import database

OWNER = 'riomyers'
PRIORITY_REPOS = ['lumen', 'pickle-rick', 'atomancy', 'cura', 'flare', 'pulse', 'citadel', 'portal']

QUERY = """
query {
  %REPOS%
}
"""

REPO_FRAGMENT = """
  %ALIAS%: repository(owner: "%OWNER%", name: "%NAME%") {
    name
    defaultBranchRef {
      target {
        ... on Commit {
          oid
          messageHeadline
          committedDate
        }
      }
    }
    issues(states: OPEN, first: 1) { totalCount }
  }
"""


def sense():
    # Build one GraphQL query for all repos
    fragments = []
    for i, repo in enumerate(PRIORITY_REPOS):
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
            database.record_observation('github', f'GraphQL error: {r.stderr[:100]}', 'warning')
            return

        data = json.loads(r.stdout).get('data', {})
    except subprocess.TimeoutExpired:
        database.record_observation('github', 'GitHub GraphQL timed out', 'warning')
        return
    except Exception as e:
        database.record_observation('github', f'GitHub sensor error: {e}', 'warning')
        return

    for i, repo in enumerate(PRIORITY_REPOS):
        info = data.get(f'r{i}')
        if not info:
            continue

        # Latest commit
        branch = info.get('defaultBranchRef') or {}
        target = branch.get('target') or {}
        sha = (target.get('oid') or '')[:7]
        msg = target.get('messageHeadline', '')
        if sha:
            database.record_observation('github', f'{repo}: latest {sha} {msg[:80]}', 'info')

        # Open issues
        issues = (info.get('issues') or {}).get('totalCount', 0)
        if issues > 0:
            database.record_observation('github', f'{repo}: {issues} open issues', 'info')


if __name__ == '__main__':
    database.init_db()
    sense()
