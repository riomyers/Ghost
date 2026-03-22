#!/usr/bin/env python3
"""Self-modification module — Ghost proposes, tests, and deploys changes to itself."""
import sys
sys.path.insert(0, '/home/atom/pickle-agent/src')

import subprocess
import shutil
import json
from pathlib import Path
from datetime import datetime

AGENT_DIR = Path('/home/atom/pickle-agent')
SANDBOX_DIR = Path('/home/atom/pickle-agent-sandbox')
BACKUP_DIR = Path('/home/atom/pickle-agent-backups')
PROPOSALS_DIR = AGENT_DIR / 'proposals'
NTFY_TOPIC = 'https://ntfy.sh/ghost-pickle-rick'
NTFY_PRIORITIES = {'min': '1', 'low': '2', 'default': '3', 'high': '4', 'urgent': '5'}


def notify(title, msg, priority='default'):
    pri = NTFY_PRIORITIES.get(priority, '3')
    try:
        subprocess.run(['curl', '-s', '-d', msg[:4000],
                        '-H', f'Title: {title}',
                        '-H', f'Priority: {pri}',
                        NTFY_TOPIC],
                      capture_output=True, timeout=15)
    except Exception:
        pass


def propose_change(file_path, new_content, description):
    """Write a proposed change without applying it."""
    PROPOSALS_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    proposal = {
        'timestamp': ts,
        'file': file_path,
        'description': description,
        'status': 'pending',
    }

    proposal_file = PROPOSALS_DIR / f'{ts}_{Path(file_path).stem}.json'
    proposal_file.write_text(json.dumps(proposal, indent=2))

    proposed_code = PROPOSALS_DIR / f'{ts}_{Path(file_path).name}.proposed'
    proposed_code.write_text(new_content)

    notify('Ghost: Self-Mod Proposal',
           f'I want to change {file_path}:\n{description}\n\nApprove at ghost.riomyers.com',
           priority='default')
    return str(proposal_file)


def backup_current():
    """Backup current agent code before applying changes."""
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = BACKUP_DIR / ts
    BACKUP_DIR.mkdir(exist_ok=True)
    shutil.copytree(AGENT_DIR / 'src', backup / 'src')
    return str(backup)


def apply_proposal(proposal_file):
    """Apply a proposed change with backup and rollback capability."""
    proposal = json.loads(Path(proposal_file).read_text())
    proposed_code_file = Path(proposal_file).with_suffix('.proposed')

    if not proposed_code_file.exists():
        return 'Proposed code file not found'

    target = Path(proposal['file'])
    if not str(target).startswith('/home/atom/pickle-agent/'):
        return 'BLOCKED: Can only modify files in pickle-agent directory'

    # Backup
    backup_path = backup_current()

    # Apply
    new_content = proposed_code_file.read_text()
    target.write_text(new_content)

    # Syntax check
    if target.suffix == '.py':
        try:
            compile(new_content, str(target), 'exec')
        except SyntaxError as e:
            # Rollback
            shutil.copytree(Path(backup_path) / 'src', AGENT_DIR / 'src', dirs_exist_ok=True)
            proposal['status'] = 'rolled_back'
            Path(proposal_file).write_text(json.dumps(proposal, indent=2))
            notify('Ghost: Self-Mod ROLLED BACK', f'Syntax error in {target.name}: {e}', priority='high')
            return f'Rolled back: {e}'

    proposal['status'] = 'applied'
    proposal['backup'] = backup_path
    Path(proposal_file).write_text(json.dumps(proposal, indent=2))

    notify('Ghost: Self-Mod Applied', f'Changed {target.name}: {proposal["description"]}', priority='default')
    return f'Applied: {proposal["description"]}'


def rollback_last():
    """Rollback to most recent backup."""
    if not BACKUP_DIR.exists():
        return 'No backups available'

    backups = sorted(BACKUP_DIR.iterdir(), reverse=True)
    if not backups:
        return 'No backups found'

    latest = backups[0]
    shutil.copytree(latest / 'src', AGENT_DIR / 'src', dirs_exist_ok=True)
    notify('Ghost: Rolled Back', f'Restored from backup {latest.name}', priority='high')
    return f'Rolled back to {latest.name}'


def list_proposals():
    """List pending proposals."""
    if not PROPOSALS_DIR.exists():
        return []
    proposals = []
    for f in sorted(PROPOSALS_DIR.glob('*.json')):
        proposals.append(json.loads(f.read_text()))
    return proposals


if __name__ == '__main__':
    print('Self-modification module ready')
    print(f'Proposals dir: {PROPOSALS_DIR}')
    print(f'Backups dir: {BACKUP_DIR}')
