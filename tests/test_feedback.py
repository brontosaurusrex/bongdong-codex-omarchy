import importlib.machinery
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import tomllib

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


notifier = load('feedback_notifier', ROOT / 'codex-notify.py')
installer = load('feedback_installer', ROOT / 'install')


def window(address='0xaaa', pid=11, title='codex', tags=None):
    return {'address': address, 'pid': pid, 'title': title,
            'tags': ['terminal*'] if tags is None else tags}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.send = Mock()
        self.tracker = notifier.ActivityTracker(self.send, lambda: 30)
        self.tracker.sync([window()], 0)

    def test_duration_boundary_and_independent_windows(self):
        self.tracker.sync([window(), window('0xbbb', 22)], 0)
        self.tracker.title('0xaaa', '⠋ first', 100)
        self.tracker.title('0xbbb', '⠙ second', 110)
        self.tracker.title('0xaaa', 'first', 140)
        self.tracker.title('0xbbb', 'second', 140)
        self.tracker.flush(141)
        self.tracker.flush(145)
        self.send.assert_called_once_with('first', {'address': '0xaaa', 'pid': 11}, 40)

    def test_flicker_is_bridged_and_next_turn_gets_fresh_timer(self):
        self.tracker.title('0xaaa', '⠋ project', 100)
        self.tracker.title('0xaaa', 'project', 140)
        self.tracker.title('0xaaa', '⠙ project', 140.5)
        self.tracker.flush(142)
        self.send.assert_not_called()
        self.tracker.title('0xaaa', 'project', 150)
        self.tracker.flush(151)
        self.tracker.title('0xaaa', '⠙ project', 160)
        self.tracker.title('0xaaa', 'project', 165)
        self.tracker.flush(166)
        self.send.assert_called_once_with('project', {'address': '0xaaa', 'pid': 11}, 50)

    def test_closed_windows_do_not_notify(self):
        self.tracker.title('0xaaa', '⠋ project', 100)
        self.tracker.title('0xaaa', 'project', 140)
        self.tracker.close('0xaaa')
        self.tracker.flush(141)
        self.send.assert_not_called()

    def test_real_dynamic_terminal_tags_and_nonterminal_filter(self):
        self.assertTrue(notifier.terminal(window(tags=['terminal*', 'default-opacity*'])))
        self.assertTrue(notifier.terminal(window(tags=['terminal'])))
        self.assertFalse(notifier.terminal(window(tags=['firefox-based-browser*'])))

    def test_title_event_with_commas(self):
        notifier.handle_event(self.tracker, 'windowtitlev2>>aaa,⠋ project, name', 100)
        notifier.handle_event(self.tracker, 'windowtitlev2>>aaa,project, name', 140)
        self.tracker.flush(141)
        self.assertEqual(self.send.call_args.args[0], 'project, name')

    def test_click_action_checks_address_and_pid(self):
        target = {'address': '0xaaa', 'pid': 11}
        with patch.object(notifier, 'clients', return_value=[window(pid=999)]), patch.object(notifier.subprocess, 'run') as run:
            notifier.focus_target(target)
            run.assert_not_called()
        with patch.object(notifier, 'clients', return_value=[window('0xbbb', 22), window()]), patch.object(notifier.subprocess, 'run') as run:
            notifier.focus_target(target)
            self.assertIn('address:0xaaa', run.call_args.args[0][-1])


class SoundTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.sounds = Path(self.directory.name) / 'sounds'
        self.sounds.mkdir()
        self.config = Path(self.directory.name) / 'config.toml'
        self.config.write_text(f'min_seconds = 30\nsound_dir = "{self.sounds}"\n')
        self.config_patch = patch.object(notifier, 'CONFIG', self.config)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        notifier._SOUND_CHILDREN.clear()

    def test_random_audio_filter_and_nonblocking_player(self):
        for name in ('a.mp3', 'b.WAV', 'notes.txt'):
            (self.sounds / name).write_bytes(b'test')
        (self.sounds / 'folder.ogg').mkdir()
        with patch.object(notifier.random, 'choice', side_effect=lambda choices: choices[1]) as choose, patch.object(notifier.subprocess, 'Popen') as play:
            clip = notifier.play_random_sound()
            self.assertEqual(clip, self.sounds / 'b.WAV')
            self.assertEqual(choose.call_args.args[0], [self.sounds / 'a.mp3', self.sounds / 'b.WAV'])
            self.assertEqual(play.call_args.args[0][-2:], ['--', str(clip)])
            play.return_value.wait.assert_not_called()

    def test_empty_folder_and_disabled_audio(self):
        with patch.object(notifier.subprocess, 'Popen') as play:
            self.assertIsNone(notifier.play_random_sound())
            (self.sounds / 'a.mp3').write_bytes(b'test')
            self.config.write_text(f'sound_enabled = false\nsound_dir = "{self.sounds}"\n')
            self.assertIsNone(notifier.play_random_sound())
            play.assert_not_called()

    def test_audio_failure_cannot_suppress_popup(self):
        (self.sounds / 'a.mp3').write_bytes(b'test')
        with patch.object(notifier.subprocess, 'Popen', side_effect=FileNotFoundError('mpv missing')), patch.object(notifier.subprocess, 'run') as popup, patch.object(sys, 'stderr', io.StringIO()):
            notifier.send_popup('project', {'address': '0xaaa', 'pid': 11}, 40)
            popup.assert_called_once()


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='codex feedback test ')
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.hypr = self.home / '.config/hypr'
        self.hypr.mkdir(parents=True)
        (self.hypr / 'hyprland.lua').write_text('require("default.hypr.omarchy")\n')
        (self.hypr / 'mine.lua').write_text('hl.config({ animations = { enabled = true } })\n')
        (self.hypr / 'autostart.lua').write_text('o.exec_on_start("existing-personal-service")\n')

    def test_install_and_repeat_are_safe_and_preserve_settings(self):
        config = self.home / '.config/codex-notify/config.toml'
        config.parent.mkdir()
        config.write_text('min_seconds = 47\nsound_enabled = false\n')
        sounds = self.home / '.local/share/codex-notify/sounds'
        sounds.mkdir(parents=True)
        (sounds / 'turkey.mp3').write_bytes(b'existing-user-sound')
        plan = installer.make_plan(self.home)
        installer.validate(plan)
        backup, originals = installer.write_plan(plan, self.home)
        self.assertIsNotNone(backup)
        self.assertEqual((sounds / 'turkey.mp3').read_bytes(), b'existing-user-sound')
        settings = tomllib.loads(config.read_text())
        self.assertEqual(settings['min_seconds'], 47)
        self.assertFalse(settings['sound_enabled'])
        self.assertIn('existing-personal-service', (self.hypr / 'autostart.lua').read_text())
        self.assertIn('animations', (self.hypr / 'mine.lua').read_text())
        self.assertEqual((self.hypr / 'mine.lua').read_text().count('require("hypr.codex-border")'), 1)
        self.assertTrue((self.home / 'bin/codex-notify.py').stat().st_mode & 0o100)
        second_backup, second_originals = installer.write_plan(installer.make_plan(self.home), self.home)
        self.assertIsNone(second_backup)
        self.assertEqual(second_originals, {})

    def test_migrate_original_border_without_personal_animation_loss(self):
        personal = 'hl.animation({ leaf = "windowsOut", speed = 4 })\n\n'
        (self.hypr / 'mine.lua').write_text(personal + (ROOT / 'hypr/codex-border.lua').read_text())
        (self.hypr / 'autostart.lua').write_text('o.exec_on_start("python3 /home/b/bin/codex-notify.py --start-watcher")\n')
        plan = installer.make_plan(self.home)
        migrated = plan[self.hypr / 'mine.lua'][0].decode()
        self.assertTrue(migrated.startswith(personal))
        self.assertNotIn('local codex_palette', migrated)
        self.assertEqual(migrated.count('require("hypr.codex-border")'), 1)
        startup = plan[self.hypr / 'autostart.lua'][0].decode()
        self.assertEqual(startup.count('--start-watcher'), 1)
        self.assertIn(str(self.home), startup)

    def test_custom_border_is_not_silently_overwritten(self):
        text = 'local codex_working_rules = { border_color = "custom" }\n'
        (self.hypr / 'mine.lua').write_text(text)
        with self.assertRaises(ValueError): installer.make_plan(self.home)
        self.assertEqual((self.hypr / 'mine.lua').read_text(), text)
        self.assertFalse((self.home / 'bin').exists())

    def test_dotfile_symlink_is_preserved(self):
        target = self.home / 'personal-mine.lua'
        target.write_text((self.hypr / 'mine.lua').read_text())
        (self.hypr / 'mine.lua').unlink()
        (self.hypr / 'mine.lua').symlink_to(target)
        installer.write_plan(installer.make_plan(self.home), self.home)
        self.assertTrue((self.hypr / 'mine.lua').is_symlink())
        self.assertIn('hypr.codex-border', target.read_text())

    def test_failed_file_write_rolls_back_previous_changes(self):
        first = self.home / 'first.txt'
        second = self.home / 'second.txt'
        first.write_text('original')
        original_write = installer.atomic_write
        def failing_write(path, content, mode):
            if path == second:
                raise OSError('simulated disk error')
            return original_write(path, content, mode)
        with patch.object(installer, 'atomic_write', side_effect=failing_write):
            with self.assertRaises(OSError):
                installer.write_plan({first: (b'updated', 0o644), second: (b'new', 0o644)}, self.home)
        self.assertEqual(first.read_text(), 'original')
        self.assertFalse(second.exists())


if __name__ == '__main__':
    unittest.main()
