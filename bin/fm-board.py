#!/usr/bin/env python3
"""Presentation over canonical fleet JSON; no status/backlog/metadata parser.

config/board-groups.json: optional {"rules": [{"repo": "regex", "match":
"regex", "group": "Continuo", "category": "compensation"}]}.
Rules are first-match, case-insensitive searches; repo matches the canonical repo
or project, match searches title + task id. Omitted patterns match everything.
Invalid configuration fails before writing. Built-in rules follow custom rules.
Only the exact `## My notes` section is user-owned; regeneration preserves its
contents verbatim. The generated part uses atomic replace and detects concurrent
notes edits before publishing. All presentation writers serialize on board.lock.
"""
import argparse
import curses
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import time

SCRIPT = Path(__file__).resolve().parent
GROUPS = ['Continuo', 'Cadenza', 'ISP', 'AAA', 'MCA website', 'Firstmate', 'Other']
CATEGORIES = ['multi-firm programme', 'gaps and competitor features',
              'compensation', 'onboarding/legal', 'other']
SECTIONS = ['Waiting on the captain', 'Running', 'Done recently',
            'Parked with a bring-back trigger']
NOTES = '## My notes\n'


def plain(value):
    value = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value or ''))
    for old, new in [('crewmate', 'worker'), ('captain-hold', 'decision'),
                     ('watcher', 'monitor'), ('wake', 'update'), ('hold', 'wait'),
                     ('no-mistakes', 'review'), ('ask-user', 'decision')]:
        value = re.sub(r'\b' + old + r'\b', new, value, flags=re.I)
    return ' '.join(value.split())


def mapping(home):
    path = home / 'config/board-groups.json'
    custom = json.loads(path.read_text())['rules'] if path.exists() else []
    if not isinstance(custom, list):
        raise ValueError('board-groups.json rules must be an array')
    defaults = [dict(repo=pattern, group=group) for pattern, group in [
        ('continuo', 'Continuo'), ('cadenza', 'Cadenza'),
        ('isp', 'ISP'), ('(^|[/_-])aaa($|[/_-])', 'AAA'),
        ('mca.*(website|web)|website.*mca', 'MCA website'),
        ('firstmate', 'Firstmate')]]
    compiled = []
    for rule in custom + defaults:
        group = rule['group']
        category = rule.get('category')
        if group not in GROUPS or (category and (group != 'Continuo' or category not in CATEGORIES)):
            raise ValueError('unknown board group/category')
        compiled.append((re.compile(rule.get('repo', '.*'), re.I),
                         re.compile(rule.get('match', '.*'), re.I), group, category))
    return compiled


def group_for(record, rules):
    repo = str(record.get('repo') or record.get('project') or record['id'])
    title = str(record.get('title') or '') + ' ' + str(record['id'])
    for repo_re, title_re, group, category in rules:
        if repo_re.search(repo) and title_re.search(title):
            if group == 'Continuo' and not category:
                category = next((cat for pattern, cat in [
                    ('multi.firm|programme|program', CATEGORIES[0]),
                    ('competitor|gap|feature', CATEGORIES[1]),
                    ('compensation|remuneration|payroll', CATEGORIES[2]),
                    ('onboard|legal|agreement|engagement', CATEGORIES[3])]
                    if re.search(pattern, title, re.I)), 'other')
            return group, category
    return 'Other', None


def task_view(task):
    current = task.get('current_state', {})
    state, detail = current.get('state', 'unknown'), plain(current.get('detail'))
    backlog = task.get('backlog') or {}
    decisions = task.get('hints', {}).get('open_decisions', [])
    url = (task.get('pr') or {}).get('url') or backlog.get('pr_url')
    if backlog.get('state') == 'done':
        return 'DONE', 'Done recently', 'Completed'
    if any(d.get('verb') == 'needs-decision' for d in decisions):
        return 'NEEDS ANSWER', SECTIONS[0], '; '.join(plain(d['summary']) for d in decisions if d.get('verb') == 'needs-decision')
    if backlog.get('captain_actionable') or backlog.get('hold_kind') == 'captain':
        return 'NEEDS ANSWER', SECTIONS[0], plain(backlog.get('hold_reason') or backlog.get('title'))
    if state == 'paused':
        return 'PAUSED', SECTIONS[3], detail or 'Waiting for external work; trigger not recorded'
    if state in ('blocked', 'failed') or any(d.get('verb') == 'blocked' for d in decisions):
        return 'BLOCKED', SECTIONS[0], detail or 'Help the worker unblock'
    if state == 'parked':
        return 'NEEDS ANSWER', SECTIONS[0], detail or 'Review and approve the worker’s next step'
    if state == 'done' and url:
        if re.search(r'checks green|checks.passed|CI checks passed', detail, re.I):
            release_note = detail + ' ' + plain(task.get('paths', {}).get('status_log', {}).get('last_event', {}).get('note'))
            apply = re.search(r'apply.*(?:database|migration)|(?:database|migration).*(?:must|needs?|awaiting|pending|before merge|to be applied)', release_note, re.I) and not re.search(r'already applied|has been applied', release_note, re.I)
            return ('READY: apply + merge?' if apply else 'READY: merge?', SECTIONS[0],
                    'Apply the production database change, then give the merge word' if apply else 'Give the merge word')
        return 'NEEDS ANSWER', SECTIONS[0], 'Review the PR; green checks have not been confirmed'
    if state == 'done':
        return 'DONE', 'Done recently', detail or 'Completed'
    if state == 'working':
        return ('CI RUNNING' if re.search(r'\bci (running|pending)\b', detail, re.I) else 'WORKING', SECTIONS[1], detail or 'Worker running')
    return 'BLOCKED', SECTIONS[0], 'Check worker state: ' + (detail or 'current state unavailable')


def recent(record, now):
    date = (record.get('completion') or {}).get('date')
    try:
        return 0 <= (now.date() - dt.date.fromisoformat(date[:10])).days <= 2
    except (TypeError, ValueError):
        return False


def project(snapshot, rules):
    now = dt.datetime.fromisoformat(snapshot['generated'].replace('Z', '+00:00'))
    rows, labels, seen = [], [], set()
    records = snapshot.get('backlog', {}).get('records', [])
    tasks = snapshot.get('tasks', [])
    owners = {row['id']: row for row in snapshot.get('secondmate_current', {}).get('records', [])}
    # Child state is supplied only by the canonical secondmate roll-up; no home
    # traversal, remote call, or terminal read is introduced by the board.
    for task in tasks:
        if task.get('kind') == 'secondmate':
            owner = owners.get(task['id'], {})
            label = {'active_child_work': 'WORKING', 'captain_decision': 'NEEDS ANSWER',
                     'externally_held': 'PAUSED', 'no_active_work': 'DONE'}.get(owner.get('current', {}).get('state'), 'BLOCKED')
            if any(row.get('state') in ('blocked', 'failed') for row in owner.get('endpoints', [])):
                label = 'BLOCKED'
            if task.get('backend') == 'herdr' and not task.get('remote') and task.get('endpoint', {}).get('exists') is True:
                labels.append(dict(id=task['id'], target=task['endpoint']['target'], label=label))
            continue
        record = dict(task.get('backlog') or {})
        record.update(id=task['id'], project=task.get('project'))
        record.setdefault('title', task['id'])
        label, section, action = task_view(task)
        if task.get('backend') == 'herdr' and not task.get('remote') and task.get('endpoint', {}).get('exists') is True:
            labels.append(dict(id=task['id'], target=task['endpoint']['target'], label=label))
        if section == SECTIONS[2] and not recent(record, now):
            seen.add(task['id'])
            continue
        url = (task.get('pr') or {}).get('url') or record.get('pr_url')
        rows.append((record, section, action, url))
        seen.add(task['id'])
    for record in records:
        if not record.get('structured') or record['id'] in seen:
            continue
        state = record.get('state')
        if state == 'done':
            if not recent(record, now):
                continue
            section, action = SECTIONS[2], 'Completed'
        elif record.get('captain_actionable'):
            section, action = SECTIONS[0], plain(record.get('hold_reason') or record.get('title'))
        elif state == 'in_flight' and record.get('current_role') == 'worker':
            section, action = SECTIONS[0], 'Check worker: no current endpoint recorded'
        else:
            section = SECTIONS[3]
            trigger = record.get('hold_reason') or record.get('blocked_reason')
            blockers = record.get('unresolved_blocker_ids', [])
            action = plain(trigger) if trigger else ('After ' + ', '.join(blockers) if blockers else 'When you choose to start this')
        rows.append((record, section, action, record.get('pr_url')))
    for owner in owners.values():
        recorded = set()
        for surface, section in [('decisions_open', SECTIONS[0]), ('active_children', SECTIONS[1]), ('holds', SECTIONS[3]), ('queued', SECTIONS[3])]:
            for item in owner.get(surface, []):
                if item['id'] in recorded:
                    continue
                recorded.add(item['id'])
                record = dict(item, id=owner['id'] + '/' + item['id'])
                selected = SECTIONS[0] if item.get('captain_actionable') else section
                action = plain(item.get('reason') or item.get('summary') or item.get('doing') or item.get('hold_reason') or item.get('blocked_reason'))
                if not action:
                    blockers = item.get('unresolved_blocker_ids', [])
                    action = 'After ' + ', '.join(blockers) if blockers else 'When you choose to start this'
                rows.append((record, selected, action, item.get('pr_url')))
        # The summary already carries current endpoint state. Preserve PR review
        # and failure actions for terminal children still awaiting delivery.
        for item in owner.get('endpoints', []):
            if item['id'] in recorded or item.get('state') not in ('done', 'failed'):
                continue
            task = dict(id=item['id'], current_state=dict(state=item['state'], detail=item.get('detail')), pr=dict(url=item.get('pr_url')))
            _, section, action = task_view(task)
            if section != SECTIONS[2]:
                rows.append((dict(item, id=owner['id'] + '/' + item['id']), section, action, item.get('pr_url')))
        if owner.get('freshness', {}).get('status') not in (None, 'fresh') or owner.get('omitted') or owner.get('provenance', {}).get('summary_valid') is False:
            rows.append((dict(id=owner['id'], title='Worker status'), SECTIONS[0], 'Check worker: summary incomplete or unavailable', None))
    if snapshot.get('secondmate_current', {}).get('registry', {}).get('available') is False:
        rows.append((dict(id='registered-workers', title='Registered workers'), SECTIONS[0], 'Check worker list: it could not be read', None))
    for record in snapshot.get('secondmate_landed', {}).get('records', []):
        if recent(record, now):
            rows.append((record, SECTIONS[2], 'Completed', record.get('pr_url')))
    grouped = {group: {} for group in GROUPS}
    for record, section, action, url in rows:
        group, category = group_for(record, rules)
        title = plain(record.get('title') or record['id'])
        line = f'- {title} - {plain(action)}'
        urls = [url] + [link for link in record.get('links', []) if re.search(r'/(?:pull/|-/merge_requests/)\d+', link)]
        for link in dict.fromkeys(link for link in urls if link and str(link).startswith('https://')):
            line += '\n  ' + str(link)
        grouped[group].setdefault((category, section), []).append(line)
    lines = ['# Crew board', '', 'CLIENT / INITIATIVE REPO', '']
    for group in GROUPS:
        lines += ['## ' + group]
        content = grouped[group]
        if not content:
            lines += ['No current work.', '']
            continue
        cats = CATEGORIES if group == 'Continuo' else [None]
        for cat in cats:
            if cat and not any(key[0] == cat for key in content):
                continue
            if cat:
                lines.append('### ' + cat)
            for section in SECTIONS:
                entries = content.get((cat, section), [])
                if entries:
                    lines += [('#### ' if cat else '### ') + section] + list(dict.fromkeys(entries))
        lines.append('')
    # Disclose canonical inventory gaps without claiming an empty, healthy board.
    if snapshot.get('main_inventory', {}).get('valid') is False:
        lines += ['Worker records need attention; check the fleet status report.', '']
    return '\n'.join(lines) + '\n', labels


def write_board(path, generated):
    before = path.read_text() if path.exists() else ''
    notes = before[before.index(NOTES):] if NOTES in before else NOTES
    new = generated + notes
    if new == before:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
        tmp = Path(out.name)
        out.write(new)
    try:
        if (path.read_text() if path.exists() else '') != before:
            raise RuntimeError('board changed while rendering; retry to preserve notes')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def herdr(session, *args):
    result = subprocess.run(['herdr', *args, '--session', session],
                            text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError('Herdr ' + ' '.join(args[:2]) + ': ' + (result.stderr or result.stdout).strip())
    if args[:2] in [('pane', 'run'), ('pane', 'send-text'), ('pane', 'send-keys'), ('pane', 'wait-output')]:
        return {}
    return json.loads(result.stdout) if result.stdout.strip() else {}


def update_panes(home, labels):
    session = os.environ.get('HERDR_SESSION', 'default')
    parent = os.environ['FM_BOARD_PARENT_PANE']
    tab = os.environ['FM_BOARD_PARENT_TAB']
    workspace = os.environ['FM_BOARD_PARENT_WORKSPACE']
    for item in labels:
        target_session, pane = item['target'].split(':', 1)
        # Remote homes and other sessions are never implicitly adopted.
        if target_session != session or pane == parent:
            continue
        info = herdr(session, 'pane', 'get', pane)['result']['pane']
        title = item['id'] + ' - ' + item['label']
        if info.get('label') != title:
            herdr(session, 'pane', 'rename', pane, title)
    title = 'Firstmate board ' + hashlib.sha256(str(home).encode()).hexdigest()[:12]
    panes = herdr(session, 'pane', 'list')['result']['panes']
    matches = [p for p in panes if p.get('label') == title]
    if len(matches) > 1:
        raise RuntimeError('multiple board panes; refusing another split')
    if matches:
        if matches[0].get('tab_id') != tab or matches[0].get('workspace_id') != workspace:
            raise RuntimeError('board is in another tab; refusing another split')
        return
    result = herdr(session, 'pane', 'split', parent, '--direction', 'right',
                   '--ratio', '0.5', '--cwd', str(home), '--no-focus')['result']
    pane = result['pane']['pane_id']
    # Name before launching, so a retry never creates another pane after a
    # partial launch failure. Never type into an existing/possibly occupied pane.
    herdr(session, 'pane', 'rename', pane, title)
    env = {key: os.environ[key] for key in ('HERDR_SESSION', 'HERDR_SOCKET_PATH', 'HERDR_PANE_ID') if key in os.environ}
    env.update(FM_HOME=str(home), FM_BACKEND='herdr', FM_BOARD_DISABLED='0', PATH=os.environ['PATH'])
    launch = home / 'state/board-viewer.sh'
    with tempfile.NamedTemporaryFile(mode='w', dir=launch.parent, delete=False) as out:
        temporary = Path(out.name)
        os.fchmod(out.fileno(), 0o700)
        out.write('#!/bin/sh\nexec ' + shlex.join(['env', *[key + '=' + value for key, value in env.items()], str(SCRIPT / 'fm-board.sh'), 'view']) + '\n')
    try:
        temporary.replace(launch)
    finally:
        temporary.unlink(missing_ok=True)
    command = 'exec ' + shlex.quote(str(launch))
    if len(command.encode()) > 512:
        raise RuntimeError('board launcher path exceeds the safe terminal input length')
    # A newly split terminal can still be in canonical mode while shell startup
    # runs. Wait for an actual prompt, not a fixed delay, before submitting.
    herdr(session, 'pane', 'wait-output', pane, '--regex',
          r'(?m)[^\n]*[#$%>❯➜][ \t]*$', '--lines', '8', '--timeout', '5000')
    herdr(session, 'pane', 'run', pane, command)


def fingerprint(home):
    paths = [home / 'data/backlog.md', home / 'config/board-groups.json']
    for pattern in ('*.meta', '*.status', '*.busy*'):
        paths += sorted((home / 'state').glob(pattern))
    digest = hashlib.sha256()
    for path in paths:
        if path.is_file():
            digest.update(str(path).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def refresh(args, home):
    state = home / 'state'
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'board.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        signature = fingerprint(home)
        cache = state / 'board-refresh.json'
        if args.command == 'refresh' and not args.snapshot and cache.exists():
            prior = json.loads(cache.read_text())
            if prior.get('signature') == signature and time.time() - prior.get('time', 0) < 30:
                return
        rules = mapping(home)
        snapshot = json.loads(Path(args.snapshot).read_text()) if args.snapshot else json.loads(subprocess.check_output([str(SCRIPT / 'fm-fleet-snapshot.sh'), '--json'], text=True, timeout=180))
        if snapshot.get('schema') != 'fm-fleet-snapshot.v1':
            raise ValueError('expected fm-fleet-snapshot.v1')
        generated, labels = project(snapshot, rules)
        write_board(home / 'data/board.md', generated)
        if args.command == 'refresh':
            update_panes(home, labels)
            cache.write_text(json.dumps(dict(signature=signature, time=time.time())))


def view(home):
    def display(screen):
        curses.use_default_colors()
        for index, color in enumerate((curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_GREEN, curses.COLOR_MAGENTA), 1):
            curses.init_pair(index, color, -1)
        screen.timeout(1000)
        offset, refreshed = 0, 0
        process = None
        while True:
            if time.monotonic() - refreshed > 5 and (process is None or process.poll() is not None):
                process = subprocess.Popen([str(SCRIPT / 'fm-board.sh'), 'refresh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                refreshed = time.monotonic()
            height, width = screen.getmaxyx()
            path = home / 'data/board.md'
            text = path.read_text() if path.exists() else 'Waiting for board...'
            lines = []
            for line in text.splitlines():
                color = (2 if 'Waiting on the captain' in line else 3 if 'Done recently' in line else 4 if 'Parked with' in line else 1 if line.startswith('#') else 0)
                for wrapped in textwrap.wrap(line, max(1, width - 2), replace_whitespace=False) or ['']:
                    lines.append((wrapped, color))
            offset = max(0, min(offset, max(0, len(lines) - height + 1)))
            screen.erase()
            for row, (line, color) in enumerate(lines[offset:offset + height - 1]):
                try:
                    screen.addnstr(row, 0, line, max(0, width - 1), curses.color_pair(color))
                except curses.error:
                    pass
            try:
                screen.addnstr(height - 1, 0, 'j/k scroll | PgUp/PgDn | q exit', max(0, width - 1), curses.A_DIM)
            except curses.error:
                pass
            screen.noutrefresh()
            curses.doupdate()
            key = screen.getch()
            if key == ord('q'):
                return
            if key in (ord('j'), curses.KEY_DOWN):
                offset += 1
            elif key in (ord('k'), curses.KEY_UP):
                offset -= 1
            elif key == curses.KEY_NPAGE:
                offset += max(1, height - 2)
            elif key == curses.KEY_PPAGE:
                offset -= max(1, height - 2)
            elif key == curses.KEY_HOME:
                offset = 0
            elif key == curses.KEY_END:
                offset = len(lines)
    curses.wrapper(display)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['refresh', 'render', 'view'])
    parser.add_argument('--snapshot')
    args = parser.parse_args()
    home = Path(os.environ['FM_HOME']).resolve()
    if args.command == 'view':
        view(home)
    else:
        refresh(args, home)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print('fm-board: ' + str(exc), file=sys.stderr)
        sys.exit(1)
