#!/usr/bin/env python3
"""PR reviewer — Ghost reads diffs and posts review comments on GitHub."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json


def review_pr(repo, number):
    """Read PR diff, review with Claude, post comment on GitHub."""
    full_repo = f'riomyers/{repo}'

    # Get the diff
    try:
        r = subprocess.run(
            ['gh', 'pr', 'diff', str(number), '--repo', full_repo],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode != 0:
            return f'Failed to get diff: {r.stderr[:200]}'
        diff = r.stdout[:3000]  # Limit diff size for Claude
    except:
        return 'Failed to get diff'

    # Get PR info
    try:
        r = subprocess.run(
            ['gh', 'pr', 'view', str(number), '--repo', full_repo,
             '--json', 'title,body,author,headRefName'],
            capture_output=True, text=True, timeout=15
        )
        pr_info = json.loads(r.stdout) if r.returncode == 0 else {}
    except:
        pr_info = {}

    title = pr_info.get('title', '?')
    body = (pr_info.get('body') or '')[:500]

    # Review with Claude
    prompt = f"""You are Ghost, an autonomous code reviewer. Review this PR concisely.

PR: {full_repo}#{number} — {title}
Description: {body[:300]}

DIFF:
{diff}

Write a brief code review (3-5 bullet points max). Focus on:
- Bugs or logic errors
- Security issues
- Missing edge cases
- Code quality concerns

If the code looks good, say so briefly. Be direct, no fluff. Start with a one-line verdict (LGTM / Needs Changes / Concerns)."""

    try:
        r = subprocess.run(
            ['claude', '-p', '--output-format', 'text'],
            input=prompt, capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return f'Claude review failed: {r.stderr[:200]}'
        review = r.stdout.strip()
    except:
        return 'Claude review timed out'

    # Post the review as a comment
    comment = f"## Ghost Auto-Review\n\n{review}\n\n---\n*Reviewed autonomously by Ghost (Pickle Rick Agent)*"

    try:
        r = subprocess.run(
            ['gh', 'pr', 'comment', str(number), '--repo', full_repo, '--body', comment],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return f'Review posted on {full_repo}#{number}'
        return f'Failed to post: {r.stderr[:200]}'
    except:
        return 'Failed to post review comment'


if __name__ == '__main__':
    import sys
    if len(sys.argv) >= 3:
        result = review_pr(sys.argv[1], int(sys.argv[2]))
        print(result)
    else:
        print('Usage: pr_reviewer.py <repo> <pr_number>')
