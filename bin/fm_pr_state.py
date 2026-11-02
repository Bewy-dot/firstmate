#!/usr/bin/env python3
"""Nonblocking board PR evidence, never task or merge authority.

Cache: state/board-pr-cache/<sha256(url)>.json, atomic private JSON records keyed
by exact canonical URL. Open PR TTL is 60 seconds; failures wait 120 seconds and closed PRs wait
300 seconds before retrying. GitHub aliases share one query per host per refresh.
A per-home, per-host rolling hour caps board spending at 1000 GraphQL points;
reported cost is charged, with a conservative reservation for unknown outcomes.
Rate-limit resets suspend fetching without replacing cached evidence. Stale entries are
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
ERROR_TTL = 120
BUDGET = 1000
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
            ttl = ERROR_TTL if record.get('error') else 300 if record.get('state') in ('MERGED', 'CLOSED') else TTL
            return dict(record, stale=bool(record.get('refresh_error')) or not 0 <= time.time() - record['fetched_at'] < ttl)
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
            raise RuntimeError('GitHub requires the batched refresh worker')
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


def github_query(entries):
    """Aliases isolate repositories and PRs; identities have already been validated."""
    fields = ['rateLimit { cost remaining resetAt }']
    for index, (_, _, project, number) in enumerate(entries):
        owner, repo = project.split('/')
        fields.append('p%d: repository(owner:%s,name:%s) { pullRequest(number:%d) {' %
                      (index, json.dumps(owner), json.dumps(repo), int(number)) +
                      ' state headRefOid commits(last:1) { nodes { commit { statusCheckRollup {' +
                      ' contexts(first:100) { pageInfo { hasNextPage } nodes {' +
                      ' ... on CheckRun { name status conclusion startedAt isRequired(pullRequestNumber:%s) }' % number +
                      ' ... on StatusContext { context state createdAt isRequired(pullRequestNumber:%s) }' % number +
                      ' } } } } } } } }')
    return 'query { ' + ' '.join(fields) + ' }'


def github_record(url, data):
    pr = data['pullRequest']
    record = dict(url=url, fetched_at=time.time(), live=True,
                  observed_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  state=pr['state'], head=pr['headRefOid'])
    if record['state'] not in ('OPEN', 'CLOSED', 'MERGED'):
        raise ValueError('unrecognized PR state')
    if record['state'] == 'OPEN':
        commit = pr['commits']['nodes'][-1]['commit']
        rollup = commit['statusCheckRollup']
        checks = []
        if rollup:
            contexts = rollup['contexts']
            if contexts['pageInfo']['hasNextPage']:
                raise ValueError('over 100 check contexts; required checks incomplete')
            for check in contexts['nodes']:
                if not check['isRequired']:
                    continue
                state = check.get('conclusion') or check.get('status') or check['state']
                bucket = ('pass' if state in ('SUCCESS', 'NEUTRAL', 'SKIPPED') else
                          'pending' if state in ('EXPECTED', 'PENDING', 'QUEUED', 'IN_PROGRESS', 'WAITING', 'REQUESTED')
                          else 'fail')
                checks.append(dict(name=check.get('name') or check['context'], state=state,
                                   bucket=bucket, startedAt=check.get('startedAt') or check.get('createdAt')))
        record['check_runs'] = checks
    return record


def defer(home, urls, reason, until=None):
    for url in urls:
        record = read(home, url) or dict(url=url, live=True, fetched_at=0, error=reason)
        record.pop('stale', None)
        record['refresh_error'] = reason
        record['retry_at'] = until or time.time() + ERROR_TTL
        atomic(cache_path(home, url), record)


def github_refresh(home, host, entries):
    directory = home / 'state/board-pr-cache'
    ledger_path = directory / ('github-' + hashlib.sha256(host.encode()).hexdigest() + '.json')
    now = time.time()
    try:
        ledger = json.loads(ledger_path.read_text())
    except (OSError, ValueError):
        ledger = {}
    charges = [row for row in ledger.get('charges', []) if row[0] > now - 3600]
    urls = [entry[0] for entry in entries]
    # Each alias has at most three bounded connections (1 commit, 100 contexts).
    # This deliberately over-reserves the GitHub connection-cost formula; failed
    # requests retain the reservation when the server cannot report actual cost.
    reserve = 1 + 3 * len(entries)
    if now < ledger.get('blocked_until', 0):
        defer(home, urls, ledger.get('reason', 'GitHub rate-limit backoff'), ledger['blocked_until'])
        return
    if sum(row[1] for row in charges) + reserve > BUDGET:
        defer(home, urls, 'GitHub board hourly GraphQL budget exhausted',
              min(row[0] for row in charges) + 3601 if charges else now + 3600)
        return
    charges.append([now, reserve])
    ledger = dict(charges=charges)
    atomic(ledger_path, ledger)  # reserve before the request, including worker crashes
    try:
        raw, stderr = call(['gh', 'api', '--hostname', host, 'graphql', '-f',
                            'query=' + github_query(entries)], accepted=(0, 1))
        try:
            response = json.loads(raw)
        except ValueError:
            if 'rate limit' in stderr.lower() or 'rate_limit' in stderr.lower():
                response = {}
            else:
                raise RuntimeError(stderr or 'invalid PR response') from None
        data = response.get('data') or {}
        rate = data.get('rateLimit')
        if rate and isinstance(rate.get('cost'), int) and rate['cost'] >= 0:
            charges[-1][1] = max(1, rate['cost'])
            atomic(ledger_path, ledger)
        errors = response.get('errors') or []
        reason = stderr + ' ' + ' '.join(error.get('message', '') for error in errors)
        if 'rate limit' in reason.lower() or 'rate_limit' in reason.lower() or (rate and rate.get('remaining') == 0):
            reset = now + 3600  # conservative fallback if REST is unavailable
            if rate and rate.get('resetAt'):
                reset = dt.datetime.fromisoformat(rate['resetAt'].replace('Z', '+00:00')).timestamp()
            else:
                try:
                    raw, _ = call(['gh', 'api', '--hostname', host, 'rate_limit'])
                    reset = float(json.loads(raw)['resources']['graphql']['reset'])
                except (OSError, RuntimeError, ValueError, KeyError, TypeError):
                    pass
            ledger.update(blocked_until=max(now + 60, reset + 1), reason='GitHub rate-limit backoff until ' +
                          dt.datetime.fromtimestamp(reset, dt.timezone.utc).isoformat())
            atomic(ledger_path, ledger)
            defer(home, urls, ledger['reason'], ledger['blocked_until'])
            return
        for index, (url, _, _, _) in enumerate(entries):
            alias = 'p' + str(index)
            try:
                alias_errors = [error['message'] for error in errors
                                if not error.get('path') or error['path'][0] == alias]
                if alias_errors:
                    raise RuntimeError('; '.join(alias_errors))
                record = github_record(url, data[alias])
                atomic(cache_path(home, url), record)
            except (ValueError, KeyError, TypeError, IndexError, RuntimeError) as exc:
                defer(home, [url], failure(str(exc), 'GitHub'))
    except (OSError, RuntimeError, ValueError) as exc:
        defer(home, urls, failure(str(exc), 'GitHub'))


def refresh(home, urls):
    """Run under the inherited home lock; recheck TTL after acquiring it."""
    groups, others = {}, []
    for url in urls:
        cached = read(home, url)
        if cached and not cached['stale']:
            continue
        # Failed refreshes preserve the observation time but have their own retry clock.
        if cached and time.time() < cached.get('retry_at', 0):
            continue
        try:
            forge, host, project, number = identity(url)
            if forge == 'github':
                groups.setdefault(host, []).append((url, host, project, number))
            else:
                others.append(url)
        except (OSError, RuntimeError, ValueError):
            others.append(url)
    for host, entries in groups.items():
        github_refresh(home, host, entries)
    def save(url):
        atomic(cache_path(home, url), fetch(url))
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        list(pool.map(save, others))
    atomic(home / 'state/board-pr-updated.json', dict(time=time.time()))


def request(home, urls):
    """Return immediately; the child owns the singleton lock until all fetches end."""
    due = []
    for url in sorted(set(urls)):
        cached = read(home, url)
        if not cached or (cached['stale'] and time.time() >= cached.get('retry_at', 0)):
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
        refresh(home, urls)

        # Reproject cached canonical records immediately, without waiting for a
        # supervision turn or another expensive inventory read.
        if os.environ.get('FM_BOARD_PARENT_PANE') and os.environ.get('FM_BOARD_DISABLED') != '1':
            subprocess.Popen([sys.executable, str(SCRIPT / 'fm-board.py'), 'refresh'],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
