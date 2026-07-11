import os
import sys
import argparse
import win32com.client
from win32com.shell import shell, shellcon


def _pythonw() -> str:
    """pythonw.exe runs without a console window; errors still go to app.log."""
    target = sys.executable.replace("python.exe", "pythonw.exe")
    return target if os.path.exists(target) else sys.executable


def _make_shortcut(lnk_name: str, icon_filename: str, extra_args: str = ""):
    """Create a desktop .lnk targeting pythonw launch_dashboard.py [extra_args].

    WScript.Shell cannot save a filename containing emoji, so we save under an
    ASCII temp name and rename to the (possibly emoji) final name afterwards."""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "launch_dashboard.py")
    desktop = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0)
    tmp_path = os.path.join(desktop, "_launch_tmp.lnk")
    final_path = os.path.join(desktop, lnk_name)

    ws = win32com.client.Dispatch("WScript.Shell")
    shortcut = ws.CreateShortCut(tmp_path)
    shortcut.TargetPath = _pythonw()
    args = f'"{script_path}"'
    if extra_args:
        args += f" {extra_args}"
    shortcut.Arguments = args
    shortcut.WorkingDirectory = current_dir
    icon = os.path.join(current_dir, icon_filename)
    shortcut.IconLocation = icon if os.path.exists(icon) else _pythonw()
    shortcut.Description = "Sim Racing Launcher"
    shortcut.Save()

    if os.path.exists(final_path):
        os.remove(final_path)
    os.rename(tmp_path, final_path)

    # The console may not encode the emoji name (cp1252)
    try:
        print(f"Shortcut created: {final_path}")
    except UnicodeEncodeError:
        print(f"Shortcut created on Desktop: {os.path.basename(final_path).encode('ascii', 'replace').decode()}")


def make_default_shortcuts():
    """The plain launcher plus the one-click iRacing profile shortcut."""
    _make_shortcut("🚀Launch.lnk", "1f680.ico")
    _make_shortcut("🏁 iRacing.lnk", "car.ico", '--profile "iRacing" --launch --minimized')


def make_profile_shortcut(profile: str):
    """A one-click launch shortcut for any profile (use after renaming one)."""
    _make_shortcut(f"🏁 {profile}.lnk", "car.ico", f'--profile "{profile}" --launch --minimized')


def main():
    parser = argparse.ArgumentParser(description="Create /Launch desktop shortcuts")
    parser.add_argument("--profile", help="Create a one-click launch shortcut for this profile")
    args = parser.parse_args()
    if args.profile:
        make_profile_shortcut(args.profile)
    else:
        make_default_shortcuts()


if __name__ == "__main__":
    try:
        main()
        input("Success! Shortcut(s) updated. Press Enter to exit...")
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")
