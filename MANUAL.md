/Launch User Manual — v1.1

Overview
/Launch is a Windows desktop utility that manages and sequences the launch of sim‑racing applications and supporting tools. It provides per‑app CPU priority and affinity, optional admin launches (UAC), delayed launch sequencing, crash detection, and basic usage statistics.

Requirements
- Windows 10/11
- Python 3.9+
- Recommended packages: listed in requirements.txt (customtkinter, psutil, pillow, pystray, pywin32 optional)

Installation
1. Clone or extract the repository to a folder.
2. Install dependencies:
   pip install -r requirements.txt
3. (Optional) Create a desktop shortcut:
   python install_shortcut.py

First run
- Launch the app by running launch_dashboard.py or using the desktop shortcut.
- The default profile ("Default") is created automatically.

UI Overview
- Header: App name, version, profile selector, profile add/delete, Settings (⚙), Quit (⏻).
- Add App: Click "➕ Add App" and choose an executable (.exe).
- App Rows: Each app is shown as a row with icon, name, metadata (delay/priority), and controls:
  - ⚙: Edit settings for the app.
  - ▶: Launch the single app.
  - ■: Kill the running app (graceful terminate).
  - ×: Delete the app from the profile.
  - Drag handle: Reorder apps by dragging to set launch order.

Profiles
- Use the profile dropdown to switch between sets of apps.
- Click + to create a new profile and − to delete the active profile (Default cannot be deleted).
- Profiles are stored inside launch_config.json.

App Settings (per app)
- Name: Friendly display name.
- Path: Executable path (.exe). The app validates this on save.
- Delay: Seconds to wait after launching this app before starting the next in a sequence.
- Priority: Windows process priority (Realtime, High, Normal, etc.). Use Realtime with caution.
- Affinity: Select CPU cores to restrict the process (useful for separating game vs background processes).
- Run as Administrator: When checked, launching that app will trigger a UAC prompt (ShellExecute). Note: no PID is returned from ShellExecute, so some settings may be applied after the process starts (retry logic included).
- Enabled: Toggle to disable an app without removing it.

Launch Sequence
- "🚀 LAUNCH SEQUENCE" launches enabled apps in profile order, respecting per-app delays.
- If "Minimize on launch" is enabled in Settings, the app will minimize to tray during a sequence.
- Sequence will skip apps already running.

Process Management
- Process matching is done by normalized exe path to avoid duplicate launches.
- Graceful stop attempts a terminate(), then kill() on timeout.
- Priority and affinity are applied when possible; permission errors are logged if admin rights are required.

Crash Detection
- The app optionally monitors launched apps for unexpected exits (crashes).
- An exit is only treated as a crash when the process ended with a non-zero exit code. Closing an app normally is not recorded as a crash.
- For apps where no exit code is available (e.g. admin launches), only runs shorter than 30 seconds are treated as crashes.
- When a crash is detected, an entry (including the exit code) is added to crash_history.json and the app shows a notification.
- Crash detection continues while /Launch is minimized to the tray (checked every 10 seconds).
- Note: automatic restart of crashed apps is not yet implemented; crashes are detected and recorded only.

App Statistics
- launch counts, total runtime and average runtime are tracked in app_statistics.json.
- Basic statistics are shown on rows where available.

Tray & Hotkeys
- Closing the window minimizes the app to the system tray.
- Tray menu provides Show and Exit options.
- The ⏻ button in the header exits the app directly without going through the tray. Launched apps keep running.
- Global hotkeys are supported via the optional 'keyboard' package (not installed by default). Hotkeys are configurable in Settings when keyboard support is present.

Settings
Accessible from the header ⚙:
- Theme: Light, Dark, System (requires restart for full effect).
- Monitor Interval: How often the app checks process states (ms).
- Crash detection: Enable or disable crash monitoring.
- Auto-save interval: Periodic saving of config (ms).
- Sound notifications: Play sounds for important events (requires system audio support).
- Minimize on launch: Minimize app during launch sequences.

Logs & Troubleshooting
- All data files (config, settings, logs, statistics) are stored in the same folder as launch_dashboard.py, regardless of the working directory the app was started from.
- app.log: Main error and exception log.
- launch_config.json: Main user config. A .bak copy of the last known-good config is saved automatically before every write.
- If the config becomes corrupted, the broken file is preserved as launch_config.json.corrupt-<timestamp> and the config is restored automatically from the .bak backup.
- crash_history.json and app_statistics.json store analytics and crash reports for diagnostics.

Troubleshooting Tips
- Empty or placeholder icons: Install pywin32 (pip install pywin32) for better exe icon extraction.
- Priority/Affinity changes fail: The app may require admin rights to change another process's priority/affinity. Launch /Launch itself as admin to allow controlling other processes, or use the Run as Administrator checkbox when adding apps.
- App not launching: Confirm executable path is correct and the app isn't blocked by antivirus/UAC.
- Config corrupted: Recovery is automatic — the app restores from launch_config.json.bak and keeps the broken file as launch_config.json.corrupt-<timestamp> for inspection.

Privacy & Data
- Local-only telemetry: statistics and crash history are stored locally in JSON files. No data is sent externally.
- To clear analytics, delete app_statistics.json and crash_history.json.

Upgrading
- Replace the application files with the new release and restart. Settings and profiles are retained in launch_config.json.
- The app creates a backup of the config before overwriting.

Support
- Check app.log for errors.
- Include launch_config.json, app.log, crash_history.json and app_statistics.json when requesting help.

Appendix — Safe Defaults & Warnings
- Realtime priority can make a system unstable if misused. The app will warn when selecting dangerous options, but use caution and keep backups of your config.
- Always test new affinity/priority settings with a short session before committing to long races.

This manual documents v1.1 features and usage. For more advanced automation (plugins, REST API, remote telemetry) refer to future releases.
