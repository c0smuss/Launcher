import os
import sys
import win32com.client
from win32com.shell import shell, shellcon

def create_shortcut():
    # Get paths
    current_dir = os.getcwd()
    target = sys.executable # Path to python.exe
    script_path = os.path.join(current_dir, "launch_dashboard.py")
    
    # Desktop path
    desktop = shell.SHGetFolderPath(0, shellcon.CSIDL_DESKTOP, None, 0)
    shortcut_path = os.path.join(desktop, "SimLaunch.lnk")
    
    # Create shortcut
    ws = win32com.client.Dispatch("WScript.Shell")
    shortcut = ws.CreateShortCut(shortcut_path)
    
    # FORCE python.exe (Console mode) to see errors if they happen
    shortcut.TargetPath = target
        
    shortcut.Arguments = f'"{script_path}"'
    shortcut.WorkingDirectory = current_dir
    shortcut.IconLocation = sys.executable
    shortcut.Description = "Sim Racing Launcher"
    shortcut.Save()
    
    print(f"Shortcut created at: {shortcut_path}")
    print("Note: This shortcut uses a console window so you can see startup errors.")

if __name__ == "__main__":
    try:
        create_shortcut()
        input("Success! Shortcut updated. Press Enter to exit...")
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")