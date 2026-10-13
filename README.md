# Bongdong Codex Omarchy

Feedback for Codex CLI in Omarchy:

- A vivid window border while Codex works, chosen from the current theme.
- A popup when a terminal becomes idle after more than 30 seconds. Clicking it focuses that terminal.
- A random sound from your sound folder alongside each qualifying popup.

It watches Codex's title spinner, with independent timers for each terminal window. Brief title flickers are ignored. Becoming idle includes cancellation or waiting for input. Use ordinary `codex`; no Codex hooks or launcher overrides are needed.

## Install

Requires Omarchy with [Lua Hyprland configuration](https://wiki.hypr.land/Configuring/Basics/Window-Rules/), Python 3.11+, `hyprctl`, `notify-send` (libnotify), `mpv`, and `luac` (lua).

```bash
git clone https://github.com/brontosaurusrex/bongdong-codex-omarchy.git
cd bongdong-codex-omarchy
./install --dry-run  # Preview file changes
./install
```

The installer preserves existing settings, migrates the original border block out of `mine.lua`, and backs up changed files under `~/.cache/codex-notify/backups/`. It reloads Hyprland and restarts the watcher. Use `--no-reload` to install for the next login instead.

Installed files:

- `~/bin/codex-notify.py` — watcher, popups, audio, and click-to-focus.
- `~/.config/hypr/codex-border.lua` — theme-aware border, loaded by `mine.lua`.
- `~/.config/hypr/autostart.lua` — watcher startup at desktop login.
- `~/.config/codex-notify/config.toml` — settings; existing values are preserved.
- `~/.local/share/codex-notify/sounds/` — audio files; `turkey.mp3` is included.

## Settings and commands

Edit `~/.config/codex-notify/config.toml`:

```toml
min_seconds = 30
sound_enabled = true
sound_dir = "~/.local/share/codex-notify/sounds"
```

Add MP3, WAV, OGG, Opus, FLAC, M4A, AAC, or AIFF files directly to the sound folder. Changes to settings and sounds apply without restarting.

```bash
~/bin/codex-notify.py --test             # Popup and sound
~/bin/codex-notify.py --test-sound       # Sound only
~/bin/codex-notify.py --start-watcher    # Start manually
pkill -f '[c]odex-notify.py --watch'      # Stop until next login
```

Watcher log: `~/.cache/codex-notify/watcher.log`. Run package checks with `./check`.
