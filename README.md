# /Launch — Sim Racing Command Center

![tests](https://github.com/c0smuss/Launcher/actions/workflows/tests.yml/badge.svg)

A Windows desktop launcher for sim-racing setups. Add your companion apps (telemetry, head tracking, wheel software, overlays), set per-app CPU priority/affinity and launch delays, then start everything with one click.

## What's new in v1.3.1
- **Snappier UI** — editing or drag-reordering apps now updates rows in place instead of rebuilding the whole list (was a ~¼-second freeze).
- **Quieter on disk** — the autosave loop no longer rewrites an unchanged config every 5 seconds.
- **Cheaper monitoring** — crash detection checks the process handle instead of scanning the process table every tick.
- **Faster launch sequences** — the gap between delay-0 apps is now a setting (default 0.75 s, was a fixed 1.5 s), and nothing waits after the last app; a full stack launches in about half the time.
- **Crash detection now covers admin-launched apps** — watching begins once the app is first seen running (Windows provides no exit code for elevated launches, so only exits within ~30 s of appearing count as crashes).
- Fixed: the profile ⋮ menu now follows monitor DPI scaling and the app theme; a leaked menu per ⋮ click; back-to-back toasts being hidden early by the previous toast's timer.

## What's new in v1.3
- **One-click race day** — a `🏁 iRacing` desktop shortcut selects the iRacing profile, launches the whole stack, and sits in the tray, announcing completion with a native notification. Runs the app via CLI flags (`--profile`, `--launch`, `--minimized`).
- **Single instance** — launching a second time forwards to the running launcher instead of opening a duplicate that fights over the config.
- **Race Mode** (button + `Ctrl+Alt+R`) — closes apps from other profiles, turns on Windows Presentation Mode to silence notifications, and boosts the sim to High priority; everything reverts on toggle-off or exit.
- **Global hotkeys** — launch sequence, kill all, race mode, and show/hide, working even while the sim has focus (configurable in Settings).
- **Tray quick actions** — Launch Sequence, Race Mode, and Kill All from the tray menu.
- **Native notifications** when minimized to the tray.
- **Profile management** — rename, duplicate, and export/import profiles as JSON (under the `⋮` menu).
- **Statistics view** (📊) — a summary and per-app table built from the data collected since v1.0.
- **Start with Windows** — optional boot-to-tray via a Settings checkbox.
- **Update check** — an optional once-a-day check against this repo's latest version.
- **Live theme switching**, rotating logs, and an update checker round out the release.

## What's new in v1.2
- **Efficiency Mode (EcoQoS)** — per-app opt-in to Windows 11 power throttling: the scheduler runs the app on E-cores at low clocks (shows as "Efficiency mode" in Task Manager). Ideal for background helpers, leaving P-cores and thermal headroom to the sim. Configured in the app's ⚙ settings; eco apps show a 🍃 tag.
- **The launcher itself runs in Efficiency Mode** by default (toggle in Settings), minimizing its own footprint while you race.
- **Quit button (⏻)** in the header — exit directly without going through the tray menu.
- Fixed a "can't invoke winfo command" error dialog that could appear when quitting from the tray.
- Desktop shortcut now uses `pythonw.exe` (no console window) and the rocket icon; the app shows its own icon in the taskbar and tray instead of Python's.

## What's new in v1.1
- **Accurate crash detection** — apps closed normally (exit code 0) are no longer recorded as crashes; crash records now include the exit code.
- **Safe config recovery** — if `launch_config.json` is corrupted, the broken file is preserved as `launch_config.json.corrupt-<timestamp>` and the config is automatically restored from the `.bak` backup (which is never overwritten by a bad file).
- **Smoother UI** — process CPU/memory sampling is non-blocking, and kill operations run in the background instead of freezing the window.
- **Thread-safety fixes** — launch sequences and tray actions no longer touch the UI from background threads.
- **Tray monitoring** — process monitoring and crash detection keep running (at a slower 10s interval) while minimized to the tray.
- **Data file anchoring** — all config/log/stats files are stored next to the script, so launching from a shortcut with a different working directory can't create a second empty config.
- Fixed: `last_run` timestamps written to the wrong app during launch sequences, crash counts never incrementing in statistics, settings autosave loops stacking, and the edit dialog silently dropping fields.

## Features
- Launch sequencing with per-app delays and drag-to-reorder
- Per-app CPU priority, core affinity (P-core / E-core presets), and Efficiency Mode (EcoQoS)
- Run-as-Administrator support (UAC prompt)
- Multiple profiles with rename / duplicate / export / import
- Race Mode: close distractions, silence notifications, boost the sim
- Global hotkeys and tray quick actions
- One-click per-profile desktop shortcuts; optional start-with-Windows
- Live process status, crash detection, and a statistics view
- Minimize to system tray with native notifications

## Quick Start
1. Install requirements:
   ```
   pip install -r requirements.txt
   ```
2. (Optional) Create desktop shortcuts:
   ```
   python install_shortcut.py
   ```
   This creates **🚀Launch** (opens the launcher) and **🏁 iRacing** (launches the iRacing profile straight to the tray). Generate one for any profile with `python install_shortcut.py --profile "<name>"`.
3. Start the app:
   ```
   python launch_dashboard.py
   ```
   or double-click a desktop shortcut.

Requires Windows 10/11 and Python 3.9+. `keyboard` (in `requirements.txt`) enables global hotkeys; Efficiency Mode and Presentation Mode need Windows 11.

## Development
Run the test suite (pure-logic tests, no GUI):
```
pip install pytest
python -m pytest tests/ -q
```
Tests run automatically on every push via GitHub Actions (`windows-latest`, Python 3.13).

## How to enable "Run as Admin"
1. Add an app (e.g., CrewChief).
2. Click the ⚙ (gear) icon on the row.
3. Check "Run as Administrator".
4. Click Save.

When launching an app marked "Run as Administrator", Windows will show a UAC prompt.

## Notes
- **Icons:** If pywin32 is not available or an exe has no embedded icon, a placeholder square icon is shown.
- **Config:** Profiles and app lists are saved in `launch_config.json` next to the script. A `.bak` of the last good config is kept automatically and used for recovery if the config becomes corrupted.
- **Logs:** Errors are written to `app.log` in the same folder.
- **Data files** (`launch_config.json`, `app.log`, `crash_history.json`, `app_statistics.json`, `launcher_settings.json`) are machine-specific and not tracked in git.

See [MANUAL.md](MANUAL.md) for the full user manual.
