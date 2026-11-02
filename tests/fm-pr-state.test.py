#!/usr/bin/env python3
"""Board public-interface regressions with temporary homes and fake forge CLIs."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import datetime, json, os, pathlib, re, sys, time
args=sys.argv[1:]
log=pathlib.Path(os.environ['CALLS'])
with log.open('a') as out: out.write(json.dumps(dict(kind='start',pid=os.getpid(),at=time.time(),args=args))+'\n')
try:
    time.sleep(float(os.environ.get('DELAY', '.03')))
    if 'graphql' in args:
        query=args[args.index('-f')+1]
        mode=os.environ.get('GH_MODE', '')
        if mode=='limit':
            print('GraphQL API rate limit exceeded',file=sys.stderr); sys.exit(1)
        data={'rateLimit':dict(cost=int(os.environ.get('GH_COST','1')),remaining=4000,resetAt='2099-01-01T00:00:00Z')}
        errors=[]
        changed=pathlib.Path(os.environ['CHANGE']).exists()
        for alias, number in re.findall(r'(p\d+): repository.*?pullRequest\(number:(\d+)\)', query):
            number=int(number)
            reasons={7:'error connecting to api.github.com: connection refused',8:'HTTP 403: permission denied',
                     9:'HTTP 404: Not Found',10:'request timed out after 4s',11:'invalid PR response'}
            if number in reasons:
                errors.append(dict(message=reasons[number],path=[alias])); data[alias]=None; continue
            state={2:'IN_PROGRESS',3:'FAILURE'}.get(number,'FAILURE' if changed else 'SUCCESS')
            nodes=[] if number==6 else [dict(name='tests',status='COMPLETED',conclusion=state,startedAt='2026-10-30T00:00:00Z',isRequired=True),
                dict(context='lint',state='SUCCESS',createdAt=None,isRequired=True),
                dict(name='optional',status='COMPLETED',conclusion='FAILURE',isRequired=False)]
            data[alias]=dict(pullRequest=dict(state={4:'MERGED',5:'CLOSED'}.get(number,'OPEN'),headRefOid='live-head',
                commits=dict(nodes=[dict(commit=dict(statusCheckRollup=dict(contexts=dict(nodes=nodes,pageInfo=dict(hasNextPage=mode=='overflow')))))])))
        print(json.dumps(dict(data=data,errors=errors))); sys.exit(0)
    if 'rate_limit' in args:
        print(json.dumps(dict(resources=dict(graphql=dict(reset=float(os.environ['RESET'])))))); sys.exit(0)
    lab='--hostname' in args
    if lab:
        number=int(re.search(r'(?:merge_requests|pipelines)/(\d+)', ' '.join(args))[1])
    else:
        number=int(re.search(r'/pull/(\d+)', ' '.join(args))[1])
    reason={7:'error connecting to api.github.com: connection refused',8:'HTTP 403: permission denied',9:'HTTP 404: Not Found'}
    if number in reason:
        print(reason[number],file=sys.stderr); sys.exit(1)
    if number==10: time.sleep(20)
    if number==11:
        print('not JSON'); sys.exit(0)
    changed=pathlib.Path(os.environ['CHANGE']).exists()
    if lab and '/jobs' in ' '.join(args):
        status={2:'running',3:'failed'}.get(number, 'success')
        print(json.dumps([dict(name='gitlab-tests',status=status,allow_failure=False,started_at='2026-10-30T00:00:00Z')]))
        print(json.dumps([dict(name='optional',status='failed',allow_failure=True)]))
    elif lab:
        print(json.dumps(dict(state={4:'merged',5:'closed'}.get(number,'opened'),diff_refs=dict(head_sha='live-head'),
            head_pipeline=None if number==6 else dict(id=number,status='running' if number==2 else 'success'))))
    elif args[1]=='view':
        print(json.dumps(dict(state={4:'MERGED',5:'CLOSED'}.get(number,'OPEN'),headRefOid='live-head')))
    elif number==6:
        print('no required checks reported on the branch',file=sys.stderr); sys.exit(1)
    else:
        bucket={2:'pending',3:'fail'}.get(number,'fail' if changed else 'pass')
        state=dict(pending='IN_PROGRESS',fail='FAILURE',pass_='SUCCESS').get(bucket,'SUCCESS')
        print(json.dumps([dict(name='tests',bucket=bucket,state=state,startedAt='2026-10-30T00:00:00Z'),
                          dict(name='lint',bucket='pass',state='SUCCESS')]))
finally:
    with log.open('a') as out: out.write(json.dumps(dict(kind='end',pid=os.getpid(),at=time.time()))+'\n')
'''


class PRState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fm-pr-state-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        for name in ('state', 'data', 'config', 'fakebin'):
            (self.home / name).mkdir()
        for name in ('gh', 'glab'):
            path = self.home / 'fakebin' / name
            path.write_text(FAKE)
            path.chmod(0o700)
        (self.home / 'data/backlog.md').write_text('## In flight\n\n## Queued\n\n## Done\n')
        self.env = dict(os.environ, FM_HOME=str(self.home), FM_BACKEND='tmux', FM_BOARD_DISABLED='0',
                        CALLS=str(self.home / 'calls'), CHANGE=str(self.home / 'change'),
                        PATH=str(self.home / 'fakebin') + os.pathsep + os.environ['PATH'])

    def task(self, number, forge='github', status_only=False):
        name = f'{forge}-{number}'
        url = f'https://github.com/example/project/pull/{number}' if forge == 'github' else f'https://gitlab.example.com/team/sub/project/-/merge_requests/{number}'
        (self.home / f'state/{name}.meta').write_text('kind=scout\nproject=firstmate\npr_head=old-head\n' + ('' if status_only else f'pr={url}\n'))
        if status_only:
            (self.home / f'state/{name}.status').write_text(f'done: Implemented; {url}\n')
        return url

    def render(self):
        start = time.monotonic()
        result = subprocess.run([str(ROOT / 'bin/fm-board.sh'), 'render'], env=self.env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return time.monotonic() - start

    def records(self):
        records = []
        for path in (self.home / 'state/board-pr-cache').glob('*.json'):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and record.get('url'):
                records.append((path, record))
        return records

    def await_records(self, count, predicate=lambda rows: True):
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            rows = self.records()
            if len(rows) == count and predicate(rows):
                return rows
            time.sleep(.04)
        self.fail(f'cache did not converge: {self.records()}')

    def test_all_forge_states_errors_timeout_and_board(self):
        for number in range(1, 12):
            self.task(number, status_only=number == 3)
        for number in range(1, 7):
            self.task(number, 'gitlab', status_only=True)
        self.render()
        # The canonical snapshot cost scales with the fleet, but no forge calls
        # happen until it is captured; a cold render publishes FETCHING immediately.
        first = (self.home / 'data/board-full.md').read_text()
        self.assertIn('CI FETCHING', first)
        self.await_records(17)
        self.render()
        board = (self.home / 'data/board-full.md').read_text()
        for expected in ('CI GREEN', 'CI RUNNING: 1 of 2 checks passed', 'CI FAILED: tests',
                         'CI FAILED: gitlab-tests', 'MERGED', 'CLOSED', 'no checks configured',
                         'GitHub unreachable', 'GitHub: no permission', 'GitHub: PR not found',
                         'GitHub request timed out after 4s', 'GitHub: invalid PR response'):
            self.assertIn(expected, board)
        self.assertNotIn('unknown', board.lower())
        self.assertNotIn('optional', board)
        for _, record in self.records():
            self.assertIn('fetched_at', record)
        # All boards and tabs read the same persisted projection.
        model = json.loads((self.home / 'state/board-model.json').read_text())
        labels = [row['label'] for row in model['need']] + [step['label'] for programme in model['programmes'] for step in programme['steps']]
        self.assertIn('CI GREEN', labels)
        for section in ('1', '2'):
            out = subprocess.check_output([str(ROOT / 'bin/fm-board.sh'), 'view', '--once', '--section', section,
                                           '--width', '100', '--height', '60'], env=dict(self.env, FM_BOARD_DISABLED='0'), text=True)
            self.assertNotIn('Checks unknown', out)
        events = [json.loads(line) for line in (self.home / 'calls').read_text().splitlines()]
        active, maximum = set(), 0
        for event in events:
            if event['kind'] == 'start':
                active.add(event['pid'])
                maximum = max(maximum, len(active))
            else:
                active.discard(event['pid'])
        # The killed timeout process has no finally event; starts after it still
        # cannot exceed the four-thread worker's real concurrency.
        self.assertLessEqual(maximum, 4)

    def test_cache_fresh_stale_background_and_single_flight(self):
        self.task(1)
        self.render()
        self.await_records(1)
        self.render()
        calls = (self.home / 'calls').read_text()
        self.render()
        self.assertEqual(calls, (self.home / 'calls').read_text())
        path, record = self.records()[0]
        record['fetched_at'] -= 61
        path.write_text(json.dumps(record))
        (self.home / 'change').touch()
        self.env['DELAY'] = '1'
        elapsed = self.render()
        board = (self.home / 'data/board-full.md').read_text()
        self.assertIn('CI GREEN', board)
        self.assertIn('cached; refreshing', board)
        self.assertLess(elapsed, 2)
        # Render again while the child owns the lock, without duplicating fetches.
        self.render()
        self.await_records(1, lambda rows: rows[0][1].get('check_runs', [{}])[0].get('bucket') == 'fail')
        self.render()
        self.assertIn('CI FAILED: tests', (self.home / 'data/board-full.md').read_text())
        starts = [json.loads(line) for line in (self.home / 'calls').read_text().splitlines() if json.loads(line)['kind'] == 'start']
        self.assertEqual(len(starts), 2)  # one batched query per observation, no duplicate child

    def test_related_references_and_corrupt_cache_recover(self):
        self.task(1)
        (self.home / 'state/github-1.status').write_text(
            'working: Earlier PR https://github.com/example/project/pull/3\n'
            'done: Follow-up https://gitlab.example.com/team/sub/project/-/merge_requests/1\n')
        self.render()
        self.await_records(3)
        self.render()
        self.assertIn('related PRs:', (self.home / 'data/board-full.md').read_text())
        self.assertIn('CI FAILED: tests', (self.home / 'data/board-full.md').read_text())
        path, _ = next(row for row in self.records() if row[1]['url'].endswith('/pull/1'))
        path.write_text('{broken')
        self.render()
        self.assertIn('CI FETCHING', (self.home / 'data/board-full.md').read_text())
        self.await_records(3)
        self.render()
        self.assertIn('CI GREEN', (self.home / 'data/board-full.md').read_text())

    def age(self, seconds=61):
        for path, record in self.records():
            record['fetched_at'] -= seconds
            record.pop('retry_at', None)
            path.write_text(json.dumps(record))

    def starts(self):
        path = self.home / 'calls'
        return [json.loads(line)['args'] for line in path.read_text().splitlines()
                if json.loads(line)['kind'] == 'start'] if path.exists() else []

    def test_ttl_batch_and_hourly_measurement(self):
        for number in range(20, 30):
            self.task(number)
        self.render()
        self.await_records(10)
        calls = self.starts()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].count('graphql'), 1)
        query = calls[0][-1]
        self.assertEqual(query.count('pullRequest(number:'), 10)
        self.assertIn('isRequired(pullRequestNumber:20)', query)
        self.age(30)
        self.render()
        self.assertEqual(len(self.starts()), 1)  # 30-second-old OPEN records stay fresh
        self.age(31)
        self.render()
        self.await_records(10, lambda rows: all(time.time()-row[1]['fetched_at'] < 10 for row in rows))
        self.assertEqual(len(self.starts()), 2)
        ledger = next((self.home / 'state/board-pr-cache').glob('github-*.json'))
        self.assertEqual(json.loads(ledger.read_text())['charges'][0][1], 1)
        print('10 PRs: old 20 calls/5s = 14,400 minimum points/hour; batch fixture 1 point/60s = 60 points/hour')

    def test_rate_limit_keeps_values_and_skips_until_reset(self):
        self.task(1)
        self.render()
        self.await_records(1)
        self.age()
        self.env.update(GH_MODE='limit', RESET=str(time.time()+3600))
        self.render()
        self.await_records(1, lambda rows: 'refresh_error' in rows[0][1])
        self.assertEqual(len(self.starts()), 3)  # initial, failed batch, REST reset
        self.age()
        self.render()
        time.sleep(.2)
        self.assertEqual(len(self.starts()), 3)
        board = (self.home / 'data/board-full.md').read_text()
        self.assertIn('CI GREEN', board)
        self.assertIn('cached; GitHub rate-limit backoff', board)
        ledger = next((self.home / 'state/board-pr-cache').glob('github-*.json'))
        data = json.loads(ledger.read_text())
        data['blocked_until'] = time.time()-1
        ledger.write_text(json.dumps(data))
        self.age()
        self.env.pop('GH_MODE')
        self.render()
        self.await_records(1, lambda rows: 'refresh_error' not in rows[0][1])
        self.assertEqual(len(self.starts()), 4)

    def test_budget_persists_and_recovers_after_rolling_hour(self):
        self.task(1)
        self.env['GH_COST'] = '997'
        self.render()
        self.await_records(1)
        self.age()
        self.render()
        self.await_records(1, lambda rows: 'budget exhausted' in rows[0][1].get('refresh_error',''))
        self.assertEqual(len(self.starts()), 1)
        ledger = next((self.home / 'state/board-pr-cache').glob('github-*.json'))
        data = json.loads(ledger.read_text())
        data['charges'][0][0] -= 3601
        ledger.write_text(json.dumps(data))
        self.age()
        self.env['GH_COST'] = '1'
        self.render()
        self.await_records(1, lambda rows: 'refresh_error' not in rows[0][1])
        self.assertEqual(len(self.starts()), 2)

    def test_incomplete_contexts_never_report_green(self):
        self.task(1)
        self.env['GH_MODE'] = 'overflow'
        self.render()
        self.await_records(1)
        self.render()
        self.assertNotIn('CI GREEN', (self.home / 'data/board-full.md').read_text())
        self.assertIn('required checks incomplete', (self.home / 'data/board-full.md').read_text())

    def test_structured_status_parser_and_scaffolds(self):
        tail = ' | pr=https://github.com/example/project/pull/1 | checks=green | next=merge | owner=captain'
        for verb in ('done', 'needs-decision [key=choice]', 'blocked'):
            raw = verb + ': Specific outcome' + tail
            out = subprocess.check_output(['bash', '-c', '. "$1"; status_end_state_json "$2"', 'test',
                                           str(ROOT / 'bin/fm-classify-lib.sh'), raw], text=True)
            self.assertEqual(json.loads(out), dict(pr='https://github.com/example/project/pull/1', checks='green', next='merge', owner='captain'))
        for raw in ('done: ready', 'working: underway' + tail, 'done: x' + tail + ' | checks=failed',
                    'done: x' + tail.replace('checks=green', 'checks=unknown'),
                    'done: x' + tail.replace(' | owner=captain', ''),
                    'done: x' + tail.replace('owner=captain', 'owner=anyone'),
                    'done: x' + tail.replace('next=merge', 'next=maybe'),
                    'done: x' + tail.replace('pr=https://github.com/example/project/pull/1', 'pr=https://bad url'),
                    'done: x' + tail.replace('pr=https://github.com/example/project/pull/1', 'pr=https://example.com/no-pr')):
            out = subprocess.check_output(['bash', '-c', '. "$1"; status_end_state_json "$2"', 'test',
                                           str(ROOT / 'bin/fm-classify-lib.sh'), raw], text=True)
            self.assertEqual(json.loads(out), {}, raw)
        for name, args in (('direct', ['repo', '--mode', 'direct-PR']), ('local', ['repo', '--mode', 'local-only']),
                           ('pipeline', ['repo', '--mode', 'no-mistakes']), ('scout', ['repo', '--scout']),
                           ('mate', ['--secondmate', '--no-projects'])):
            subprocess.run([str(ROOT / 'bin/fm-brief.sh'), name, *args], env=self.env, capture_output=True, check=True)
            brief = (self.home / f'data/{name}/brief.md').read_text()
            for text in ('| checks=<green|running|failed|none>', 'recommend a default', 'from whom', 'keep watching'):
                self.assertIn(text, brief)


if __name__ == '__main__':
    unittest.main()
