#!/usr/bin/env python3
"""Exercise the board's executable interface with canonical JSON fixtures."""
import fcntl
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import termios
import time
import re
import tempfile
import unittest
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]


def green_pr(url):
    return dict(url=url, observed_at='2026-10-30T08:00:00Z', merge_state='CLEAN',
                check_runs=[dict(name='tests', status='COMPLETED', conclusion='SUCCESS')])


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
                    task('database-release', 'isp', 'done', 'checks green; apply production database migration before merge', pr=green_pr(url+'isp/pull/1')),
                    task('merge-ready', 'aaa', 'done', 'checks green: PR ready for review', pr=green_pr(url+'aaa/pull/2')),
                    task('review-first', 'mca-website', 'done', 'PR opened', pr=dict(url=url+'mca-website/pull/3'), hints=dict(open_decisions=[dict(verb='needs-decision', summary='Please review the PR')])),
                    task('external-release', 'firstmate', 'paused', 'Resume after upstream version 2 is released'),
                    task('build-failed', 'firstmate', 'failed', 'Review the build failure'),
                    task('unknown-task', 'misc', 'unknown'),
                    task('already-applied', 'isp', 'done', 'checks green; database migration already applied', pr=green_pr(url+'isp/pull/4'))])


def sequence_fixture():
    snap = fixture()
    snap['tasks'] = snap['tasks'][:1]
    base = dict(structured=True, repo='continuo', state='queued',
                kind='captain', hold_kind='captain', captain_actionable=True)
    snap['backlog']['records'] = [dict(base, id='appendix-' + str(n),
        title='Org onboarding Appendix A decision ' + str(n),
        hold_reason='Choose the legal option ' + str(n)) for n in range(80)]
    for name, title, blockers, state, detail, extra in [
        ('billing', 'Recurring billing', [], 'done', 'checks green',
         dict(pr=green_pr('https://github.com/example/continuo/pull/101'))),
        ('fees', 'Fee schedule', ['billing'], 'working', 'Implementing', {}),
        ('proposals', 'Plan proposals', ['fees'], 'working', 'Implementing', {}),
        ('rollout', 'Plan rollout', [], 'blocked', 'Waiting for captain word', {})]:
        backlog = dict(id=name, title=title, repo='continuo', structured=True,
                       state='in_flight', blocked_by_ids=blockers,
                       unresolved_blocker_ids=blockers)
        if name in ('billing', 'rollout'):
            backlog.update(hold_kind='captain', hold_reason=('Give the merge word' if name == 'billing' else 'Wait for your word before rollout'),
                           captain_actionable=True)
        snap['tasks'].append(dict(id=name, kind='ship', project='continuo', backend='herdr',
            endpoint=dict(target='lab:w1:p2', exists=True), backlog=backlog,
            current_state=dict(state=state, detail=detail), **extra))
        snap['backlog']['records'].append(backlog)
    return snap


class Board(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='fm-board-')
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        (self.home / 'config').mkdir()
        self.path = self.home / 'snapshot.json'
        self.env = dict(os.environ, FM_HOME=str(self.home), FM_BOARD_DISABLED='0', FM_BACKEND='tmux')
        self.env.pop('NO_COLOR', None)
        self.env.pop('FM_BOARD_ICONS', None)
        self.snap = fixture()

    def run_board(self, command='render', check=True):
        self.path.write_text(json.dumps(self.snap))
        return subprocess.run([str(ROOT / 'bin/fm-board.sh'), command, '--snapshot', str(self.path)],
                              env=self.env, capture_output=True, text=True, check=check)

    def board(self):
        return (self.home / 'data/board.md').read_text()

    def full(self):
        return (self.home / 'data/board-full.md').read_text()

    def test_compact_decisions_and_dependency_sequence(self):
        self.snap = sequence_fixture()
        self.run_board()
        board = self.board()
        self.assertLessEqual(len(board.splitlines()), 60)
        self.assertIn('[Full detail](board-full.md)', board)
        self.assertIn('NEEDS YOU NOW', board)
        self.assertIn('QUEUED DECISIONS', board)
        self.assertNotIn('Choose the legal option', board)
        self.assertIn('80 decisions', board)
        self.assertIn('Recurring billing [READY: merge?] -> Fee schedule [WAITING', board)
        self.assertIn('Plan rollout [HOLDING]', board)
        self.assertIn('https://github.com/example/continuo/pull/101', board)
        self.assertIn('Choose the legal option 79', self.full())
        self.assertIn('WAITING: Recurring billing', self.full())
        self.assertNotIn('Fee schedule [WORKING]', self.full())

    def test_large_inventory_stays_bounded_and_complete(self):
        self.snap = sequence_fixture()
        for n in range(100):
            task = dict(self.snap['tasks'][0], id='question-' + str(n),
                        backlog=dict(id='question-' + str(n), repo='continuo',
                                     title='Very long title ' * 30))
            self.snap['tasks'].append(task)
        self.snap['main_inventory']['valid'] = False
        self.run_board()
        self.assertLessEqual(len(self.board().splitlines()), 60)
        self.assertIn('+98 more groups', self.board())
        self.assertIn('question-99', self.full())
        self.assertIn('Worker records need attention', self.board())
        self.assertNotIn('Very long title ' * 5, self.board())

    def test_all_summary_sections_have_a_fixed_line_budget(self):
        self.snap = fixture()
        for n in range(40):
            base = dict(self.snap['tasks'][0], id='answer-' + str(n),
                        pr=dict(url=f'https://github.com/example/continuo/pull/{n + 100}'))
            self.snap['tasks'].append(base)
            running = dict(base, id='running-' + str(n), hints={},
                           current_state=dict(state='working', detail='Implementing'))
            self.snap['tasks'].append(running)
        for n, repo in enumerate(('continuo', 'cadenza', 'isp', 'aaa', 'mca-website', 'firstmate', 'other')):
            for kind in ('captain', 'ship'):
                self.snap['backlog']['records'].append(dict(id=f'{kind}-{n}',
                    structured=True, repo=repo, kind=kind, state='queued',
                    title='Later work', hold_reason='After the pilot'))
        self.run_board()
        self.assertLessEqual(len(self.board().splitlines()), 60)
        self.assertIn('+', self.board().split('## Running')[1])

    def test_dependencies_scope_cycles_and_completed_prerequisites(self):
        self.snap = sequence_fixture()
        billing = self.snap['tasks'][1]['backlog']
        billing.update(state='done', completion=dict(date='2026-10-01'))
        self.snap['tasks'][1]['current_state'] = dict(state='done', detail='Merged')
        self.run_board()
        self.assertIn('Recurring billing [DONE] -> Fee schedule [WAITING]', self.board())
        self.assertIn('Recurring billing', self.full())
        # Missing ids remain an explicit wait; cycles must not hide or hang work.
        self.snap['tasks'][1]['backlog']['blocked_by_ids'] = ['proposals']
        self.snap['tasks'][2]['backlog']['unresolved_blocker_ids'] = ['missing-step']
        self.run_board()
        self.assertIn('Waiting on missing-step', self.full())
        self.assertIn('Plan proposals', self.board())

    def test_queued_decision_does_not_mean_asked_even_when_eligible(self):
        self.snap = sequence_fixture()
        self.snap['tasks'] = []
        self.snap['backlog']['records'] = self.snap['backlog']['records'][:80]
        self.run_board()
        now = self.board().split('## NEEDS YOU NOW')[1].split('## Running')[0]
        self.assertIn('None.', now)
        self.assertIn('80 decisions filed; not yet asked', self.board())
        self.assertNotIn('Org onboarding Appendix A', self.board())

    def test_secondmate_uses_endpoint_delivery_not_filed_decision(self):
        self.snap = dict(schema='fm-fleet-snapshot.v1', generated='2026-10-30T08:00:00Z',
            tasks=[], backlog=dict(records=[]), secondmate_current=dict(records=[dict(
                id='mate', freshness=dict(status='fresh'), provenance=dict(summary_valid=True),
                queued=[dict(id='ready', title='Ready change', repo='isp', hold_kind='captain',
                             captain_actionable=True, hold_reason='Merge this'),
                        dict(id='filed', title='Choose pilot', repo='isp', kind='captain',
                             captain_actionable=True, hold_reason='Choose the date')],
                decisions_open=[dict(id='ready', source='backlog', verb='captain-hold'),
                                dict(id='filed', source='backlog', verb='captain-hold')],
                endpoints=[dict(id='ready', title='Ready change', repo='isp', state='done',
                    detail='checks green', pr_url='https://github.com/example/isp/pull/42')])]))
        self.run_board()
        self.assertIn('Ready change [CHECKS UNKNOWN]', self.board())
        self.assertNotIn('Choose pilot', self.board())
        self.assertIn('Choose pilot [QUEUED]', self.full())
        self.assertEqual(self.full().count('Ready change ['), 1)

    def test_green_delivery_request_and_independent_question(self):
        self.snap = sequence_fixture()
        ready = self.snap['tasks'][1]
        ready['hints'] = dict(open_decisions=[dict(verb='needs-decision', summary='Awaiting your merge word')])
        self.run_board()
        self.assertIn('Recurring billing [READY: merge?]', self.board())
        ready['hints']['open_decisions'].append(dict(verb='needs-decision', summary='Which currency should invoices use?'))
        self.run_board()
        self.assertIn('Recurring billing [NEEDS ANSWER]', self.board())
        self.assertIn('Which currency should invoices use?', self.full())

    def test_filed_decisions_count_under_existing_programme(self):
        self.snap = sequence_fixture()
        programme = dict(id='org', title='Org onboarding', repo='continuo', kind='program',
                         current_role='program', state='in_flight', structured=True)
        self.snap['backlog']['records'].append(programme)
        for row in self.snap['backlog']['records'][:80]:
            row['blocked_by_ids'] = ['org']
            row['unresolved_blocker_ids'] = ['org']
        self.run_board()
        self.assertIn('Continuo / Org onboarding: 80 decisions filed', self.board())
        self.assertNotIn('Org onboarding Appendix A', self.board())

    def test_branching_dependencies_do_not_imply_sibling_sequence(self):
        self.snap = sequence_fixture()
        self.snap['tasks'][3]['backlog']['blocked_by_ids'] = ['billing']
        self.run_board()
        self.assertNotIn('Fee schedule [WAITING] -> Plan proposals', self.board())
        self.assertIn('Plan proposals [WAITING] after Recurring billing', self.board())

    def frame(self, *args, env=None):
        run_env = dict(self.env, FM_BOARD_DISABLED='1', **(env or {}))
        out = subprocess.run([sys.executable, str(ROOT / 'bin/fm-board.py'), 'view', '--once', *args],
                             env=run_env, capture_output=True, text=True, check=True).stdout
        return out, re.sub(r'\x1b\][^\x1b]*\x1b\\|\x1b\[[0-9;]*m', '', out)

    def test_default_view_is_one_screen_whatever_the_inventory(self):
        self.snap = fixture()
        for n in range(40):
            base = dict(self.snap['tasks'][0], id='answer-' + str(n), backlog=dict(id='answer-' + str(n), repo='continuo', title='Question ' + str(n)))
            self.snap['tasks'].append(base)
            self.snap['tasks'].append(dict(base, id='running-' + str(n), hints={}, current_state=dict(state='working', detail='Implementing')))
        self.snap['fm_home'] = str(self.home)
        self.run_board()
        for width in (60, 100):
            for selected in (0, 5, 50):
                with self.subTest(width=width, selected=selected):
                    _, text = self.frame('--width', str(width), '--select', str(selected))
                    self.assertLessEqual(len(text.splitlines()), 34)
                    self.assertTrue(all(len(line) <= width for line in text.splitlines()))
        _, text = self.frame('--width', '60')
        self.assertIn('+', text.split('Needs you now')[1].split('In motion')[0])
        _, detail = self.frame('--width', '60', '--detail')
        self.assertGreater(len(detail.splitlines()), 100)

    def test_terminal_colours_pills_links_and_selection(self):
        self.snap['fm_home'] = str(self.home)
        self.snap['tasks'] = [t for t in self.snap['tasks'] if t['id'] != 'build-failed']
        (self.home / '.obsidian').mkdir()
        (self.home / 'data/compensation-rules').mkdir(parents=True)
        (self.home / 'data/compensation-rules/brief.md').write_text('brief')
        self.run_board()
        raw, text = self.frame('--width', '70', '--select', '1')
        self.assertIn('\x1b[38;2;249;226;175m', raw)  # yellow: needs you
        self.assertIn('\x1b[38;2;250;179;135m', raw)  # peach: apply the database change first
        self.assertIn('\x1b[38;2;166;227;161m', raw)  # green: ready or done
        self.assertIn('\x1b[38;2;137;180;250m', raw)  # blue: running
        self.assertNotIn('243;139;168', raw)           # red is reserved for a failure
        self.assertIn('\ue0b2', raw)                  # powerline pill caps
        self.assertIn('Database, then merge', text)
        self.assertIn('Question for you', text)
        self.assertIn('\x1b]8;;https://github.com/example/isp/pull/1\x1b\\', raw)
        self.assertIn('$ bin/fm-goto.sh', text)  # the selected worker shows its command
        quoted = urllib.parse.quote(self.home.name, safe='')
        first, _ = self.frame('--width', '70')
        self.assertIn('obsidian://open?vault=' + quoted + '&file=data%2Fcompensation-rules%2Fbrief', first)
        plain_raw, plain = self.frame('--width', '70', env=dict(FM_BOARD_ICONS='plain'))
        self.assertNotIn('\ue0b2', plain_raw)
        mono_raw, _ = self.frame('--width', '70', env=dict(NO_COLOR='1'))
        self.assertNotIn('38;2', mono_raw)
        self.assertNotIn('48;2', mono_raw)

    def test_failure_uses_peach_and_a_word(self):
        self.snap['tasks'] = [t for t in self.snap['tasks'] if t['id'] == 'build-failed']
        self.snap['tasks'][0]['current_state'] = dict(state='failed', detail='Build failed')
        self.run_board()
        raw, text = self.frame('--width', '70')
        self.assertNotIn('243;139;168', raw)
        self.assertIn('Failed', text)

    def test_second_mate_home_is_authoritative_and_merged_prs_are_dropped(self):
        merged = 'https://github.com/example/isp/pull/17'
        live = 'https://github.com/example/isp/pull/185'
        stale = dict(id='mate', freshness=dict(status='fresh'), provenance=dict(summary_valid=False),
                     endpoints=[dict(id='q6', title='Queue dashboard', repo='isp', state='unknown', pr_url=merged,
                                     detail='harness state unavailable (unknown codex-unverified)')])
        self.snap = dict(schema='fm-fleet-snapshot.v1', generated='2026-10-30T08:00:00Z', main_inventory=dict(valid=True),
                         tasks=[dict(id='mate', kind='secondmate', backend='herdr', endpoint=dict(target='lab:w3:p8', exists=True),
                                     current_state=dict(state='done', detail='re-read'), pr=dict(url=merged))],
                         backlog=dict(records=[]), secondmate_current=dict(records=[stale]),
                         secondmate_landed=dict(records=[dict(id='q4', title='Queue Q4', repo='isp', owner='mate', pr_url=merged,
                                                              completion=dict(verb='merged', date='2026-10-29'))]))
        self.run_board()
        self.assertIn('Queue dashboard [CHECKS UNKNOWN]', self.full())  # the bounded summary alone misreads it
        def codex(name, pr, event, note):
            return dict(id=name, kind='ship', project='isp', backend='herdr', endpoint=dict(target='lab:w3:p9', exists=True),
                        backlog=dict(id=name, title='Worker ' + name, repo='isp'), pr=dict(url=pr),
                        current_state=dict(state='unknown', source='pane', detail='harness state unavailable (unknown codex-unverified)'),
                        paths=dict(status_log=dict(last_event=dict(state=event, note=note))))
        owned = dict(schema='fm-fleet-snapshot.v1', generated='2026-10-30T08:00:00Z', fm_home='/mate', main_inventory=dict(valid=True),
                     tasks=[codex('q6', live, 'paused', "PR open, waiting for the captain's merge decision"),
                            codex('switch', None, 'paused', 'waiting for the natural 08:15 run')],
                     backlog=dict(records=[dict(id='q4', title='Queue Q4', repo='isp', structured=True, state='done',
                                                completion=dict(verb='merged', date='2026-10-29'), pr_url=merged)]))
        (self.home / 'mate.json').write_text(json.dumps(owned))
        self.path.write_text(json.dumps(self.snap))
        subprocess.run([str(ROOT / 'bin/fm-board.sh'), 'render', '--snapshot', str(self.path), '--home-snapshot', 'mate=' + str(self.home / 'mate.json')],
                       env=self.env, check=True, capture_output=True)
        board, full = self.board(), self.full()
        self.assertNotIn('Worker status', full)
        self.assertNotIn('BLOCKED', board)
        needs = board.split('## NEEDS YOU NOW')[1].split('## Running')[0]
        self.assertIn('Worker q6 [CHECKS UNKNOWN]', needs)
        self.assertIn('Checks unknown', needs)
        self.assertIn(live, needs)
        self.assertNotIn(merged, board)  # a merged PR is never offered
        self.assertNotIn('switch', needs)  # a deliberate wait is not a request for you
        self.assertIn('Worker switch [PAUSED]', full)
        self.assertIn('Queue Q4 [DONE]', full)
        model = json.loads((self.home / 'state/board-model.json').read_text())
        self.assertEqual([i['id'] for i in model['need']], ['q6'])
        self.assertEqual(model['need'][0]['loc'], dict(workspace='w3', tab='fm-q6', pane='p9'))
        self.assertEqual(model['need'][0]['goto'], dict(id='q6', home='/mate'))
        self.assertEqual(model['need'][0]['prs'], [dict(label='PR #185', url=live)])

    def test_merged_pr_never_asks_for_review(self):
        url = 'https://github.com/example/isp/pull/77'
        self.snap['tasks'] = [dict(self.snap['tasks'][3], id='shipped', pr=dict(url=url),
                                   backlog=dict(id='shipped', title='Shipped work', repo='isp', completion=dict(verb='merged', date='2026-10-30')))]
        self.snap['backlog']['records'].append(dict(id='shipped', title='Shipped work', structured=True, repo='isp', state='done',
                                                    completion=dict(verb='merged', date='2026-10-30'), pr_url=url))
        self.run_board()
        self.assertNotIn(url, self.board())
        self.assertIn('Shipped work [DONE]', self.full())

    def test_registered_local_second_mate_home_is_read_from_disk(self):
        mate = self.home / 'mate'
        (self.home / 'data').mkdir()
        (self.home / 'state').mkdir()
        for path in (mate / 'data', mate / 'state'):
            path.mkdir(parents=True)
        (mate / '.fm-secondmate-home').touch()
        (self.home / 'data/backlog.md').write_text('## In flight\n\n## Queued\n\n## Done\n')
        (self.home / 'data/secondmates.md').write_text(f'- mate - Owns ISP (home: {mate}; scope: ISP; projects: isp; added 2026-10-29)\n')
        (mate / 'data/backlog.md').write_text('## In flight\n\n## Queued\n- [ ] later - Choose the pilot (repo: isp) (kind: captain) (hold-kind: captain) (hold: Which pilot?)\n\n## Done\n')
        (self.home / 'state/mate.meta').write_text(f'window=main:fm-mate\nendpoint_task_id=mate\nworktree={mate}\nproject={mate}\nkind=secondmate\nhome={mate}\nprojects=isp\n')
        subprocess.run([str(ROOT / 'bin/fm-board.sh'), 'render'], env=dict(self.env, FM_SNAPSHOT_NOW='2026-10-30T08:00:00Z'), check=True, capture_output=True)
        self.assertIn('Which pilot?', self.full())
        self.assertIn('Task: mate/later', self.full())

    def test_interactive_viewer_navigates_opens_and_goes(self):
        home = self.home
        self.snap['fm_home'] = str(home)
        (home / 'state').mkdir(exist_ok=True)
        (home / '.obsidian').mkdir()
        (home / 'data/compensation-rules').mkdir(parents=True)
        (home / 'data/compensation-rules/brief.md').write_text('brief')
        for task in self.snap['tasks']:
            task['endpoint']['target'] = 'default:w1:pAB'
        fm_write = (home / 'state/compensation-rules.meta')
        fm_write.write_text('window=default:w1:pAB\nendpoint_task_id=compensation-rules\nworktree=/w\nproject=/p\nbackend=herdr\n'
                            'herdr_session=default\nherdr_workspace_id=w1\nherdr_tab_id=w1:tAB\nherdr_pane_id=w1:pAB\n')
        fakebin = home / 'fakebin'
        fakebin.mkdir()
        log = home / 'calls.log'
        (fakebin / 'herdr').write_text('#!/bin/sh\necho "$*" >> ' + str(log) + '\ncase "$1 $2" in "pane get") echo \'{"result":{"pane":{"pane_id":"w1:pAB","tab_id":"w1:tAB"}}}\';; esac\n')
        (fakebin / 'herdr').chmod(0o755)
        (fakebin / 'open-recorder').write_text('#!/bin/sh\necho "$*" >> ' + str(home / 'opened.log') + '\n')
        (fakebin / 'open-recorder').chmod(0o755)
        self.run_board()
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 60, 100, 0, 0))
        process = subprocess.Popen([sys.executable, str(ROOT / 'bin/fm-board.py'), 'view'], stdin=slave, stdout=slave, stderr=slave,
            env=dict(self.env, FM_BOARD_DISABLED='1', TERM='xterm-256color', PATH=str(fakebin) + os.pathsep + os.environ['PATH'],
                     FM_BOARD_OPEN=str(fakebin / 'open-recorder')))

        def stop_viewer():
            if process.poll() is None:
                process.kill()
            process.wait()
        self.addCleanup(stop_viewer)
        seen = [b'']

        def until(token):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if token.encode() in seen[0]:
                    return seen[0].decode(errors='replace')
                if select.select([master], [], [], 0.1)[0]:
                    seen[0] += os.read(master, 65536)
            self.fail('Terminal did not render ' + token + ': ' + seen[0].decode(errors='replace'))

        until('quit')
        self.assertIn('Needs you now', seen[0].decode(errors='replace'))
        os.write(master, b'G')  # Unknown keys are harmless.
        os.write(master, b'f')
        until('Unknown task')  # only the full list reaches the sixth item
        os.write(master, b'f')
        os.write(master, b'\x1b[A')
        os.write(master, b'b')
        until('Opened Brief')
        self.assertIn('obsidian://open', (home / 'opened.log').read_text())
        os.write(master, b'\r')
        until('Focused compensation-rules')
        self.assertIn('tab focus w1:tAB', log.read_text())
        os.write(master, b'q')
        until('1049l')
        self.assertEqual(process.wait(timeout=5), 0)

    def test_section_frames_at_half_width(self):
        self.snap = sequence_fixture()
        self.snap['tasks'].append(fixture()['tasks'][6])
        self.run_board()
        for section, expected in [(1, 'Needs you now'), (2, 'Fee schedule'),
                                  (3, 'Nobody is blocked'), (4, 'Bring back:')]:
            raw, text = self.frame('--section', str(section), '--width', '60', '--height', '34')
            self.assertIn(expected, text)
            self.assertIn('Tab', text)
            self.assertIn('next section', text)
            self.assertLessEqual(max(map(len, text.splitlines())), 60)
            self.assertEqual(len(text.splitlines()), 34)
            self.assertNotIn('243;139;168', raw)
        _, text = self.frame('--section', '3', '--select', '79', '--width', '60', '--height', '34')
        self.assertIn('79', text)
        self.assertTrue('3Q:80' in text.splitlines()[0] or '>3 Queued 80' in text.splitlines()[0])

    def test_section_keys_and_restart(self):
        self.snap['fm_home'] = str(self.home)
        self.snap['backlog']['records'][0]['since'] = '2026-10-28'
        for name in ('brief', 'report'):
            path = self.home / ('data/onboarding-review/' + name + '.md')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        recorder = self.home / 'record-open'
        recorder.write_text('#!/bin/sh\necho "$*" >> "' + str(self.home / 'opened.log') + '"\n')
        recorder.chmod(0o700)
        self.env['FM_BOARD_OPEN'] = str(recorder)
        self.run_board()
        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 34, 60, 0, 0))
        saved_terminal = termios.tcgetattr(slave)
        env = dict(self.env, FM_BOARD_DISABLED='1', HERDR_SESSION='fixture-session')
        def launch():
            p = subprocess.Popen([sys.executable, str(ROOT / 'bin/fm-board.py'), 'view'],
                                 stdin=slave, stdout=slave, stderr=slave, env=env)
            def cleanup():
                if p.poll() is None:
                    p.kill()
                p.wait()
            self.addCleanup(cleanup)
            return p
        def read_frame(token=None):
            data = b''
            deadline = time.monotonic() + (5 if token else 0.2)
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.03)[0]:
                    data += os.read(master, 65536)
                    if token and token.encode() in data:
                        break
            return data.decode(errors='replace')
        def quit_viewer(p):
            os.write(master, b'q')
            # TCSADRAIN restores the terminal only after queued output is read.
            # Waiting on the process without draining its PTY deadlocks on macOS.
            data = b''
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.03)[0]:
                    data += os.read(master, 65536)
                if p.poll() is not None:
                    while select.select([master], [], [], 0)[0]:
                        data += os.read(master, 65536)
                    break
            self.assertIsNotNone(p.poll(), 'Viewer did not exit while its PTY was being drained')
            self.assertEqual(p.wait(timeout=0.1), 0)
            self.assertIn(b'\x1b[?25h\x1b[?1049l', data)
            restored = termios.tcgetattr(slave)
            # macOS sets PENDIN while switching back to canonical input.
            # It is kernel queue bookkeeping, not an unrestored terminal mode.
            restored[3] &= ~getattr(termios, 'PENDIN', 0)
            expected_terminal = list(saved_terminal)
            expected_terminal[3] &= ~getattr(termios, 'PENDIN', 0)
            self.assertEqual(restored, expected_terminal)

        p = launch()
        self.assertIn('Needs you now', read_frame('Needs you now'))
        for key, expected, token in [
            (b'\t', 1, 'In motion'), (b'\t', 2, 'Nobody is blocked'),
            (b'\t', 3, 'Bring back:'), (b'\t', 0, 'Needs you now'),
            (b'\x1b[Z', 3, 'Bring back:'), (b'1', 0, 'Needs you now'),
            (b'2', 1, 'In motion'), (b'3', 2, '2d ago'), (b'4', 3, 'Bring back:')]:
            read_frame()  # Drain the previous frame before checking this key.
            os.write(master, key)
            deadline = time.monotonic() + 5
            preference = self.home / 'state/board-view.json'
            while time.monotonic() < deadline:
                try:
                    if json.loads(preference.read_text())['section'] == expected:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.01)
            text = read_frame(token)
            self.assertIn(token, text)
            pref = json.loads((self.home / 'state/board-view.json').read_text())
            self.assertEqual(pref['section'], expected)
        os.write(master, b'3')
        read_frame('Nobody is blocked')
        for key, label in [(b'b', 'Brief'), (b'r', 'Report'), (b'\r', 'Detail')]:
            os.write(master, key)
            self.assertIn('Opened ' + label, read_frame('Opened ' + label))
        opened = (self.home / 'opened.log').read_text()
        self.assertIn('onboarding-review/brief.md', opened)
        self.assertIn('onboarding-review/report.md', opened)
        self.assertIn('board-full.md', opened)
        os.write(master, b'4')
        read_frame('Bring back:')
        quit_viewer(p)
        p = launch()
        self.assertIn('Bring back:', read_frame('Bring back:'))
        # Live model updates preserve the section and render its empty state.
        model_path = self.home / 'state/board-model.json'
        model = json.loads(model_path.read_text())
        model['queued'] = []
        model['counts']['queued'] = 0
        model_path.write_text(json.dumps(model))
        os.write(master, b'3')
        self.assertIn('Nothing queued', read_frame('Nothing queued'))
        quit_viewer(p)

    def test_groups_actions_history_and_notes(self):
        self.run_board()
        text = self.full()
        for expected in ['## Continuo', 'Programme: compensation', 'Programme: multi-firm programme',
                         'Programme: onboarding/legal', 'Programme: gaps and competitor features',
                         '## Cadenza', '## ISP', '## AAA', '## MCA website', '## Firstmate', '## Other',
                         'Should allowances count toward compensation?',
                         'Apply the production database change, then give the merge word',
                         'Give the merge word', 'Review the PR; checks unknown',
                         'Resume after upstream version 2 is released', 'Login repair',
                         'https://github.com/example/cadenza/pull/9',
                         'https://github.com/example/isp/pull/1']:
            self.assertIn(expected, text)
        self.assertNotIn('Old completed work', text)
        notes = '\n- Ask Ben about the pilot.\n  Keep this exact indentation.\n'
        path = self.home / 'data/board.md'
        path.write_text(self.board() + notes)
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
        self.assertIn('Unknown task', self.full().split('## Continuo')[1].split('## Cadenza')[0])
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
                                 verb='needs-decision', source='status', summary='Approve the revised liability cap')],
            queued=[dict(id='pilot', repo='cadenza', title='Pilot launch', captain_actionable=True,
                         hold_reason='Approve the pilot date')],
            endpoints=[dict(id='ready', repo='isp', title='Database release', state='done',
                            detail='checks green; apply production database migration before merge',
                            pr_url='https://github.com/example/isp/pull/40')])])
        self.run_board()
        board = self.full()
        self.assertIn('Approve the revised liability cap', board.split('## Cadenza')[0])
        self.assertIn('Approve the pilot date', board.split('## Cadenza')[1].split('## ISP')[0])
        self.assertIn('Apply the production database change, then give the merge word', board.split('## ISP')[1])
        self.assertIn('https://github.com/example/isp/pull/40', board)

    def test_status_release_note_supplements_green_current_state(self):
        task = self.snap['tasks'][3]
        task['current_state']['detail'] = 'checks green: PR ready for review'
        task['paths'] = dict(status_log=dict(last_event=dict(note='Apply production database migration before merge')))
        self.run_board()
        self.assertIn('Apply the production database change, then give the merge word', self.full())

    def test_pr_checks_running_failed_green_and_unknown(self):
        url = 'https://github.com/example/isp/pull/1'
        base = self.snap['tasks'][3]
        self.snap['backlog']['records'] = []
        self.snap['tasks'] = [base]
        cases = [
            (dict(green_pr(url), check_runs=[
                dict(name='tests', status='IN_PROGRESS', startedAt='2026-10-30T07:53:00Z'),
                dict(name='lint', state='PENDING')]), 'CI RUNNING: 2 pending, 7m elapsed'),
            (dict(green_pr(url), check_runs=[dict(name='lint', status='COMPLETED', conclusion='FAILURE')]), 'CI FAILED: lint'),
            (green_pr(url), 'READY: apply + merge?'),
            (dict(url=url), 'CHECKS UNKNOWN'),
            (dict(green_pr(url), merge_state='BLOCKED'), 'CHECKS UNKNOWN'),
            (dict(green_pr(url), check_runs=[]), 'CHECKS UNKNOWN'),
            (dict(green_pr(url), observed_at='2026-10-30T07:00:00Z'), 'CHECKS UNKNOWN')]
        for pr, label in cases:
            with self.subTest(label=label, pr=pr):
                base['pr'] = pr
                self.run_board()
                self.assertIn('[' + label + ']', self.full())
                self.assertNotIn('Review the PR', self.full())
                model = json.loads((self.home / 'state/board-model.json').read_text())
                labels = [i['label'] for i in model['need']] + [s['label'] for p in model['programmes'] for s in p['steps']]
                self.assertIn(label, labels)
        base['hints'] = dict(open_decisions=[dict(verb='needs-decision', summary='Please review the PR')])
        base['pr'] = cases[0][0]
        self.run_board()
        self.assertIn('CI RUNNING: 2 pending', self.full())
        self.assertNotIn('Review the PR', self.full())
        base['pr'] = dict(url=url)
        self.run_board()
        self.assertIn('Review the PR; checks unknown', self.full())

    def test_offline_pr_enrichment_matches_url_and_head(self):
        url = 'https://github.com/example/isp/pull/1'
        task = self.snap['tasks'][3]
        task['pr'] = dict(url=url, head='current')
        self.snap['tasks'] = [task]
        evidence = self.home / 'prs.json'
        evidence.write_text(json.dumps(dict(schema='fm-bearings.v1', candidate_prs=[dict(green_pr(url), head='current')])))
        self.path.write_text(json.dumps(self.snap))
        cmd = [str(ROOT / 'bin/fm-board.sh'), 'render', '--snapshot', str(self.path), '--pr-snapshot', str(evidence)]
        subprocess.run(cmd, env=self.env, check=True, capture_output=True)
        self.assertIn('READY: apply + merge?', self.full())
        task['pr']['head'] = 'new-commit'
        self.path.write_text(json.dumps(self.snap))
        subprocess.run(cmd, env=self.env, check=True, capture_output=True)
        self.assertIn('CHECKS UNKNOWN', self.full())
        self.assertNotIn('READY:', self.full())

    def test_real_canonical_snapshot_backlog(self):
        (self.home / 'data').mkdir()
        (self.home / 'state').mkdir()
        (self.home / 'data/backlog.md').write_text('## Queued\n- [ ] choose - Choose the pilot firm (repo: continuo) (kind: captain) (hold-kind: captain) (hold: Which firm pilots first?)\n\n## Done\n- [x] shipped - Firm login https://github.com/example/continuo/pull/5 (repo: continuo) (merged 2026-10-29)\n')
        subprocess.run([str(ROOT / 'bin/fm-board.sh'), 'render'], env=dict(self.env, FM_SNAPSHOT_NOW='2026-10-30T08:00:00Z'), check=True, capture_output=True)
        self.assertIn('Which firm pilots first?', self.full())
        self.assertIn('https://github.com/example/continuo/pull/5', self.full())
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
        Path(os.sys.argv[2]).write_text(json.dumps(sequence_fixture() if os.environ.get('FM_BOARD_SEQUENCE_FIXTURE') else fixture()))
    else:
        unittest.main()
