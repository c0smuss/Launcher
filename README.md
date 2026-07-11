# /Launch — Sim Racing Command Center

A Windows desktop launcher for sim-racing setups. Add your companion apps (telemetry, head tracking, wheel software, overlays), set per-app CPU priority/affinity and launch delays, then start everything with one click.

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
- Per-app CPU priority and core affinity (P-core / E-core presets)
- Run-as-Administrator support (UAC prompt)
- Multiple profiles (e.g. one per sim, or non-racing sets)
- Live process status with memory/CPU readouts per app
- Crash detection with history, plus per-app launch/runtime statistics
- Minimize to system tray

## Quick Start
1. Install requirements:
   ```
   pip install -r requirements.txt
   ```
2. (Optional) Create a desktop icon:
   ```
   python install_shortcut.py
   ```
3. Start the app:
   ```
   python launch_dashboard.py
   ```
   or double-click the desktop "SimLaunch" icon.

Requires Windows 10/11 and Python 3.9+.

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
