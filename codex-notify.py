#!/usr/bin/env python3
"""Watch the terminal-title spinner used by the Codex working-border rule.

~/bin/codex-notify.py --start-watcher   Start watching all terminal windows.
~/bin/codex-notify.py --test            Test a clickable popup and sound.
~/bin/codex-notify.py --test-sound      Play one random sound.
Settings: ~/.config/codex-notify/config.toml (min_seconds).
"""

import argparse
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path
import re
import random
import select
import socket
import subprocess
import sys
import time
import tomllib

SCRIPT = str(Path(__file__).resolve())
CONFIG = Path.home() / '.config/codex-notify/config.toml'
CACHE = Path.home() / '.cache/codex-notify'
# Keep this spinner/title match aligned with hypr/codex-border.lua.
SPINNER = re.compile(r'^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏] ')
SETTLE_SECONDS = 1.0
SOUNDS = Path.home() / '.local/share/codex-notify/sounds'
AUDIO_EXTENSIONS = {'.mp3', '.wav', '.ogg', '.oga', '.opus', '.flac', '.m4a', '.aac', '.aif', '.aiff'}
_SOUND_CHILDREN = []


def minimum_seconds():
    config = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    value = config.get('min_seconds', 30)
    if isinstance(value, bool):
        raise ValueError('min_seconds must be a nonnegative number')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('min_seconds must be a nonnegative number')
    return value


def clients():
    return json.loads(subprocess.check_output(
        ['hyprctl', '-j', 'clients'], text=True, stderr=subprocess.DEVNULL, timeout=3,
    ))


def terminal(window):
    tags = [tag.rstrip('*') for tag in window.get('tags', [])]
    return 'terminal' in tags or any(t.startswith('codex-working-') for t in tags)


def ancestors():
    pid = os.getpid()
    seen = set()
    while pid > 0 and pid not in seen:
        seen.add(pid)
        yield pid
        try:
            fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            pid = int(fields[1])
        except (OSError, ValueError, IndexError):
            break


def capture_target():
    windows = clients()
    for pid in ancestors():
        matches = [w for w in windows if w.get('pid') == pid]
        if len(matches) > 1:
            active = json.loads(subprocess.check_output(
                ['hyprctl', '-j', 'activewindow'], text=True,
                stderr=subprocess.DEVNULL, timeout=3,
            ))
            matches = [w for w in matches if w.get('address') == active.get('address')]
        if len(matches) == 1:
            return {key: matches[0][key] for key in ('address', 'pid')}
    return None


def focus_target(target):
    address = target.get('address', '')
    if not isinstance(address, str) or not re.fullmatch(r'0x[0-9a-fA-F]+', address):
        raise ValueError('invalid window address')
    if not any(w.get('address') == address and w.get('pid') == target.get('pid')
               for w in clients()):
        return 0  # Never substitute a different terminal after this one closes.
    subprocess.run(
        ['hyprctl', 'dispatch', f'hl.dsp.focus({{ window = "address:{address}" }})'],
        check=True, timeout=3, stdout=subprocess.DEVNULL,
    )
    return 0


def play_random_sound():
    """Start a random clip without delaying other terminal notifications."""
    try:
        config = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
        enabled = config.get('sound_enabled', True)
        if not isinstance(enabled, bool):
            raise ValueError('sound_enabled must be true or false')
        if not enabled:
            return None
        folder = Path(config.get('sound_dir', str(SOUNDS))).expanduser()
        if not folder.is_dir():
            return None
        choices = sorted(p for p in folder.iterdir()
                         if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
        if not choices:
            return None
        clip = random.choice(choices)
        # Reap finished players on subsequent plays; retain running ones so
        # simultaneous terminal completions remain independent.
        _SOUND_CHILDREN[:] = [p for p in _SOUND_CHILDREN if p.poll() is None]
        process = subprocess.Popen(
            ['mpv', '--no-config', '--no-video', '--no-terminal',
             '--really-quiet', '--audio-display=no', '--', str(clip)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
        _SOUND_CHILDREN.append(process)
        return clip
    except (OSError, ValueError, TypeError) as error:
        # Audio trouble must never prevent the popup or stop the watcher.
        print(f'codex-notify: sound playback failed: {error}', file=sys.stderr, flush=True)
        return None


def send_popup(project, target, elapsed=None, test=False):
    play_random_sound()
    title = 'Codex notification test' if test else 'Codex stopped working'
    if project:
        title += ' — ' + project
    if elapsed is not None:
        title += f' ({elapsed:.0f}s)'
    action = [sys.executable, SCRIPT, '--focus', json.dumps(target)]
    subprocess.run([
        'notify-send', '--app-name=Codex', '--urgency=normal',
        '--expire-time=10000', '--icon=utilities-terminal',
        '--hint=string:omarchy-exec-argv:' + json.dumps(action),
        '--', title, html.escape('Click to return to this terminal.'),
    ], check=True, timeout=5)


class ActivityTracker:
    """Track independent busy intervals, bridging brief title flickers."""

    def __init__(self, send=send_popup, threshold=minimum_seconds):
        self.windows = {}
        self.send = send
        self.threshold = threshold

    def sync(self, windows, now):
        present = set()
        for window in windows:
            if not terminal(window):
                continue
            address = window['address']
            present.add(address)
            record = self.windows.get(address)
            if record is None or record['pid'] != window['pid']:
                self.windows[address] = {
                    'pid': window['pid'], 'started': None, 'stopped': None,
                    'project': '',
                }
            self.title(address, window.get('title', ''), now)
        for address in list(self.windows):
            if address not in present:
                del self.windows[address]

    def title(self, address, title, now):
        record = self.windows.get(address)
        if record is None:
            return
        if SPINNER.match(title):
            if record['started'] is None:
                record['started'] = now
            record['stopped'] = None
            record['project'] = SPINNER.sub('', title, count=1)
        elif record['started'] is not None and record['stopped'] is None:
            record['stopped'] = now

    def close(self, address):
        self.windows.pop(address, None)

    def timeout(self, now):
        deadlines = [r['stopped'] + SETTLE_SECONDS for r in self.windows.values()
                     if r['stopped'] is not None]
        return max(0, min(deadlines) - now) if deadlines else 30

    def flush(self, now):
        for address, record in list(self.windows.items()):
            stopped = record['stopped']
            if stopped is None or now < stopped + SETTLE_SECONDS:
                continue
            elapsed = max(0, stopped - record['started'])
            record['started'] = record['stopped'] = None
            if elapsed > self.threshold():
                self.send(record['project'], {'address': address, 'pid': record['pid']}, elapsed)


def address_value(raw):
    return '0x' + raw.removeprefix('0x')


def handle_event(tracker, line, now):
    event, separator, data = line.partition('>>')
    if not separator:
        return
    if event == 'windowtitlev2':
        raw_address, comma, title = data.partition(',')
        if comma:
            address = address_value(raw_address)
            if address not in tracker.windows:
                tracker.sync(clients(), now)
            tracker.title(address, title, now)
    elif event == 'closewindow':
        tracker.close(address_value(data))
    elif event in ('openwindow', 'configreloaded'):
        tracker.sync(clients(), now)


def watch():
    minimum_seconds()
    signature = os.environ.get('HYPRLAND_INSTANCE_SIGNATURE')
    if not signature:
        raise ValueError('HYPRLAND_INSTANCE_SIGNATURE is missing; run in your desktop session')
    runtime = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}'))
    socket_path = runtime / 'hypr' / signature / '.socket2.sock'
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_name = hashlib.sha256(signature.encode()).hexdigest()[:16] + '.lock'
    with (CACHE / lock_name).open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0  # One watcher per desktop, even with many Codex launches.
        while True:
            try:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
                    stream.connect(str(socket_path))
                    tracker = ActivityTracker()
                    tracker.sync(clients(), time.monotonic())
                    buffer = b''
                    while True:
                        ready, _, _ = select.select([stream], [], [], tracker.timeout(time.monotonic()))
                        if ready:
                            chunk = stream.recv(65536)
                            if not chunk:
                                break
                            buffer += chunk
                            while b'\n' in buffer:
                                line, buffer = buffer.split(b'\n', 1)
                                handle_event(tracker, line.decode('utf-8', errors='replace'), time.monotonic())
                        try:
                            tracker.flush(time.monotonic())
                        except (OSError, ValueError, subprocess.SubprocessError) as error:
                            print(f'codex-notify: notification failed: {error}', file=sys.stderr, flush=True)
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(f'codex-notify: waiting for Hyprland: {error}', file=sys.stderr, flush=True)
            # Logout removes the socket; exit instead of keeping stale sessions.
            if not socket_path.exists():
                return 0
            time.sleep(2)


def start_watcher():
    minimum_seconds()
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (CACHE / 'watcher.log').open('a') as log:
        subprocess.Popen(
            [sys.executable, SCRIPT, '--watch'], stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True,
        )


def main():
    # Previously launched sessions may still call the old hook command.
    # Accept it without blocking their prompts or producing duplicate popups.
    if sys.argv[1:2] in (['--start'], ['--target']):
        start_watcher()
        return 0
    if len(sys.argv) == 2 and sys.argv[1].lstrip().startswith('{'):
        start_watcher()
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--watch', action='store_true')
    mode.add_argument('--start-watcher', action='store_true')
    mode.add_argument('--test', action='store_true')
    mode.add_argument('--test-sound', action='store_true')
    mode.add_argument('--focus')
    args = parser.parse_args()
    if args.test_sound:
        clip = play_random_sound()
        if clip is None:
            print('No sound played; check sound_enabled and sound_dir.', file=sys.stderr)
            return 1
        print(f'Playing: {clip}')
        return 0
    if args.focus:
        return focus_target(json.loads(args.focus))
    if args.watch:
        return watch()
    if args.start_watcher:
        start_watcher()
        return 0
    target = capture_target()
    if target is None:
        raise ValueError('could not identify this terminal window')
    send_popup(Path.cwd().name, target, test=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f'codex-notify: {error}', file=sys.stderr)
        sys.exit(1)
