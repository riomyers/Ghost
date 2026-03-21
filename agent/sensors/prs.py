#!/usr/bin/env python3
"""PR sensor v2 — single GraphQL query for all repos."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
from pathlib import Path
import database

OWNER = 'riomyers'
REPOS = ['lumen', 'pickle-rick', 'atomancy', 'cura', 'flare', 'pulse', 'citadel', 'portal']
REVIEWED_FILE = Path('/home/atom/pickle-agent/data/reviewed_prs.json')

QUERY = """
query {
  %REPOS%
}
"""

REPO_FRAGMENT = """
  %ALIAS%: repository(owner: "%OWNER%", name: "%NAME%") {
    name
    pullRequests(states: OPEN, first: 3, orderBy: {field: CREATED_AT, direction: DESC}) {
      nodes {
        number
        title
        author { login }
        headRefName
        changedFiles
        additions
        deletions
        createdAt
      }
    }
  }
"""


def load_reviewed():
    try:
        return json.loads(REVIEWED_FILE.read_text())
    except Exception:
        return []


def save_reviewed(reviewed):
    REVIEWED_FILE.write_text(json.dumps(reviewed[-200:]))


def sense():
    # Build GraphQL query
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

    reviewed = load_reviewed()
    new_prs = []

    for i, repo in enumerate(REPOS):
        info = data.get(f'r{i}')
        if not info:
            continue
        prs = (info.get('pullRequests') or {}).get('nodes', [])
        for pr in prs:
            pr_id = f'{repo}#{pr["number"]}'
            if pr_id in reviewed:
                continue
            author = (pr.get('author') or {}).get('login', '?')
            adds = pr.get('additions', 0)
            dels = pr.get('deletions', 0)
            new_prs.append({'repo': repo, 'number': pr['number'], 'title': pr['title']})
            reviewed.append(pr_id)
            database.record_observation(
                'github_pr',
                f'NEW PR: {repo}#{pr["number"]} "{pr["title"]}" by {author} (+{adds}/-{dels})',
                'warning'
            )

    save_reviewed(reviewed)

    if new_prs:
        review_goal = None
        for g in database.get_active_goals():
            if 'PR' in g['description'] or 'review' in g['description'].lower():
                review_goal = g
                break
        gid = review_goal['id'] if review_goal else database.create_goal(
            'Review new pull requests', priority=9, goal_type='code_review')
        for pr in new_prs:
            database.create_task(gid, f'Review {pr["repo"]}#{pr["number"]}: {pr["title"][:60]}',
                               'review_pr', {'repo': pr['repo'], 'number': pr['number']})


if __name__ == '__main__':
    database.init_db()
    sense()
