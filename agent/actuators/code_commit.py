#!/usr/bin/env python3
"""Safe Code Commit Actuator — Ghost opens PRs but NEVER merges.

Flow: clone → branch → write files → commit → push → open PR
Safety: blocks main/master pushes, blocks merge, blocks force push
"""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import json
import subprocess
import shutil
from pathlib import Path
from datetime import datetime

WORK_DIR = Path('/home/atom/pickle-agent/workspaces')
OWNER = 'riomyers'

# Hard safety limits
BLOCKED_BRANCHES = ('main', 'master', 'production', 'release')
MAX_FILES_PER_PR = 10
MAX_FILE_SIZE = 50_000  # 50KB per file


def _run(cmd, cwd=None, timeout=60):
    """Run a command, return (success, output)."""
    try:
        r = subprocess.run(
            cmd if isinstance(cmd, list) else cmd.split(),
            capture_output=True, text=True, timeout=timeout, cwd=cwd
        )
        output = (r.stdout + r.stderr).strip()
        return r.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, 'Command timed out'
    except Exception as e:
        return False, str(e)


def propose_code_change(params):
    """Main entry point — called from kernel execute_tasks().

    params dict:
        repo: str — repo name (without owner), e.g. "pickle-rick"
        branch: str — branch name, e.g. "ghost/fix-typo"
        files: dict — {filepath: content} to write
        message: str — commit message
        description: str — PR body description
        base: str — base branch (default "dev", fallback "main")
    """
    repo_name = params.get('repo', '')
    branch = params.get('branch', '')
    files = params.get('files', {})
    message = params.get('message', 'Ghost: automated change')
    description = params.get('description', '')
    base = params.get('base', 'dev')

    # --- Safety checks ---
    if not repo_name or not branch:
        return 'ERROR: repo and branch are required'

    if branch.lower() in BLOCKED_BRANCHES:
        return f'BLOCKED: Cannot push to protected branch {branch}'

    if not branch.startswith('ghost/'):
        branch = f'ghost/{branch}'

    if not files:
        return 'ERROR: no files specified'

    if len(files) > MAX_FILES_PER_PR:
        return f'ERROR: too many files ({len(files)} > {MAX_FILES_PER_PR})'

    for filepath, content in files.items():
        if len(content) > MAX_FILE_SIZE:
            return f'ERROR: file {filepath} too large ({len(content)} > {MAX_FILE_SIZE})'
        if '..' in filepath or filepath.startswith('/'):
            return f'BLOCKED: suspicious path {filepath}'

    full_repo = f'{OWNER}/{repo_name}'

    # --- Clone ---
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    workspace = WORK_DIR / f'{repo_name}-{datetime.now().strftime("%Y%m%d_%H%M%S")}'

    ok, out = _run(['gh', 'repo', 'clone', full_repo, str(workspace)], timeout=120)
    if not ok:
        return f'ERROR cloning {full_repo}: {out[:200]}'

    # --- Check base branch exists ---
    ok, out = _run(['git', 'rev-parse', '--verify', f'origin/{base}'], cwd=str(workspace))
    if not ok:
        # Fallback to main
        base = 'main'
        ok, out = _run(['git', 'rev-parse', '--verify', f'origin/{base}'], cwd=str(workspace))
        if not ok:
            shutil.rmtree(workspace, ignore_errors=True)
            return f'ERROR: neither dev nor main branch found in {full_repo}'

    # --- Create branch ---
    ok, out = _run(['git', 'checkout', '-b', branch, f'origin/{base}'], cwd=str(workspace))
    if not ok:
        shutil.rmtree(workspace, ignore_errors=True)
        return f'ERROR creating branch: {out[:200]}'

    # --- Write files ---
    written = []
    for filepath, content in files.items():
        full_path = workspace / filepath
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        written.append(filepath)

    # --- Commit ---
    _run(['git', 'add'] + written, cwd=str(workspace))
    ok, out = _run(['git', 'commit', '-m', message], cwd=str(workspace))
    if not ok:
        shutil.rmtree(workspace, ignore_errors=True)
        return f'ERROR committing: {out[:200]}'

    # --- Push ---
    ok, out = _run(['git', 'push', 'origin', branch], cwd=str(workspace), timeout=120)
    if not ok:
        shutil.rmtree(workspace, ignore_errors=True)
        return f'ERROR pushing: {out[:200]}'

    # --- Open PR ---
    pr_body = f"""## Ghost Automated PR

{description}

---
**Files changed:** {', '.join(written)}
**Branch:** `{branch}` → `{base}`
**Opened by:** Ghost Brain v4 (autonomous agent)

> This PR was created automatically. Please review before merging.
> Ghost NEVER merges PRs — a human must approve and merge."""

    ok, out = _run(
        ['gh', 'pr', 'create',
         '--repo', full_repo,
         '--base', base,
         '--head', branch,
         '--title', message,
         '--body', pr_body],
        cwd=str(workspace), timeout=60
    )

    # Cleanup workspace
    shutil.rmtree(workspace, ignore_errors=True)

    if ok:
        return f'PR opened: {out.strip()}'
    else:
        return f'ERROR creating PR: {out[:200]}'


if __name__ == '__main__':
    print('Code commit actuator ready.')
    print(f'Work dir: {WORK_DIR}')
    print(f'Owner: {OWNER}')
    print(f'Safety: blocked branches={BLOCKED_BRANCHES}, max_files={MAX_FILES_PER_PR}')
