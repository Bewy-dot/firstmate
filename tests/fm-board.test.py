#!/usr/bin/env python3
"""Exercise the board's executable interface with canonical JSON fixtures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    def task(name, repo, state, detail='', **kw):
        row = dict(id=name, kind='ship', project=repo, backend='herdr',
                   endpoint=dict(target='lab:w1:p2', exists=True),
                   current_state=dict(state=state, source='status-log', detail=detail),
                   backlog=dict(id=name, title=name.replace('-', ' ').capitalize(), repo=repo))
        row.update(kw)
        return row
    url = 'https://github.com/example/'
    return dict(schema='fm-fleet-snapshot.v1', generated='2026-10-30T08:00:00Z',
                main_inventory=dict(valid=True), backlog=dict(records=[
                    dict(id='onboarding-review', title='Approve engagement terms', structured=True,
                         repo='continuo', state='queued', captain_actionable=True,
                         hold_kind='captain', hold_reason='Approve the engagement wording'),
                    dict(id='competitor-gap', title='Competitor dashboard gaps', structured=True,
                         repo='continuo', state='queued', hold_reason='After the firm pilot finishes'),
                    dict(id='recent', title='Login repair', structured=True, repo='cadenza', state='done',
                         completion=dict(date='2026-10-29'), pr_url='https://github.com/example/cadenza/pull/9'),
                    dict(id='old', title='Old completed work', structured=True, repo='isp', state='done',
                         completion=dict(date='2026-10-25'))]), tasks=[
                    task('compensation-rules', 'continuo', 'parked', 'Choose a tax treatment',
                         hints=dict(open_decisions=[dict(verb='needs-decision', summary='Should allowances count toward compensation?')])),
                    task('multi-firm-programme', 'continuo', 'working', 'Implement firm switching'),
                    task('cadenza-ci', 'cadenza', 'working', 'ci running'),
                    task('database-release', 'isp', 'done', 'checks green; apply production database migration before merge', pr=dict(url=url+'isp/pull/1')),
                    task('merge-ready', 'aaa', 'done', 'checks green: PR ready for review', pr=dict(url=url+'aaa/pull/2')),
                    task('review-first', 'mca-website', 'done', 'PR opened', pr=dict(url=url+'mca-website/pull/3')),
                    task('external-release', 'firstmate', 'paused', 'Resume after upstream version 2 is released'),
                    task('build-failed', 'firstmate', 'failed', 'Review the build failure'),
                    task('unknown-task', 'misc', 'unknown'),
                    task('already-applied', 'isp', 'done', 'checks green; database migration already applied', pr=dict(url=url+'isp/pull/4'))])


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fm-board-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'config').mkdir()
        self.path = self.home / 'snapshot.json'
        self.env = dict(os.environ, FM_HOME=str(self.home), FM_BOARD_DISABLED='0', FM_BACKEND='tmux')
        self.snap = fixture()

    def run_board(self, command='render', check=True):
        self.path.write_text(json.dumps(self.snap))
        return subprocess.run([str(ROOT / 'bin/fm-board.sh'), command, '--snapshot', str(self.path)],
                              env=self.env, capture_output=True, text=True, check=check)

    def board(self):
        return (self.home / 'data/board.md').read_text()

    def test_groups_actions_history_and_notes(self):
        self.run_board()
        text = self.board()
        for expected in ['## Continuo', '### compensation', '### multi-firm programme',
                         '### onboarding/legal', '### gaps and competitor features',
                         '## Cadenza', '## ISP', '## AAA', '## MCA website', '## Firstmate', '## Other',
                         'Should allowances count toward compensation?',
                         'Apply the production database change, then give the merge word',
                         'Give the merge word', 'Review the PR; green checks have not been confirmed',
                         'Resume after upstream version 2 is released', 'Login repair',
                         'https://github.com/example/cadenza/pull/9',
                         'https://github.com/example/isp/pull/1']:
            self.assertIn(expected, text)
        self.assertNotIn('Old completed work', text)
        notes = '\n- Ask Ben about the pilot.\n  Keep this exact indentation.\n'
        path = self.home / 'data/board.md'
        path.write_text(text + notes)
        self.snap['tasks'][1]['current_state']['detail'] = 'Firm switching in review'
        self.run_board()
        self.assertEqual(self.board().split('## My notes\n')[1], notes)
        stamp = path.stat().st_mtime_ns
        self.run_board()
        self.assertEqual(stamp, path.stat().st_mtime_ns)

    def test_editable_mapping_and_invalid_config(self):
        config = self.home / 'config/board-groups.json'
        config.write_text(json.dumps(dict(rules=[dict(repo='misc', group='Continuo', category='compensation')])))
        self.run_board()
        self.assertIn('Unknown task', self.board().split('### compensation')[1].split('## Cadenza')[0])
        previous = self.board()
        config.write_text('{broken')
        self.assertNotEqual(self.run_board(check=False).returncode, 0)
        self.assertEqual(previous, self.board())

    def test_non_herdr_refresh_is_noop(self):
        for backend in ('tmux', 'zellij', 'orca', 'cmux'):
            with self.subTest(backend=backend):
                self.env['FM_BACKEND'] = backend
                self.run_board('refresh')
                self.assertFalse((self.home / 'data/board.md').exists())
                self.assertFalse((self.home / 'state').exists())

    def test_secondmate_actions_and_delivery(self):
        self.snap['secondmate_current'] = dict(records=[dict(
            id='delivery-worker', current=dict(state='captain_decision'),
            freshness=dict(status='fresh'), provenance=dict(summary_valid=True),
            decisions_open=[dict(id='legal-choice', repo='continuo', title='Engagement wording',
                                 summary='Legal review', reason='Approve the revised liability cap')],
            queued=[dict(id='pilot', repo='cadenza', title='Pilot launch', captain_actionable=True,
                         hold_reason='Approve the pilot date')],
            endpoints=[dict(id='ready', repo='isp', title='Database release', state='done',
                            detail='checks green; apply production database migration before merge',
                            pr_url='https://github.com/example/isp/pull/40')])])
        self.run_board()
        board = self.board()
        self.assertIn('Approve the revised liability cap', board.split('## Cadenza')[0])
        self.assertIn('Approve the pilot date', board.split('## Cadenza')[1].split('## ISP')[0])
        self.assertIn('Apply the production database change, then give the merge word', board.split('## ISP')[1])
        self.assertIn('https://github.com/example/isp/pull/40', board)

    def test_status_release_note_supplements_green_current_state(self):
        task = self.snap['tasks'][3]
        task['current_state']['detail'] = 'checks green: PR ready for review'
        task['paths'] = dict(status_log=dict(last_event=dict(note='Apply production database migration before merge')))
        self.run_board()
        self.assertIn('Apply the production database change, then give the merge word', self.board())

    def test_real_canonical_snapshot_backlog(self):
        (self.home / 'data').mkdir()
        (self.home / 'state').mkdir()
        (self.home / 'data/backlog.md').write_text('## Queued\n- [ ] choose - Choose the pilot firm (repo: continuo) (kind: captain) (hold-kind: captain) (hold: Which firm pilots first?)\n\n## Done\n- [x] shipped - Firm login https://github.com/example/continuo/pull/5 (repo: continuo) (merged 2026-10-29)\n')
        subprocess.run([str(ROOT / 'bin/fm-board.sh'), 'render'], env=dict(self.env, FM_SNAPSHOT_NOW='2026-10-30T08:00:00Z'), check=True, capture_output=True)
        self.assertIn('Which firm pilots first?', self.board())
        self.assertIn('https://github.com/example/continuo/pull/5', self.board())
        fakebin = self.home / 'fakebin'
        fakebin.mkdir()
        tmux = fakebin / 'tmux'
        tmux.write_text('#!/bin/sh\nexit 0\n')
        tmux.chmod(0o700)
        (self.home / 'state/worker.meta').write_text('window=test:fm-worker\nproject=isp\nkind=scout\npr=https://github.com/example/isp/pull/12\n')
        env = dict(self.env, FM_SNAPSHOT_NOW='2026-10-30T08:00:00Z', PATH=str(fakebin) + os.pathsep + os.environ['PATH'])
        summary = json.loads(subprocess.check_output([str(ROOT / 'bin/fm-fleet-snapshot.sh'), '--secondmate-home-summary'], env=env, text=True))
        self.assertEqual(summary['endpoints'][0]['repo'], 'isp')
        self.assertEqual(summary['endpoints'][0]['pr_url'], 'https://github.com/example/isp/pull/12')
        self.assertEqual(summary['decisions_open'][0]['repo'], 'continuo')
        self.assertEqual(summary['landed'][0]['repo'], 'continuo')


if __name__ == '__main__':
    if len(os.sys.argv) == 3 and os.sys.argv[1] == '--fixture':
        Path(os.sys.argv[2]).write_text(json.dumps(fixture()))
    else:
        unittest.main()
