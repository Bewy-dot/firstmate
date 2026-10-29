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
SECTIONS = ['NEEDS YOU NOW', 'Running', 'Done recently', 'PARKED',
            'QUEUED DECISIONS']
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


def short(value, limit=54):
    value = plain(value)
    return value if len(value) <= limit else value[:limit - 3].rstrip() + '...'


def task_view(task, titles=None):
    current = task.get('current_state', {})
    state, detail = current.get('state', 'unknown'), plain(current.get('detail'))
    backlog = task.get('backlog') or {}
    decisions = task.get('hints', {}).get('open_decisions', [])
    url = (task.get('pr') or {}).get('url') or backlog.get('pr_url')
    if backlog.get('state') == 'done':
        return 'DONE', SECTIONS[2], 'Completed'
    questions = [d for d in decisions if d.get('verb') == 'needs-decision']
    green = state == 'done' and url and re.search(r'checks green|checks.passed|CI checks passed', detail, re.I)
    merge_word = r"merge (?:word|approval)|(?:approval|permission) to merge|ready (?:to|for) merge|^merge(?: (?:the )?PR\b|\?)"
    delivery_only = questions and all(re.search(merge_word, plain(d.get('summary')), re.I) for d in questions)
    if questions and not (green and delivery_only):
        return 'NEEDS ANSWER', SECTIONS[0], '; '.join(plain(d['summary']) for d in questions)
    if state == 'failed':
        return 'BLOCKED', SECTIONS[0], detail or 'Help the worker unblock'
    if state == 'done' and url:
        if green:
            release_note = detail + ' ' + plain(task.get('paths', {}).get('status_log', {}).get('last_event', {}).get('note'))
            apply = re.search(r'apply.*(?:database|migration)|(?:database|migration).*(?:must|needs?|awaiting|pending|before merge|to be applied)', release_note, re.I) and not re.search(r'already applied|has been applied', release_note, re.I)
            return ('READY: apply + merge?' if apply else 'READY: merge?', SECTIONS[0],
                    'Apply the production database change, then give the merge word' if apply else 'Give the merge word')
        return 'REVIEW', SECTIONS[0], 'Review the PR; green checks have not been confirmed'
    blockers = backlog.get('unresolved_blocker_ids') or []
    if blockers:
        names = ', '.join((titles or {}).get(key, key) for key in blockers)
        return 'WAITING: ' + short(names, 48), SECTIONS[1], 'Waiting on ' + names
    if backlog.get('hold_kind') == 'captain' or state == 'parked':
        return 'HOLDING', SECTIONS[0], plain(backlog.get('hold_reason')) or detail or 'Awaiting your word to continue'
    if state == 'paused':
        return 'PAUSED', SECTIONS[3], detail or 'Waiting for external work; trigger not recorded'
    if state in ('blocked', 'failed') or any(d.get('verb') == 'blocked' for d in decisions):
        return 'BLOCKED', SECTIONS[0], detail or 'Help the worker unblock'
    if state == 'done':
        return 'DONE', SECTIONS[2], detail or 'Completed'
    if state == 'working':
        return ('CI RUNNING' if re.search(r'\bci (running|pending)\b', detail, re.I) else 'WORKING', SECTIONS[1], detail or 'Worker running')
    return 'BLOCKED', SECTIONS[0], 'Check worker state: ' + (detail or 'current state unavailable')


def backlog_view(record):
    if record.get('state') == 'done':
        return 'DONE', SECTIONS[2], 'Completed'
    blockers = record.get('unresolved_blocker_ids') or []
    reason = plain(record.get('hold_reason') or record.get('blocked_reason') or record.get('reason'))
    # Eligibility is not evidence that this filed decision has been asked.
    if record.get('kind') == 'captain' or record.get('hold_kind') == 'captain' or record.get('captain_actionable') or record.get('verb') == 'captain-hold':
        return 'QUEUED', SECTIONS[4], reason or 'Filed; not yet asked'
    if blockers:
        return 'WAITING', SECTIONS[3], 'After ' + ', '.join(blockers)
    if record.get('current_role') == 'program' or record.get('kind') == 'program':
        return 'PROGRAMME', SECTIONS[3], reason or 'Continue when its linked work is ready'
    if record.get('state') == 'in_flight' and record.get('current_role') == 'worker':
        return 'CHECK', SECTIONS[0], 'Check worker: no current endpoint recorded'
    return 'PARKED', SECTIONS[3], reason or 'When you choose to start this'


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
    titles = {r['id']: plain(r.get('title') or r['id']) for r in records if r.get('id')}
    owners = {r['id']: r for r in snapshot.get('secondmate_current', {}).get('records', [])}
    for task in snapshot.get('tasks', []):
        if task.get('kind') == 'secondmate':
            owners.setdefault(task['id'], dict(id=task['id'], freshness=dict(status='unavailable')))

    def add(record, label, section, action, url=None, scope=''):
        rows.append(dict(record=record, label=label, section=section, action=action,
                         url=url or record.get('pr_url'), scope=scope))

    for task in snapshot.get('tasks', []):
        if task.get('kind') == 'secondmate':
            continue  # Derive the owner's label from its reconciled children below.
        record = dict(task.get('backlog') or {})
        record.update(id=task['id'], project=task.get('project'))
        record.setdefault('title', task['id'])
        label, section, action = task_view(task, titles)
        if task.get('backend') == 'herdr' and not task.get('remote') and task.get('endpoint', {}).get('exists') is True:
            labels.append(dict(id=task['id'], target=task['endpoint']['target'], label=label))
        add(record, label, section, action, (task.get('pr') or {}).get('url'))
        seen.add(task['id'])
    for record in records:
        if record.get('structured') and record['id'] not in seen:
            add(record, *backlog_view(record))
    for owner in owners.values():
        # Merge overlapping summary surfaces by identity before classifying.
        children = {}
        for surface in ('queued', 'holds', 'active_children', 'decisions_open', 'endpoints'):
            for item in owner.get(surface, []):
                child = children.setdefault(item['id'], dict(record={}, decisions=[]))
                child['record'].update({k: v for k, v in item.items() if v is not None
                                         and not (surface == 'endpoints' and k == 'state')})
                if surface == 'decisions_open' and (item.get('source') == 'status' or item.get('verb') in ('needs-decision', 'blocked')):
                    child['decisions'].append(item)
                if surface == 'endpoints':
                    child['endpoint'] = item
                if surface == 'active_children':
                    child['active'] = item
                if surface == 'holds' and item.get('source') == 'child-state':
                    child['held'] = item
        owner_labels = []
        child_titles = {key: plain(c['record'].get('title') or key) for key, c in children.items()}
        for child in children.values():
            record = child['record']
            endpoint = child.get('endpoint')
            if endpoint or child.get('active') or child.get('held') or child['decisions']:
                current = endpoint or child.get('active') or child.get('held') or {}
                state = current.get('state') or ('parked' if child.get('held') else 'working')
                task = dict(backlog=record, current_state=dict(state=state,
                            detail=current.get('detail') or current.get('doing') or current.get('reason')),
                            hints=dict(open_decisions=child['decisions']), pr=dict(url=record.get('pr_url')))
                label, section, action = task_view(task, child_titles)
            else:
                label, section, action = backlog_view(record)
            owner_labels.append(label)
            add(record, label, section, action, scope=owner['id'])
        incomplete = (owner.get('freshness', {}).get('status') not in (None, 'fresh')
                      or owner.get('omitted') or owner.get('provenance', {}).get('summary_valid') is False
                      or owner.get('current', {}).get('state') == 'unknown')
        if incomplete:
            add(dict(id=owner['id'], title='Worker status'), 'CHECK', SECTIONS[0],
                'Check worker: summary incomplete or unavailable')
        # A broad owner state cannot distinguish a question, merge, or designed
        # hold. Use the same child classifier as the board, including precedence.
        priority = ('BLOCKED', 'CHECK', 'NEEDS ANSWER', 'READY:', 'REVIEW', 'HOLDING', 'WORKING', 'CI RUNNING', 'WAITING:', 'PAUSED')
        label = next((value for prefix in priority for value in owner_labels if value.startswith(prefix)), 'DONE')
        if incomplete or label == 'CHECK':
            label = 'BLOCKED'
        for task in snapshot.get('tasks', []):
            if task['id'] == owner['id'] and task.get('kind') == 'secondmate' and task.get('backend') == 'herdr' and not task.get('remote') and task.get('endpoint', {}).get('exists') is True:
                labels.append(dict(id=task['id'], target=task['endpoint']['target'], label=label))
    if snapshot.get('secondmate_current', {}).get('registry', {}).get('available') is False:
        add(dict(id='registered-workers', title='Registered workers'), 'CHECK', SECTIONS[0],
            'Check worker list: it could not be read')
    for record in snapshot.get('secondmate_landed', {}).get('records', []):
        if recent(record, now):
            add(record, 'DONE', SECTIONS[2], 'Completed', scope=record.get('owner', ''))

    # Keep older completed dependencies as context, without showing all history.
    referenced = {(r['scope'], key) for r in rows for key in r['record'].get('blocked_by_ids', [])}
    rows = [r for r in rows if r['section'] != SECTIONS[2] or recent(r['record'], now)
            or (r['scope'], r['record']['id']) in referenced]
    unique = {}
    for row in rows:
        unique.setdefault((row['scope'], row['record']['id']), row)
    rows = list(unique.values())
    for row in rows:
        if row['section'] == SECTIONS[2] and not recent(row['record'], now):
            row['section'] = 'Completed prerequisites'
    for row in rows:
        row['group'], row['category'] = group_for(row['record'], rules)
    full = ['# Crew board - full detail', '', '[Compact board](board.md)', '']
    for group in GROUPS:
        selected = [r for r in rows if r['group'] == group]
        if not selected:
            continue
        full.append('## ' + group)
        for section in SECTIONS + ['Completed prerequisites']:
            entries = [r for r in selected if r['section'] == section]
            if not entries:
                continue
            full.append('### ' + section)
            for row in entries:
                record = row['record']
                full.append('- ' + plain(record.get('title') or record['id']) +
                            ' [' + row['label'] + '] - ' + plain(row['action']))
                if row['category']:
                    full.append('  Programme: ' + row['category'])
                full.append('  Task: ' + (row['scope'] + '/' if row['scope'] else '') + record['id'])
                if record.get('blocked_by_ids'):
                    full.append('  After: ' + ', '.join(record['blocked_by_ids']))
                full.extend('  ' + url for url in pr_links(row))
        full.append('')
    counts = {section: sum(r['section'] == section for r in rows) for section in SECTIONS}
    compact = ['# Crew board', '',
               f"{counts[SECTIONS[0]]} need you | {counts[SECTIONS[1]]} running/waiting | "
               f"{counts[SECTIONS[4]]} decisions queued | {counts[SECTIONS[3]]} parked",
               '[Full detail](board-full.md)', '']
    components = chains(rows)
    programme_names = {}
    for component in components:
        programmes = [short(r['record'].get('title') or r['record']['id'], 45)
                      for r in component['rows'] if r['record'].get('kind') == 'program']
        if programmes:
            for row in component['rows']:
                programme_names[(row['scope'], row['record']['id'])] = ', '.join(programmes)
    for section, limit in ((SECTIONS[0], 5), (SECTIONS[1], 4)):
        compact.append('## ' + section)
        selected = [c for c in components if c['section'] == section]
        if not selected:
            compact.append('See the dependency groups above.' if section == SECTIONS[1] and counts[SECTIONS[1]] else 'None.')
        for component in selected[:limit]:
            compact.extend(component_lines(component))
        if len(selected) > limit:
            compact.append(f'+{len(selected) - limit} more groups in board-full.md')
        compact.append('')
    for section in (SECTIONS[4], SECTIONS[3]):
        compact.append('## ' + section)
        selected = [r for r in rows if r['section'] == section]
        programmes = {}
        for row in selected:
            name = programme_names.get((row['scope'], row['record']['id']), row['category'])
            programme = row['group'] + (' / ' + name if name else '')
            programmes[programme] = programmes.get(programme, 0) + 1
        for programme, count in list(programmes.items())[:5]:
            compact.append(f'- {programme}: {count} ' + ('decisions filed; not yet asked' if section == SECTIONS[4] else 'parked; triggers in full detail'))
        if len(programmes) > 5:
            compact.append(f'+{sum(list(programmes.values())[5:])} more in board-full.md')
        if not selected:
            compact.append('None.')
        compact.append('')
    compact.append(f"Done recently: {counts[SECTIONS[2]]}. Details in board-full.md.")
    if snapshot.get('main_inventory', {}).get('valid') is False:
        compact.append('Worker records need attention; check the fleet status report.')
        full.append('Worker records need attention; check the fleet status report.')
    return '\n'.join(compact) + '\n\n', '\n'.join(full) + '\n', labels


def pr_links(row):
    return list(dict.fromkeys(str(url) for url in
        [row['url']] + (row['record'].get('links') or [])
        if url and str(url).startswith('https://') and re.search(r'/(?:pull/|-/merge_requests/)\d+', str(url))))


def chains(rows):
    """Connected dependencies, ordered prerequisites first, scoped to one home.

    Project/category supplies programme context; only canonical blocker ids add
    edges. Missing prerequisites stay explicit in the waiting reason.
    """
    by_id = {(r['scope'], r['record']['id']): r for r in rows}
    edges = {key: set() for key in by_id}
    for key, row in by_id.items():
        for dep in row['record'].get('blocked_by_ids') or []:
            other = (row['scope'], dep)
            if other in edges:
                edges[key].add(other)
                edges[other].add(key)
    result, seen = [], set()
    for key in by_id:
        if key in seen:
            continue
        pending, keys = [key], []
        while pending:
            node = pending.pop()
            if node in seen:
                continue
            seen.add(node)
            keys.append(node)
            pending.extend(sorted(edges[node] - seen, reverse=True))
        ordered, remaining, cycle = [], set(keys), False
        while remaining:
            ready = [k for k in keys if k in remaining and not any(
                (by_id[k]['scope'], dep) in remaining for dep in by_id[k]['record'].get('blocked_by_ids') or [])]
            if not ready:  # Cycles must remain visible, never hang the renderer.
                cycle = True
                ordered.extend(by_id[k] for k in keys if k in remaining)
                break
            for node in ready:
                remaining.remove(node)
                ordered.append(by_id[node])
        section = next((section for section in (SECTIONS[0], SECTIONS[1], SECTIONS[4], SECTIONS[3], SECTIONS[2])
                        if any(r['section'] == section for r in ordered)), SECTIONS[3])
        result.append(dict(rows=ordered, section=section, cycle=cycle))
    return result


def component_lines(component):
    all_rows = component['rows']
    rows = [r for r in all_rows if r['section'] != SECTIONS[4]]
    queued = len(all_rows) - len(rows)
    group = rows[0]['group']

    def link(row):
        return short(row['record'].get('title') or row['record']['id'], 30) + ' [' + (
            row['label'] if not row['label'].startswith('WAITING:') else 'WAITING') + ']'

    linear = not component['cycle'] and all(
        before['record']['id'] in (after['record'].get('blocked_by_ids') or [])
        for before, after in zip(rows, rows[1:]))
    if linear:
        links = [link(row) for row in rows[:4]]
        separator = ' -> '
    else:
        names = {row['record']['id']: short(row['record'].get('title') or row['record']['id'], 24) for row in rows}
        links = []
        for row in rows[:4]:
            deps = row['record'].get('blocked_by_ids') or []
            links.append(link(row) + (' after ' + ', '.join(names.get(dep, short(dep, 24)) for dep in deps) if deps else ''))
        separator = '; '
    if len(rows) > 4:
        links.append('+' + str(len(rows) - 4) + ' steps in full detail')
    waits = [r for r in rows if r['section'] == SECTIONS[0]] or [r for r in rows if r['label'].startswith('WAITING')]
    action = '; '.join(dict.fromkeys(plain(r['action']) for r in waits)) or plain(rows[0]['action'])
    if component['cycle']:
        action = 'Check dependency cycle before continuing'
    elif len(rows) > 1:
        action = short(action, 75) + '; then continue in dependency order'
    lines = ['- ' + group + ': ' + separator.join(links) +
             (f' (+{queued} queued decisions)' if queued else ''), '  ' + short(action, 115)]
    urls = list(dict.fromkeys(url for row in all_rows for url in pr_links(row)))
    lines.extend('  ' + url for url in urls[:1])
    if len(urls) > 1:
        lines[-1] += f' (+{len(urls) - 1} PRs in board-full.md)'
    return lines


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
        generated, full, labels = project(snapshot, rules)
        write_board(home / 'data/board-full.md', full)
        write_board(home / 'data/board.md', generated)
        if args.command == 'refresh':
            update_panes(home, labels)
            cache.write_text(json.dumps(dict(signature=signature, time=time.time())))


def view(home):
    def display(screen):
        curses.curs_set(0)
        curses.use_default_colors()
        for index, color in enumerate((curses.COLOR_CYAN, curses.COLOR_YELLOW, curses.COLOR_GREEN, curses.COLOR_MAGENTA), 1):
            curses.init_pair(index, color, -1)
        screen.timeout(1000)
        offset, refreshed, detailed = 0, 0, False
        process = None

        def draw(y, x, text, width, style=0):
            try:
                screen.addnstr(y, x, text, max(0, width), style)
            except curses.error:
                pass

        while True:
            if time.monotonic() - refreshed > 5 and (process is None or process.poll() is not None):
                process = subprocess.Popen([str(SCRIPT / 'fm-board.sh'), 'refresh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                refreshed = time.monotonic()
            height, width = screen.getmaxyx()
            path = home / ('data/board-full.md' if detailed else 'data/board.md')
            text = path.read_text() if path.exists() else 'Waiting for board...'
            lines = []
            for line in text.splitlines():
                if line.startswith('# Crew board'):
                    continue
                heading = line.startswith('#')
                color = (2 if 'NEEDS YOU NOW' in line else 3 if 'Done recently' in line else 4 if 'PARKED' in line or 'QUEUED DECISIONS' in line else 1 if heading else 0)
                line = line.lstrip('#').strip() if heading else line
                line = line.replace('[Full detail](board-full.md)', 'Full detail: data/board-full.md')
                line = line.replace('[Compact board](board.md)', 'Compact board: data/board.md')
                if line.startswith('- '):
                    line = '  ' + line[2:]
                for wrapped in textwrap.wrap(line, max(1, width - 6), replace_whitespace=False) or ['']:
                    lines.append((wrapped, curses.color_pair(color) | (curses.A_BOLD if heading else 0)))
            viewport = max(1, height - 4)
            offset = max(0, min(offset, max(0, len(lines) - viewport)))
            screen.erase()
            draw(0, 1, 'CREW BOARD  /  ' + ('Full detail' if detailed else 'At a glance'), width - 3,
                 curses.color_pair(1) | curses.A_BOLD)
            draw(1, 1, '─' * max(0, width - 3), width - 3, curses.A_DIM)
            for row, (line, style) in enumerate(lines[offset:offset + viewport]):
                draw(row + 2, 1, line, width - 4, style)
            # An always-visible track shows both position and viewport size.
            thumb = max(1, min(viewport, viewport * viewport // max(1, len(lines))))
            start = (viewport - thumb) * offset // max(1, len(lines) - viewport)
            for row in range(viewport):
                draw(row + 2, width - 2, '█' if start <= row < start + thumb else '│', 1,
                     curses.color_pair(1) if start <= row < start + thumb else curses.A_DIM)
            progress = f'{offset + 1}-{min(offset + viewport, len(lines))}/{len(lines)}'
            draw(height - 1, 1, 'j/k scroll  PgUp/Dn  f summary/detail  q exit  ' + progress,
                 width - 3, curses.A_DIM)
            screen.noutrefresh()
            curses.doupdate()
            key = screen.getch()
            if key == ord('q'):
                return
            if key == ord('f'):
                detailed, offset = not detailed, 0
            elif key in (ord('j'), curses.KEY_DOWN):
                offset += 1
            elif key in (ord('k'), curses.KEY_UP):
                offset -= 1
            elif key == curses.KEY_NPAGE:
                offset += viewport
            elif key == curses.KEY_PPAGE:
                offset -= viewport
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
