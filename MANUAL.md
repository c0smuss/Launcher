/Launch User Manual — v1.3

Overview
/Launch is a Windows desktop utility that manages and sequences the launch of sim‑racing applications and supporting tools. It provides per‑app CPU priority, affinity and Efficiency Mode, optional admin launches (UAC), delayed launch sequencing, profiles, race-day automation, global hotkeys, crash detection, and usage statistics.

Requirements
- Windows 10/11 (Efficiency Mode and Presentation Mode require Windows 11)
- Python 3.9+
- Packages: listed in requirements.txt (customtkinter, psutil, pillow, pystray, pywin32, keyboard)

Installation
1. Clone or extract the repository to a folder.
2. Install dependencies:
   pip install -r requirements.txt
3. (Optional) Create desktop shortcuts:
   python install_shortcut.py
   Creates "🚀Launch" (opens the launcher) and "🏁 iRacing" (launches the iRacing profile to the tray). For any other profile: python install_shortcut.py --profile "<name>"

First run
- Launch the app by running launch_dashboard.py or using a desktop shortcut.
- The default profile ("Default") is created automatically.

Command-line arguments
launch_dashboard.py accepts:
- --profile "<name>"  Select a profile at startup (case-insensitive; unknown names are ignored with a notification).
- --launch            Run the selected profile's launch sequence once the UI is up.
- --minimized         Start minimized to the system tray.
The "🏁 iRacing" shortcut uses all three. If the launcher is already running, a second invocation forwards these to the running instance instead of opening a second window.

UI Overview
- Header: App name, version, profile selector, profile menu (⋮), Settings (⚙), Quit (⏻).
- Actions row: ➕ Add App, 🚀 LAUNCH SEQUENCE, 📊 (statistics), 🏁 RACE MODE, ☠ KILL ALL.
- App Rows: Each app is shown as a row with icon, name, metadata (delay/priority/🍃 eco), and controls:
  - ⚙: Edit settings for the app.
  - ▶: Launch the single app.
  - ■: Kill the running app (graceful terminate).
  - ×: Delete the app from the profile.
  - Drag handle: Reorder apps by dragging to set launch order.

Profiles
- Use the profile dropdown to switch between sets of apps.
- The ⋮ menu offers: New, Rename, Duplicate, Delete, Export, Import.
  - Delete is allowed for any profile as long as at least one remains.
  - Rename: if the renamed profile had a desktop shortcut, regenerate it with python install_shortcut.py --profile "<new name>" (the shortcut freezes the old name).
  - Export writes a .launchprofile.json file: {"launch_profile_version": 1, "name": ..., "apps": [...]}. Import accepts that format; apps with missing exe paths are imported and counted, malformed files are rejected.
- Profiles are stored inside launch_config.json.

App Settings (per app)
- Name: Friendly display name.
- Path: Executable path (.exe). The app validates this on save.
- Delay: Seconds to wait after launching this app before starting the next in a sequence.
- Priority: Windows process priority (Realtime, High, Normal, etc.). Use Realtime with caution.
- Affinity: Select CPU cores to restrict the process (useful for separating game vs background processes).
- Efficiency Mode (EcoQoS): Opts the app into Windows 11 power throttling — the scheduler prefers E-cores at low clock speeds and Task Manager shows "Efficiency mode". Great for background helpers (paint downloaders, daemons); avoid for latency-sensitive apps like head tracking. Requires Windows 11 (silently ignored on older versions). Eco apps show a 🍃 tag in their row.
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

Race Mode
- Toggle with the 🏁 RACE MODE button or Ctrl+Alt+R. It prepares the machine for a session and reverts everything when toggled off (or when you quit).
- On enable it can (each gated by a Settings checkbox):
  - Close apps that belong to your other profiles (e.g. the Plex/qBittorrent set). This is a forceful terminate with no save prompt, same as Kill All — don't rely on it for apps mid-write. Apps in the active profile and configured sim executables are never closed.
  - Turn on Windows Presentation Mode, which suppresses notification toasts and popups. This is the only supported mechanism (there is no public Focus Assist API) and requires Windows Pro/11.
  - Raise the sim to High priority when it is running (or as soon as it appears).
- On disable it turns Presentation Mode off and restores normal priority. It does NOT relaunch the apps it closed.
- Sim executables and extra apps to close are configured in Settings → Race Mode (comma-separated). The default sim exe is iRacingSim64DX11.exe.

App Statistics
- Launch counts, total/average runtime, and crash counts are tracked in app_statistics.json.
- Click 📊 to open the statistics view: a summary strip plus a per-app table sorted by launch count, with a Reset button.
- Note: runtime is only accumulated while the launcher window is visible (it is driven by row status changes), so totals under-count apps that are closed while /Launch is minimized to the tray.

Tray & Hotkeys
- Closing the window minimizes the app to the system tray.
- Tray menu: Show, Launch Sequence, Race Mode (checkmark reflects state), Kill All, Exit.
- The ⏻ button in the header exits the app directly without going through the tray. Launched apps keep running.
- When minimized, event notifications (sequence complete, crashes, race mode) appear as native Windows notifications instead of in-window toasts.
- Global hotkeys (via the 'keyboard' package, included in requirements.txt) work even while another app has focus. Defaults:
  - Ctrl+Alt+L — Launch sequence
  - Ctrl+Alt+K — Kill all
  - Ctrl+Alt+R — Race mode
  - Ctrl+Alt+Space — Show / hide window
  Enable/disable and rebind them in Settings → Hotkeys.
  Limitation: a non-elevated launcher's hotkeys will not fire while an elevated (admin) window has focus — this is a Windows security boundary, not a bug.

Settings
Accessible from the header ⚙:
- Theme: Light, Dark, System. Applies live (no restart needed).
- Monitor Interval: How often the app checks process states (ms).
- Crash detection: Enable or disable crash monitoring.
- Auto-save interval: Periodic saving of config (ms).
- Minimize on launch: Minimize app during launch sequences.
- Run launcher in Efficiency Mode (EcoQoS): On by default — /Launch itself is scheduled on E-cores at low power so it stays out of the sim's way. Takes effect immediately when toggled.
- Start with Windows (minimized to tray): Adds/removes an HKCU Run entry that launches the app minimized at logon. The checkbox reflects the actual registry state, so toggling startup in Task Manager stays in sync. Moving the app folder orphans the entry — re-save Settings to fix. To auto-launch a profile at boot, edit the Run entry to add --profile "<name>" --launch, or just click a 🏁 shortcut when you want the stack up.
- Check for updates on startup: Enables a once-a-day version check (see Privacy & Data).
- Hotkeys: Enable/disable global hotkeys and rebind each action.
- Race Mode: Toggle which actions Race Mode performs and configure the sim executables and extra apps to close.

Logs & Troubleshooting
- All data files (config, settings, logs, statistics) are stored in the same folder as launch_dashboard.py, regardless of the working directory the app was started from.
- app.log: Main error/diagnostic log at INFO level. It rotates automatically at ~512 KB (app.log.1, app.log.2) so it never grows without bound.
- launch_config.json: Main user config. A .bak copy of the last known-good config is saved automatically before every write.
- If the config becomes corrupted, the broken file is preserved as launch_config.json.corrupt-<timestamp> and the config is restored automatically from the .bak backup.
- crash_history.json and app_statistics.json store analytics and crash reports for diagnostics.

Troubleshooting Tips
- Empty or placeholder icons: Install pywin32 (pip install pywin32) for better exe icon extraction.
- Priority/Affinity changes fail: The app may require admin rights to change another process's priority/affinity. Launch /Launch itself as admin to allow controlling other processes, or use the Run as Administrator checkbox when adding apps.
- App not launching: Confirm executable path is correct and the app isn't blocked by antivirus/UAC.
- Config corrupted: Recovery is automatic — the app restores from launch_config.json.bak and keeps the broken file as launch_config.json.corrupt-<timestamp> for inspection.

Privacy & Data
- Statistics and crash history are stored locally in JSON files; none of it is sent externally.
- The only outbound network request is the optional update check: at most once per day, /Launch fetches a small VERSION file from this project's GitHub repository to compare version numbers. Disable it with Settings → "Check for updates on startup".
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

This manual documents v1.3 features and usage. For more advanced automation (plugins, REST API, remote telemetry) refer to future releases.
