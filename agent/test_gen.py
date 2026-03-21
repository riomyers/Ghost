#!/usr/bin/env python3
"""Test generator — Ghost reads code and generates tests."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import json
import os
from pathlib import Path


def generate_tests(repo, file_path=None):
    """Clone repo (or use existing), read a file, generate tests with Claude."""
    work_dir = Path(f'/home/atom/repos/{repo}')

    # Clone or pull
    if work_dir.exists():
        subprocess.run(['git', '-C', str(work_dir), 'pull', '--quiet'],
                      capture_output=True, timeout=30)
    else:
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ['gh', 'repo', 'clone', f'riomyers/{repo}', str(work_dir), '--', '--depth', '1'],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return f'Clone failed: {r.stderr[:200]}'

    # Find a testable file if none specified
    if not file_path:
        for ext in ['*.py', '*.ts', '*.js']:
            files = list(work_dir.rglob(ext))
            files = [f for f in files if 'node_modules' not in str(f)
                     and 'test' not in str(f).lower()
                     and '__pycache__' not in str(f)]
            if files:
                file_path = str(files[0].relative_to(work_dir))
                break

    if not file_path:
        return 'No testable files found'

    full_path = work_dir / file_path
    if not full_path.exists():
        return f'File not found: {file_path}'

    code = full_path.read_text()[:3000]

    prompt = f"""Generate unit tests for this code. Use pytest for Python, jest for JS/TS.

FILE: {file_path}
```
{code}
```

Write complete, runnable test code. Include imports. Test happy path, edge cases, and error cases. Be concise — 3-5 test functions max."""

    try:
        r = subprocess.run(
            ['claude', '-p', '--output-format', 'text'],
            input=prompt, capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return f'Claude failed: {r.stderr[:200]}'

        tests = r.stdout.strip()

        # Save tests
        test_dir = work_dir / 'ghost_tests'
        test_dir.mkdir(exist_ok=True)
        test_file = test_dir / f'test_{Path(file_path).stem}.py'
        test_file.write_text(tests)

        return f'Tests generated: {test_file.relative_to(work_dir)} ({len(tests)} chars)'
    except:
        return 'Test generation timed out'


if __name__ == '__main__':
    if len(sys.argv) >= 2:
        result = generate_tests(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
        print(result)
    else:
        print('Usage: test_gen.py <repo> [file_path]')
