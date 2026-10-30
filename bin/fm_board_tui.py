"""Terminal crew board: Catppuccin Mocha pills over state/board-model.json.

Standard library only. fm-board.py writes the model; this module only draws it,
so no fleet, status, or backlog state is parsed here. One model feeds both the
interactive viewer (truecolor ANSI, OSC 8 links, alternate screen) and the
non-interactive `fm-board.py view --once` frame used by tests and documentation.

Colour and glyph choices (docs/crew-board.md "Terminal board"):
  yellow and peach need you, green ready to merge or done, blue running,
  grey waiting, queued and parked, mauve links and the selection, peach for
  a failure with an explicit label. FM_BOARD_ICONS=plain (or config/board-icons containing
  "plain") swaps Nerd Font powerline glyphs for plain Unicode; NO_COLOR removes
  colour and implies plain glyphs.
Keys: j/k move, Enter go to the worker's pane, o open PR (else brief), b brief,
r report, Tab/Shift-Tab section, 1-4 jump, f summary/full detail, PgUp/PgDn/Home/End scroll, q quit.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import select
import signal
import subprocess
import sys
import termios
import textwrap
import time
import tty
from urllib.parse import quote

C = dict(base='#1e1e2e', surface0='#313244', surface1='#45475a', overlay0='#6c7086',
         text='#cdd6f4', sub='#a6adc8', blue='#89b4fa', green='#a6e3a1', yellow='#f9e2af',
         peach='#fab387', mauve='#cba6f7', red='#f38ba8')
NERD = dict(capl='', capr='', soft='', db='', ask='', merge='',
            eye='', flag='', fail='', done='', run='', wait='',
            park='', need='', queued='', folder='', clock='\U000f0954',
            go='⏎', bar='▌')
PLAIN = dict(capl='', capr='', soft='|', db='$', ask='?', merge='+', eye='o', flag='!', fail='x',
             done='*', run='>', wait='.', park='=', need='!', queued='?', folder='', clock='',
             go='Enter', bar='|')
STATE_COLOUR = dict(done='green', run='blue', wait='sub', need='yellow', fail='peach', queued='sub')
SUMMARY_NEED, SUMMARY_MOVING = 4, 4


def glyphs():
    mode = os.environ.get('FM_BOARD_ICONS', '')
    if not mode and os.environ.get('FM_HOME'):
        path = Path(os.environ['FM_HOME']) / 'config/board-icons'
        mode = path.read_text().strip() if path.is_file() else ''
    return PLAIN if mode == 'plain' or os.environ.get('NO_COLOR') else NERD


def seg(text, fg=None, bg=None, bold=False, link=None, underline=False):
    return (text, dict(fg=C.get(fg, fg) if fg else None, bg=C.get(bg, bg) if bg else None,
                       bold=bold, link=link, underline=underline))


def width(parts):
    return sum(len(text) for text, _ in parts)


def pad_between(left, right, w):
    return left + [seg(' ' * max(1, w - width(left) - width(right)))] + right


def clip(text, n):
    return text if len(text) <= n else text[:max(0, n - 1)] + '…'


def fit(parts, w):
    out, used = [], 0
    for text, style in parts:
        if used + len(text) > w:
            text = text[:max(0, w - used)]
        if text:
            out.append((text, style))
        used += len(text)
        if used >= w:
            break
    return out


def pill(G, label, bg, fg='base', icon=None):
    body = (G[icon] + '  ' if icon and G.get(icon) else '') + label
    if not G['capl']:
        return [seg(' ' + body + ' ', fg, bg, True)]
    return [seg(G['capl'], bg), seg(' ' + body + ' ', fg, bg, True), seg(G['capr'], bg)]


def vault_of(path):
    for parent in Path(path).resolve().parents:
        if (parent / '.obsidian').is_dir():
            return parent
    return None


def file_url(path):
    """Obsidian deep link when the file is inside a vault, otherwise a file URL."""
    vault = vault_of(path)
    if vault:
        rel = Path(path).resolve().relative_to(vault).with_suffix('')
        return 'obsidian://open?vault=' + quote(vault.name, safe='') + '&file=' + quote(str(rel), safe='')
    return 'file://' + quote(str(path))


def detail_path(model):
    return str(Path(model.get('home') or '.') / 'data/board-full.md')


def item_links(model, item):
    links = [(p['label'], p['url']) for p in item.get('prs', [])]
    links += [(name.capitalize(), file_url(path)) for name, path in item.get('files', {}).items()]
    if Path(detail_path(model)).is_file() and item.get('scope', '') == '':
        links.append(('Detail', file_url(detail_path(model))))
    return links


SECTIONS = ('Needs you now', 'In motion', 'Queued', 'Parked')


def targets_of(model, section=None):
    if section is not None:
        if section == 0:
            return [dict(kind='need', item=i) for i in model.get('need', [])]
        if section == 1:
            return [dict(kind='step', item=s, programme=p) for p in model.get('programmes', []) for s in p['steps']]
        kind = ('queued', 'parked')[section - 2]
        return [dict(kind=kind, item=i) for i in model.get(kind, [])]

    return [dict(kind='need', item=i) for i in model.get('need', [])] + \
           [dict(kind='prog', item=p) for p in model.get('programmes', [])]


def target_step(target):
    """The item itself, or a programme's running step (else its first step)."""
    if target['kind'] != 'prog':
        return target['item']
    steps = target['item']['steps']
    return next((s for s in steps if s['state'] == 'run'), next((s for s in steps if s['state'] != 'done'), steps[0]))


def loc_line(G, loc):
    if not loc:
        return []
    return [seg(G['folder'] + ' ' + loc['workspace'], 'sub'), seg(' ' + G['soft'] + ' ', 'overlay0'),
            seg(loc['tab'], 'sub'), seg(' ' + G['soft'] + ' ', 'overlay0'), seg(loc['pane'], 'sub')]


def bar_line(G, model, w, section=None):
    c = model['counts']
    tabs = [('Need ' + str(c['need']), 'yellow' if c['need'] else 'surface0', 'base' if c['need'] else 'text'),
            ('Moving ' + str(c['moving']), 'surface0', 'text'), ('Queued ' + str(c['queued']), 'surface0', 'text'),
            ('Parked ' + str(c['parked']), 'surface0', 'text')]
    left = []
    for n, (label, bg, fg) in enumerate(tabs):
        if section is not None:
            bg, fg = ('mauve', 'base') if n == section else ('surface0', 'text')
            label = ('>' if n == section else '') + str(n + 1) + ' ' + label
        left += pill(G, label, bg, fg) + [seg(' ')]
    if width(left) > w:
        left = []
        for n, key in enumerate(('need', 'moving', 'queued', 'parked')):
            label = ('>' if n == section else '') + str(n + 1) + key[0].upper() + ':' + str(c[key])
            left += pill(G, label, 'mauve' if n == section else 'surface0', 'text')
    try:
        stamp = dt.datetime.fromisoformat(model['generated'].replace('Z', '+00:00')).astimezone().strftime('%H:%M')
    except (KeyError, ValueError):
        stamp = ''
    right = pill(G, (G['clock'] + '  ' if G['clock'] else '') + stamp, 'mauve') if stamp and w >= width(left) + 16 else []
    return pad_between(left, right, w) if right else left


def heading(text, colour, count=None, w=60):
    label = [seg('  ' + text, colour, bold=True)] + ([seg('  ' + count, 'sub')] if count else [])
    return label + [seg(' ' + '─' * max(0, w - width(label) - 3), 'surface1')]


def need_lines(G, model, entry, w, selected, expanded, full=False):
    item, mark = entry['item'], [seg(' ' + G['bar'] + ' ', 'mauve')] if selected else [seg('   ')]
    badge = pill(G, item['pill'], item['tone'], icon=item['icon']) + [seg(' ')]
    lines = [pad_between(mark + [seg(clip(item['title'], w - 3 - width(badge) - 2), 'text', bold=True,
        link=next((url for _, url in item_links(model, item)), None))], badge, w)]
    ask = textwrap.wrap(item['action'], w - 4) or ['']
    shown = ask[:4] if full else ask[:2] if expanded else ask[:1]
    if len(ask) > len(shown):
        shown[-1] = clip(shown[-1] + ' \u2026', w - 4) if len(shown[-1]) < w - 5 else shown[-1][:w - 5] + '\u2026'
    lines += [mark + [seg(line, 'text')] for line in shown]
    if expanded:
        links = []
        for n, (label, url) in enumerate(item_links(model, item)):
            links += ([seg(' ' + G['soft'] + ' ', 'overlay0')] if n else []) + [seg(label, 'mauve', link=url, underline=True)]
        if links:
            lines.append(mark + links)
        if item.get('loc'):
            go = [seg(G['go'] + ' go', 'mauve', bold=True)] if item.get('goto') else []
            lines.append(pad_between(mark + loc_line(G, item['loc']), go + [seg(' ')], w))
            if item.get('goto') and selected:
                lines.append(mark + [seg('$ bin/fm-goto.sh ' + item['id'], 'overlay0')])
    return lines


def programme_title(p):
    step = p['steps'][0]
    return p['group'] + ': ' + step['title'] if p['single'] else p['group'] + (' - ' + p['name'] if p['name'] else '')


def programme_line(G, entry, w, selected):
    p = entry['item']
    mark = [seg(' ' + G['bar'] + ' ', 'mauve')] if selected else [seg('   ')]
    chain = []
    for n, step in enumerate(p['steps'][:6]):
        chain += ([seg('─', 'overlay0')] if n else []) + [seg(G[step['state']], STATE_COLOUR[step['state']])]
    if len(p['steps']) > 6:
        chain.append(seg('+' + str(len(p['steps']) - 6), 'sub'))
    current = target_step(entry)
    word = current['word']
    if 'restarted after fix' in word:
        elapsed = re.search(r'(\d+[sm]) elapsed', word)
        word = 'CI restarted' + (' ' + elapsed[1] if elapsed else '')
    right = [seg(clip(word, max(14, w // 2)) + ' ', STATE_COLOUR[current['state']])]
    room = w - 3 - width(chain) - 2 - width(right) - 1
    return pad_between(mark + chain + [seg('  ' + clip(programme_title(p), room), 'text')], right, w)


def rail_lines(G, p, w, selected):
    mark = [seg(' ' + G['bar'] + ' ', 'mauve')] if selected else [seg('   ')]
    if p['single']:
        step = p['steps'][0]
        row = mark + [seg(G[step['state']] + ' ', STATE_COLOUR[step['state']]), seg(clip(programme_title(p), w - 22), 'text')]
        return [pad_between(row, [seg(step['word'] + ' ', STATE_COLOUR[step['state']])], w)]
    lines = [mark + [seg(clip(programme_title(p), w - 6), 'text', bold=True)]]
    if not p['single']:
        sub = f"{len(p['steps'])} steps" + (' in order' if p['linear'] else ', with dependencies') + \
              ('; dependency cycle, check before continuing' if p['cycle'] else '')
        lines.append(mark + [seg(sub, 'sub')])
    steps = p['steps']
    for n, step in enumerate(steps):
        colour = STATE_COLOUR[step['state']]
        after = '' if p['linear'] or not step['after'] else ' (after ' + ', '.join(step['after']) + ')'
        row = mark + [seg(G[step['state']] + ' ', colour), seg(clip(step['title'] + after, w - 22), 'text')]
        lines.append(pad_between(row, [seg(step['word'] + ' ', colour)], w))
        if step['after']:
            lines.extend(mark + [seg(line, 'sub')] for line in textwrap.wrap('Waiting on: ' + ', '.join(step['after']), max(10, w - 4)))
        if n < len(steps) - 1:
            dashed = steps[n + 1]['state'] in ('wait', 'queued')
            lines.append(mark + [seg('╎' if dashed else '│', 'overlay0' if dashed else colour)])
    return lines


def tally_text(rows, limit=3):
    shown = ', '.join(f'{name} {count}' for name, count in rows[:limit])
    return shown + (f', +{len(rows) - limit} more' if len(rows) > limit else '')


def quiet_lines(G, model, w, full):
    q, lines = model['quiet'], []
    done_by = {}
    for d in q['done']:
        done_by[d['group']] = done_by.get(d['group'], 0) + 1
    done_rows = sorted(done_by.items(), key=lambda kv: -kv[1])
    for icon, colour, label, rows, total in (
            ('queued', 'sub', 'decision queued' if model['counts']['queued'] == 1 else 'decisions queued', q['queued'], model['counts']['queued']),
            ('park', 'sub', 'parked', q['parked'], model['counts']['parked']),
            ('done', 'green', 'done recently', done_rows, len(q['done']))):
        head = [seg('  ' + G[icon] + '  ', colour), seg((str(total) + ' ' + label).ljust(22), 'text')]
        lines.append(head + [seg(clip(tally_text(rows, 99 if full else 3), w - width(head) - 1), 'sub')])
    if full:
        for d in q['done'][:12]:
            links = [seg(' ', None)] + [seg(p['label'], 'mauve', link=p['url'], underline=True) for p in d['prs'][:1]]
            lines.append(fit([seg('    ' + d['group'] + ': ' + clip(d['title'], w - 24), 'sub')] + links, w))
    return lines


def section_lines(model, w, selected, full, section):
    G, lines, span = glyphs(), [], (0, 0)
    targets = targets_of(model, section)
    lines += [bar_line(G, model, w, section)] + moved_line(model, w) + [[], heading(SECTIONS[section], 'mauve', str(len(targets)), w)]
    if section == 2:
        lines += [[seg('  Filed; not yet asked. Dependencies may wait.', 'sub')]]
    if not targets:
        lines += [[seg('  ' + ('Nothing needs you right now.', 'Nothing is running.', 'Nothing queued', 'Nothing parked')[section], 'sub')]]
    group = None
    for n, entry in enumerate(targets):
        item, chosen = entry['item'], n == selected
        start = len(lines)
        mark = [seg(' ' + G['bar'] + ' ' if chosen else '   ', 'mauve')]
        if section == 0:
            lines += need_lines(G, model, entry, w, chosen, chosen or full, full)
        elif section == 1:
            p = entry['programme']
            if group != p['key']:
                group = p['key']
                lines += [[seg('  ' + clip(programme_title(p), w - 3), 'text', bold=True)]]
                if p['cycle']:
                    lines += [[seg('  Dependency cycle: check before continuing', 'peach')]]
            state = item['state']
            after = ' (after ' + ', '.join(item['after']) + ')' if item['after'] and not p['linear'] else ''
            lines += [pad_between(mark + [seg(G[state] + ' ' + clip(item['title'] + after, w - 22), STATE_COLOUR[state])], [seg(item['word'], 'sub')], w)]
            if item.get('after'):
                lines.extend(mark + [seg(line, 'sub')] for line in textwrap.wrap('Waiting on: ' + ', '.join(item['after']), max(10, w - 4)))
            if chosen or full:
                lines += [mark + [seg(clip(item['action'], w - 3), 'sub')]]
                lines += [mark + loc_line(G, item.get('loc'))] if item.get('loc') else []
                lines += [mark + [seg(label + ' ', 'mauve', link=url, underline=True) for label, url in item_links(model, item)]]
            if item != p['steps'][-1]:
                lines += [mark + [seg('│' if state == 'run' else '╎', 'sub')]]
        else:
            if group != item['programme']:
                group = item['programme']
                lines += [[seg('  ' + clip(group, w - 3), 'text', bold=True)]]
            suffix = ' - filed ' + item['age'] if section == 2 else ''
            lines += [mark + [seg(clip(item['title'], max(8, w - len(suffix) - 3)) + suffix, 'text')]]
            if item.get('after'):
                lines += [mark + [seg(clip('Waiting on: ' + ', '.join(item['after']), w - 3), 'sub')]]
            if section == 3 or chosen or full:
                prefix = 'Bring back: ' if section == 3 else ''
                lines += [mark + [seg(clip(prefix + item['action'], w - 3), 'sub')]]
            if chosen or full:
                lines += [mark + [seg(label + ' ', 'mauve', link=url, underline=True) for label, url in item_links(model, item)]]
        if chosen:
            span = start, len(lines) - 1
    return lines, span


def moved_line(model, w):
    items = model.get('just_moved', [])
    if not items:
        return []
    age = max(0, time.time() - items[-1]['at'])
    item = items[-1 - (int(age // 5) % len(items))]
    text = 'Just moved: ' + item.get('compact', item['text'])
    return [[seg(clip(text, w), 'peach')]]


def build(model, w, selected, full, section=None):
    """Body lines plus the first and last line of the selected target."""
    if section is not None:
        return section_lines(model, w, selected, full, section)
    G = glyphs()
    targets, span, gap = targets_of(model), (0, 0), ([[]] if full else [])
    lines = [bar_line(G, model, w)] + moved_line(model, w) + [[]]
    need = [t for t in targets if t['kind'] == 'need']
    lines += [heading('Needs you now', 'yellow', f"{len(need)} item" + ('' if len(need) == 1 else 's'), w)] + gap
    if not need:
        lines.append([seg('  Nothing needs you right now.', 'sub')])
    for entry in (need if full else need[:SUMMARY_NEED]):
        chosen = targets.index(entry) == selected
        start = len(lines)
        lines += need_lines(G, model, entry, w, chosen, full, full) + gap
        span = (start, len(lines) - 1) if chosen else span
    if len(need) > SUMMARY_NEED and not full:
        lines.append([seg(f'  +{len(need) - SUMMARY_NEED} more, press 1', 'sub')])
    prog = [t for t in targets if t['kind'] == 'prog']
    lines += [[], heading('In motion', 'blue', f"{len(prog)} group" + ('' if len(prog) == 1 else 's'), w)] + gap
    if not prog:
        lines.append([seg('  Nothing is running.', 'sub')])
    for entry in (prog if full else prog[:SUMMARY_MOVING]):
        chosen = targets.index(entry) == selected
        start = len(lines)
        lines += (rail_lines(G, entry['item'], w, chosen) + gap) if full else [programme_line(G, entry, w, chosen)]
        span = (start, len(lines) - 1) if chosen else span
    if len(prog) > SUMMARY_MOVING and not full:
        lines.append([seg(f'  +{len(prog) - SUMMARY_MOVING} more, press f for all', 'sub')])
    lines += [[], heading('Quiet', 'sub', None, w)] + gap + quiet_lines(G, model, w, full)
    if model.get('notice'):
        lines += gap + [[seg('  ' + clip(model['notice'], w - 4), 'peach')]]
    return lines, span


def footer(G, w, message):
    rule = [seg('─' * w, 'surface1')]
    if message:
        return [rule, [seg(' ' + clip(message[0], w - 2), message[1])]]
    keys, out = [('Tab', 'next section'), ('j/k', 'move'), (G['go'], 'go'), ('o', 'PR'), ('b', 'brief'), ('r', 'report'), ('f', 'detail'), ('q', 'quit')], []
    for key, label in keys:
        part = [seg(' ' + key, 'mauve', bold=True), seg(' ' + label + ' ', 'sub')]
        if width(out) + width(part) <= w:
            out += part
    return [rule, out]


def frame(model, w, h, selected=0, full=False, off=0, message=None, section=None):
    """Visible rows (body then two footer rows) and the scroll offset that shows the selection."""
    body, (first, last) = build(model, w, selected, full, section)
    room = max(1, h - 2)
    if first < off:
        off = first
    if last >= off + room:
        off = last - room + 1
    off = max(0, min(off, max(0, len(body) - room)))
    if section is not None and off > 0 and room > 1:
        view = [body[0]] + body[off + 1:off + room]
    else:
        view = body[off:off + room]
    view += [[] for _ in range(room - len(view))]
    return [fit(line, w) for line in view + footer(glyphs(), w, message)], off, len(body)


def to_ansi(parts, color=True):
    out = ''
    for text, st in parts:
        codes = []
        if color:
            for key, base in (('fg', 38), ('bg', 48)):
                if st[key]:
                    r, g, b = (int(st[key][i:i + 2], 16) for i in (1, 3, 5))
                    codes.append(f'{base};2;{r};{g};{b}')
        if st['bold']:
            codes.append('1')
        if st['underline']:
            codes.append('4')
        link = st['link']
        out += (f'\x1b]8;;{link}\x1b\\' if link else '') + ('\x1b[' + ';'.join(codes) + 'm' if codes else '') + text + \
            ('\x1b[0m' if codes else '') + ('\x1b]8;;\x1b\\' if link else '')
    return out


def load_model(home):
    path = Path(home) / 'state/board-model.json'
    try:
        model = json.loads(path.read_text())
        return model if model.get('schema') == 'fm-board-model.v1' else None
    except (OSError, ValueError):
        return None


def opener():
    override = os.environ.get('FM_BOARD_OPEN')
    return override.split() if override else ['open'] if sys.platform == 'darwin' else ['xdg-open']


def act(key, entry, model, script):
    """Run one key action on the selected target; returns (message, colour)."""
    if entry is None:
        return 'Nothing is selected.', 'sub'
    step = target_step(entry)
    if key == '\r' and entry['kind'] in ('queued', 'parked'):
        url = file_url(step['detail'])
        subprocess.run(opener() + [url], capture_output=True, timeout=10)
        return 'Opened Detail.', 'green'
    if key == '\r':
        if not step.get('goto'):
            return 'No live pane is recorded for this item.', 'peach'
        result = subprocess.run([str(script / 'fm-goto.sh'), step['id']], capture_output=True, text=True, timeout=15,
                                env=dict(os.environ, FM_HOME=step['goto']['home']))
        text = (result.stdout or result.stderr).strip().splitlines()
        return (text[-1] if text else 'Went to the worker pane.'), 'green' if result.returncode == 0 else 'peach'
    links = item_links(model, step)
    wanted = {'o': [l for l in links if l[0].startswith('PR')] or [l for l in links if l[0] == 'Brief'],
              'b': [l for l in links if l[0] == 'Brief'], 'r': [l for l in links if l[0] == 'Report']}[key]
    if not wanted:
        return 'Nothing to open for this item.', 'peach'
    label, url = wanted[0]
    url = re.sub(r'^file://', '', url) if url.startswith('file://') else url
    subprocess.run(opener() + [url], capture_output=True, timeout=10)
    return 'Opened ' + label + '.', 'green'


def parse_keys(data):
    """Split one read into keys; escape sequences become names, unknown ones are dropped."""
    table = {b'\x1b[Z': 'backtab', b'\x1b[A': 'up', b'\x1b[B': 'down', b'\x1b[5~': 'pgup', b'\x1b[6~': 'pgdn', b'\x1b[H': 'home',
             b'\x1b[F': 'end', b'\x1b[1~': 'home', b'\x1b[4~': 'end', b'\x1bOA': 'up', b'\x1bOB': 'down'}
    keys, i = [], 0
    while i < len(data):
        for sequence, name in table.items():
            if data.startswith(sequence, i):
                keys.append(name)
                i += len(sequence)
                break
        else:
            if data[i] == 0x1b:
                i = len(data)  # an unrecognised escape sequence is ignored whole
            else:
                keys.append(chr(data[i]))
                i += 1
    return keys


def run(home, script):
    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        raise ValueError('the board viewer needs a terminal; use `fm-board.py view --once` for a single frame')
    saved = termios.tcgetattr(fd)
    resized = [True]
    signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))
    out = sys.stdout
    out.write('\x1b[?1049h\x1b[?25l')
    out.flush()
    tty.setcbreak(fd)
    selected, full, off, message, until, refreshed, mtime, process = 0, False, 0, None, 0, 0, None, None
    preference = Path(home) / 'state/board-view.json'
    session = os.environ.get('HERDR_SESSION', str(os.getsid(0)))
    try:
        prior = json.loads(preference.read_text())
        section = prior['section'] if prior['session'] == session and prior['section'] in range(4) else None
    except (OSError, ValueError, KeyError, TypeError):
        section = None
    previous, model = [], None
    try:
        while True:
            now = time.monotonic()
            if os.environ.get('FM_BOARD_DISABLED') != '1' and now - refreshed > 2 and (process is None or process.poll() is not None):
                process = subprocess.Popen([str(script / 'fm-board.sh'), 'refresh'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                refreshed = now
            path = Path(home) / 'state/board-model.json'
            stamp = tuple(p.stat().st_mtime_ns if p.exists() else None for p in
                          (path, Path(home) / 'data/board.md'))
            if stamp != mtime:
                mtime, model = stamp, load_model(home)
            columns, rows = os.get_terminal_size(out.fileno())
            if message and now > until:
                message = None
            if model is None:
                rows_out = [[seg('  Waiting for the board...', 'sub')]] + [[] for _ in range(rows - 3)] + footer(glyphs(), columns, None)
            else:
                count = len(targets_of(model, section))
                selected = max(0, min(selected, count - 1))
                rows_out, off, _ = frame(model, columns, rows, selected, full, off, message, section)
            color = not os.environ.get('NO_COLOR')
            ansi = [to_ansi(parts, color) for parts in rows_out]
            if resized[0] or len(previous) != len(ansi):
                out.write('\x1b[2J')
                previous, resized[0] = [None] * len(ansi), False
            for n, line in enumerate(ansi):
                if line != previous[n]:
                    out.write(f'\x1b[{n + 1};1H{line}\x1b[K')
            out.flush()
            previous = ansi
            if not select.select([fd], [], [], 0.2)[0]:
                continue
            for key in parse_keys(os.read(fd, 64)):
                targets = targets_of(model, section) if model else []
                if key == 'q':
                    return
                if key in ('\t', 'backtab', '1', '2', '3', '4'):
                    current = section if section is not None else 0
                    section = int(key) - 1 if key in '1234' else (current + (-1 if key == 'backtab' else 1)) % 4
                    selected, off, message = 0, 0, None
                    preference.write_text(json.dumps(dict(session=session, section=section)))
                elif key in ('j', 'down'):
                    selected += 1
                elif key in ('k', 'up'):
                    selected -= 1
                elif key == 'f':
                    full, off = not full, 0
                elif key == 'pgdn':
                    off += max(1, rows - 3)
                elif key == 'pgup':
                    off = max(0, off - max(1, rows - 3))
                elif key == 'home':
                    selected, off = 0, 0
                elif key == 'end':
                    selected = max(0, len(targets) - 1)
                elif key in ('\r', '\n', 'o', 'b', 'r') and targets:
                    selected = max(0, min(selected, len(targets) - 1))
                    try:
                        message = act('\r' if key == '\n' else key, targets[selected], model, script)
                    except (OSError, subprocess.SubprocessError) as exc:
                        message = (str(exc), 'peach')
                    until = time.monotonic() + 4
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        out.write('\x1b[?25h\x1b[?1049l')
        out.flush()


def once(home, w, h, selected, full, section=None):
    model = load_model(home)
    if model is None:
        raise ValueError('no board model yet; run `fm-board.py render` first')
    section = section - 1 if section is not None else None
    rows, _, _ = frame(model, w, h or len(build(model, w, selected, full, section)[0]) + 2, selected, full, section=section)
    color = not os.environ.get('NO_COLOR')
    sys.stdout.write('\n'.join(to_ansi(parts, color) for parts in rows) + '\n')
