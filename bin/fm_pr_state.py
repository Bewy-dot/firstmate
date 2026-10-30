#!/usr/bin/env python3
"""Nonblocking board PR evidence, never task or merge authority.

Cache: state/board-pr-cache/<sha256(url)>.json, atomic private JSON records keyed
by exact canonical URL. TTL is 60 seconds, including failures; stale entries are
returned immediately. One detached refresh per home holds refresh.lock and uses
at most four threads. Every CLI call has a four-second process-group timeout.
--refresh HOME REQUESTS LOCKFD is private worker entry, not an operator command.
GitHub uses gh's JSON interface because gh-axi exposes only formatted PR output.
GitLab uses the existing glab authentication and explicit URL host via api.
URL identity/validation stays owned by fm-pr-lib.sh, including self-hosted GitLab.
"""
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.parse import quote

SCRIPT = Path(__file__).resolve().parent
TTL = 60
TIMEOUT = 4
CONCURRENCY = 4


def atomic(path, value):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
        temporary = Path(out.name)
        json.dump(value, out)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cache_path(home, url):
    return home / 'state/board-pr-cache' / (hashlib.sha256(url.encode()).hexdigest() + '.json')


def read(home, url):
    try:
        record = json.loads(cache_path(home, url).read_text())
        if (record['url'] == url and isinstance(record['fetched_at'], (float, int))
                and math.isfinite(record['fetched_at']) and record.get('live') is True
                and (isinstance(record.get('error'), str) or record.get('state') in ('OPEN', 'MERGED', 'CLOSED'))):
            return dict(record, stale=not 0 <= time.time() - record['fetched_at'] < TTL)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def call(argv, accepted=(0,)):
    process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise RuntimeError('request timed out after 4s') from None
    if process.returncode not in accepted:
        raise RuntimeError(stderr.strip() or f'CLI exited {process.returncode}')
    return stdout, stderr


def identity(url):
    # Pass the URL as an argument, never shell text. Reuse the forge identity owner.
    out, _ = call(['bash', '-c', '. "$1"; fm_pr_url_parse "$2" || exit 2; '
                   'printf "%s\\n" "$FM_PR_PROVIDER" "$FM_PR_HOST" "$FM_PR_PATH" "$FM_PR_NUMBER"',
                   'pr-state', str(SCRIPT / 'fm-pr-lib.sh'), url])
    return out.splitlines()


def failure(reason, provider):
    lowered = reason.lower()
    if 'timed out' in lowered:
        return provider + ' request timed out after 4s'
    if any(token in lowered for token in ('403', '401', 'permission', 'forbidden', 'authentication', 'login', 'not logged')):
        return provider + ': no permission'
    if any(token in lowered for token in ('404', 'not found', 'could not resolve to a pull request', 'no pull requests found')):
        return provider + ': PR not found (or no permission)'
    if any(token in lowered for token in ('resolve host', 'unreachable', 'connection', 'network', 'dial tcp', 'tls', '502', '503', '504')):
        return provider + ' unreachable'
    # Preserve a concrete diagnostic while avoiding control sequences/multiline output.
    return provider + ': ' + ' '.join(reason.split())[:180]


def fetch(url):
    record = dict(url=url, fetched_at=time.time(), observed_at=dt.datetime.now(dt.timezone.utc).isoformat(), live=True)
    provider = 'Forge'
    try:
        forge, host, project, number = identity(url)
        provider = 'GitHub' if forge == 'github' else 'GitLab'
        if forge == 'github':
            raw, _ = call(['gh', 'pr', 'view', url, '--json', 'state,headRefOid'])
            data = json.loads(raw)
            record.update(state=data['state'].upper(), head=data['headRefOid'])
            if record['state'] not in ('OPEN', 'MERGED', 'CLOSED'):
                raise ValueError('unrecognized PR state')
            if record['state'] == 'OPEN':
                raw, stderr = call(['gh', 'pr', 'checks', url, '--required', '--json',
                                   'name,state,bucket,startedAt'], accepted=(0, 1, 8))
                if not raw.strip() and any(text in stderr.lower() for text in ('no required checks', 'no checks reported')):
                    checks = []
                elif raw.strip():
                    checks = json.loads(raw)
                else:
                    raise RuntimeError(stderr or 'empty required-check response')
                if not isinstance(checks, list):
                    raise ValueError('invalid required-check response')
                if not all(isinstance(check, dict) and check.get('bucket') in ('pass', 'fail', 'pending', 'skipping', 'cancel') for check in checks):
                    raise ValueError('invalid required-check result')
                record['check_runs'] = checks
        else:
            endpoint = 'projects/' + quote(project, safe='')
            raw, _ = call(['glab', 'api', '--hostname', host, endpoint + '/merge_requests/' + number])
            data = json.loads(raw)
            record.update(state=data['state'].upper(), head=(data.get('diff_refs') or {}).get('head_sha') or data.get('sha'))
            if record['state'] not in ('OPEN', 'OPENED', 'MERGED', 'CLOSED'):
                raise ValueError('unrecognized MR state')
            if record['state'] in ('OPEN', 'OPENED'):
                record['state'] = 'OPEN'
                pipeline = data.get('head_pipeline')
                record['check_runs'] = []
                if pipeline:
                    # Decode consecutive JSON arrays from paginated glab output.
                    raw, _ = call(['glab', 'api', '--hostname', host, '--paginate',
                                   endpoint + '/pipelines/' + str(int(pipeline['id'])) + '/jobs?per_page=100'])
                    jobs = []
                    remaining = raw.strip()
                    while remaining:
                        page, end = json.JSONDecoder().raw_decode(remaining)
                        jobs.extend(page)
                        remaining = remaining[end:].lstrip()
                    # An allowed failure is not a required check; manual optional jobs are excluded.
                    record['check_runs'] = [dict(name=job['name'], state=job['status'].upper(), startedAt=job.get('started_at'))
                                            for job in jobs if not job.get('allow_failure')]
                    states = {check['state'] for check in record['check_runs']}
                    waiting = {'RUNNING', 'PENDING', 'CREATED', 'PREPARING', 'WAITING_FOR_RESOURCE', 'MANUAL'}
                    failed = {'FAILED', 'CANCELED', 'CANCELLED'}
                    pipeline_state = str(pipeline.get('status') or '').upper()
                    if ((pipeline_state in waiting and not states.intersection(waiting))
                            or (pipeline_state in failed and not states.intersection(failed))):
                        record['check_runs'].append(dict(name='pipeline', state=pipeline_state,
                                                         startedAt=pipeline.get('created_at')))
    except FileNotFoundError as exc:
        record['error'] = provider + ': ' + Path(exc.filename).name + ' CLI not installed'
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        record['error'] = provider + ': invalid PR response (' + str(exc)[:80] + ')'
    except (OSError, RuntimeError) as exc:
        record['error'] = failure(str(exc), provider)
    return record


def request(home, urls):
    """Return immediately; the child owns the singleton lock until all fetches end."""
    due = []
    for url in sorted(set(urls)):
        cached = read(home, url)
        if not cached or cached['stale']:
            due.append(url)
    if not due:
        return
    directory = home / 'state/board-pr-cache'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / 'refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        requests = directory / 'requests.json'
        atomic(requests, due)
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--refresh', str(home), str(requests), str(lock.fileno())],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         pass_fds=(lock.fileno(),), start_new_session=True)


def enrich(home, snapshots):
    urls = []
    for snapshot in snapshots:
        for task in snapshot.get('tasks', []):
            pr = task.get('pr') or {}
            url = pr.get('url') or (task.get('backlog') or {}).get('pr_url')
            urls.extend(pr.get('references') or [])
            if url:
                urls.append(url)
                cached = read(home, url)
                task['pr'] = dict(pr, **(cached or dict(url=url, pending_fetch=True)))
                task['pr']['related'] = [read(home, ref) or dict(url=ref, pending_fetch=True)
                                         for ref in pr.get('references', []) if ref != url]
        # Bounded remote summaries still need forge evidence when no local home is readable.
        for owner in snapshot.get('secondmate_current', {}).get('records', []):
            for endpoint in owner.get('endpoints', []):
                url = endpoint.get('pr_url')
                if url:
                    urls.append(url)
                    endpoint['pr'] = read(home, url) or dict(url=url, pending_fetch=True)
    request(home, urls)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != '--refresh':
        sys.exit('fm_pr_state: private worker entry only')
    home, requests = Path(sys.argv[2]), Path(sys.argv[3])
    with os.fdopen(int(sys.argv[4])):
        urls = json.loads(requests.read_text())
        def save(url):
            atomic(cache_path(home, url), fetch(url))
            # Fingerprint includes this marker so the next viewer cycle publishes promptly.
            atomic(home / 'state/board-pr-updated.json', dict(time=time.time()))
        with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
            list(pool.map(save, urls))
