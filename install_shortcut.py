import os
import sys
import win32com.client
from win32com.shell import shell, shellcon

def create_shortcut():
    # Anchor to the script's folder so the shortcut works no matter
    # where the installer is run from
    current_dir = os.path.dirname(os.path.abspath(__file__))
    # pythonw.exe runs without a console window; errors still go to app.log
    target = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(target):
        target = sys.executable
    script_path = os.path.join(current_dir, "launch_dashboard.py")

    # Desktop path
    desktop = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0)
    shortcut_path = os.path.join(desktop, "SimLaunch.lnk")

    # Create shortcut
    ws = win32com.client.Dispatch("WScript.Shell")
    shortcut = ws.CreateShortCut(shortcut_path)
    shortcut.TargetPath = target
    shortcut.Arguments = f'"{script_path}"'
    shortcut.WorkingDirectory = current_dir
    icon = os.path.join(current_dir, "car.ico")
    shortcut.IconLocation = icon if os.path.exists(icon) else target
    shortcut.Description = "Sim Racing Launcher"
    shortcut.Save()

    print(f"Shortcut created at: {shortcut_path}")

if __name__ == "__main__":
    try:
        create_shortcut()
        input("Success! Shortcut updated. Press Enter to exit...")
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")
