import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import os
import subprocess
import json
import socket
import argparse
import copy
import threading
import time
import psutil
import logging
from logging.handlers import RotatingFileHandler
import traceback
import shutil
import ctypes
from PIL import Image, ImageDraw
import pystray
import queue
from typing import Optional, Tuple
import ctypes.wintypes
from datetime import datetime
import webbrowser
from pathlib import Path
from collections import defaultdict
import winreg
import sys

# --- GLOBAL CONSTANTS ---
APP_NAME = "/Launch"
VERSION = "1.2.0"

# Anchor all data files to the script's folder so launching from a different
# working directory (e.g. a shortcut) doesn't create a fresh empty config.
BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = str(BASE_DIR / "launch_config.json")
LOG_FILE = str(BASE_DIR / "app.log")
ICON_FILE = str(BASE_DIR / "1f680.ico")

# Files created by analytics / settings
SETTINGS_FILE = str(BASE_DIR / "launcher_settings.json")
CRASH_LOG_FILE = str(BASE_DIR / "crash_history.json")
STATS_FILE = str(BASE_DIR / "app_statistics.json")

DEFAULT_SETTINGS = {
    "theme": "Dark",
    "auto_save_interval": 5000,
    "monitor_interval": 2000,
    "startup_apps": [],
    "minimize_on_launch": False,
    "launcher_eco_mode": True,
    "check_updates": True,
    "crash_detection": True,
    "race_mode_close_other_profiles": True,
    "race_mode_presentation_mode": True,
    "race_mode_boost_sim": True,
    "race_mode_custom_kill": [],
    "race_mode_sim_exes": ["iRacingSim64DX11.exe"],
    "global_hotkeys": True,
    "keyboard_shortcuts": {
        "launch_seq": "ctrl+alt+l",
        "kill_all": "ctrl+alt+k",
        "race_mode": "ctrl+alt+r",
        "toggle_window": "ctrl+alt+space",
    },
}

def migrate_hotkey_settings(settings: dict) -> dict:
    """Bring keyboard_shortcuts up to the current key set: replace legacy
    Tk-style values ('<Control-l>') with the keyboard-library defaults, drop
    obsolete keys, and keep any user-set modern combos. Mutates and returns."""
    defaults = DEFAULT_SETTINGS["keyboard_shortcuts"]
    old = settings.get("keyboard_shortcuts") or {}
    new = {}
    for key, default in defaults.items():
        val = old.get(key)
        new[key] = default if (not val or (isinstance(val, str) and val.startswith("<"))) else val
    settings["keyboard_shortcuts"] = new
    return settings

# --- SINGLE-INSTANCE IPC ---
# A bound localhost socket doubles as the instance lock and command channel.
IPC_HOST = "127.0.0.1"
IPC_PORT = 48653
IPC_BANNER_APP = "slaunch"
_IPC_MAX_BYTES = 4096
_IPC_ACTIONS = ("show", "launch")

def parse_ipc_message(line: str) -> Optional[dict]:
    """Parse one line of IPC command JSON. Returns a dict whose 'action' is
    whitelisted, or None for anything malformed / oversized / unknown."""
    if not line or len(line) > _IPC_MAX_BYTES:
        return None
    try:
        msg = json.loads(line)
    except Exception:
        return None
    if not isinstance(msg, dict) or msg.get("action") not in _IPC_ACTIONS:
        return None
    return msg

def cli_args_to_ipc_message(args) -> dict:
    """Map parsed CLI args to the command a second instance forwards to the
    primary. --launch (optionally with --profile) becomes a launch; anything
    else just surfaces the existing window."""
    if getattr(args, "launch", False):
        msg = {"action": "launch"}
        if getattr(args, "profile", None):
            msg["profile"] = args.profile
        return msg
    return {"action": "show"}

def acquire_single_instance():
    """Bind the IPC port. Returns the listening socket if we're the primary,
    else None (another instance — or a stranger — owns it). The bind failure
    IS the lock, so SO_REUSEADDR is deliberately not set."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((IPC_HOST, IPC_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None

def forward_to_primary(message: dict) -> bool:
    """Send a command to the running primary. Returns True only after a
    verified handshake, False if the port owner isn't us (foreign program)."""
    try:
        with socket.create_connection((IPC_HOST, IPC_PORT), timeout=2) as c:
            c.settimeout(2)
            banner_line = c.recv(_IPC_MAX_BYTES).decode("utf-8", "replace").split("\n", 1)[0]
            try:
                banner = json.loads(banner_line)
            except Exception:
                return False
            if not isinstance(banner, dict) or banner.get("app") != IPC_BANNER_APP:
                return False
            c.sendall((json.dumps(message) + "\n").encode())
            return True
    except Exception:
        return False

# --- START WITH WINDOWS (HKCU Run key) ---
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE_NAME = "SimLaunch"

def _pythonw_path() -> str:
    target = sys.executable.replace("python.exe", "pythonw.exe")
    return target if os.path.exists(target) else sys.executable

def build_run_command(pythonw: str, script: str) -> str:
    """HKCU Run value: launch minimized to tray with pythonw (no console)."""
    return f'"{pythonw}" "{script}" --minimized'

def is_run_at_startup() -> bool:
    """Registry is the source of truth (INV-6), not the settings file."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE_NAME)
            return True
    except OSError:
        return False

def set_run_at_startup(enable: bool):
    if enable:
        cmd = build_run_command(_pythonw_path(), os.path.abspath(__file__))
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, RUN_VALUE_NAME, 0, winreg.REG_SZ, cmd)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, RUN_VALUE_NAME)
        except FileNotFoundError:
            pass

# CPU Priority Map
PRIORITY_MAP = {
    "Realtime": psutil.REALTIME_PRIORITY_CLASS,
    "High": psutil.HIGH_PRIORITY_CLASS,
    "Above Normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
    "Normal": psutil.NORMAL_PRIORITY_CLASS,
    "Below Normal": psutil.BELOW_NORMAL_PRIORITY_CLASS,
    "Idle": psutil.IDLE_PRIORITY_CLASS
}

# --- LOGGING ---
# RotatingFileHandler with delay=True: the file isn't opened until the first
# record is emitted, so merely importing this module creates no app.log
# (keeps imports side-effect-free for tests/CI). Rotates at 512 KB, keeps 2.
_log_handler = RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=2, delay=True)
_log_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(message)s'))
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_root_logger.addHandler(_log_handler)

def log_error(context, e):
    print(f"Error in {context}: {e}")
    logging.error(f"Error in {context}: {str(e)}\n{traceback.format_exc()}")

# --- ICON UTILITIES ---
try:
    import win32ui
    import win32gui
    import win32con
    import win32api
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False

# Extracted exe icons are cached for the session so refresh_list_ui (which runs
# on every drag/edit) doesn't re-hit the Win32 icon APIs each time. Keyed by
# (normalized path or "" for missing/no-path, size); icons don't change while an
# exe exists, so no invalidation is needed.
_icon_cache = {}

def get_icon_from_exe(path, size=32):
    key = (os.path.normpath(path).lower() if path and os.path.exists(path) else "", size)
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    img = _extract_icon_image(path, size)
    _icon_cache[key] = img
    return img

def _extract_icon_image(path, size=32):
    if not HAS_WIN32 or not path or not os.path.exists(path):
        return get_placeholder_icon(size)
    try:
        large, small = win32gui.ExtractIconEx(path, 0)
        hicon = small[0] if small else (large[0] if large else None)
        if not hicon:
            return get_placeholder_icon(size)

        hdc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
        hbmp = win32ui.CreateBitmap()
        hbmp.CreateCompatibleBitmap(hdc, size, size)
        hdc = hdc.CreateCompatibleDC()
        hdc.SelectObject(hbmp)
        win32gui.DrawIconEx(hdc.GetHandleOutput(), 0, 0, hicon, size, size, 0, None, 0x0003)

        bmpinfo = hbmp.GetInfo()
        bmpstr = hbmp.GetBitmapBits(True)
        img = Image.frombuffer('RGB', (bmpinfo['bmWidth'], bmpinfo['bmHeight']), bmpstr, 'raw', 'BGRX', 0, 1)
        win32gui.DestroyIcon(hicon)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:
        return get_placeholder_icon(size)

def get_placeholder_icon(size=32, color=(70, 130, 180)):
    img = Image.new('RGB', (size, size), color=color)
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))

def create_tray_icon_image():
    try:
        if os.path.exists(ICON_FILE):
            return Image.open(ICON_FILE)
    except Exception:
        pass
    width = 64; height = 64
    image = Image.new('RGB', (width, height), (30, 30, 30))
    dc = ImageDraw.Draw(image)
    dc.polygon([(20, 16), (20, 48), (48, 32)], fill=(46, 204, 113))
    return image

# --- PROCESS UTILITIES ---

def is_app_running(exe_path: str) -> Tuple[bool, Optional[psutil.Process]]:
    """Return (running, psutil.Process) by matching normalized exe path."""
    if not exe_path:
        return False, None
    norm_target = os.path.normpath(exe_path).lower()
    try:
        for proc in psutil.process_iter(['exe', 'name', 'pid']):
            try:
                p_path = proc.info.get('exe')
                if p_path and os.path.normpath(p_path).lower() == norm_target:
                    return True, proc
            except (psutil.NoSuchProcess, psutil.AccessDenied, FileNotFoundError):
                continue
    except Exception as e:
        log_error("is_app_running", e)
    return False, None

def kill_app(exe_path):
    running, proc = is_app_running(exe_path)
    if running and proc:
        try:
            proc.terminate()
            return True
        except Exception as e:
            log_error("kill_app", e)
    return False

def launch_executable(app_data: dict) -> Optional[subprocess.Popen]:
    """Handles launching logic including Admin requests."""
    path = app_data.get('path')
    if not path:
        raise FileNotFoundError("No path provided")
    cwd = os.path.dirname(path) or None
    run_as_admin = app_data.get('admin', False)

    if run_as_admin:
        # ShellExecute triggers UAC prompt; no PID returned
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", path, "", cwd or '.', 1)
            if isinstance(ret, int) and ret <= 32:
                raise Exception(f"ShellExecute failed with code {ret}")
            return None
        except Exception as e:
            log_error("launch_executable.admin", e)
            raise
    else:
        # Standard Launch
        try:
            return subprocess.Popen([path], cwd=cwd)
        except Exception as e:
            log_error("launch_executable", e)
            raise

def get_process_memory_usage(proc: psutil.Process) -> int:
    """Get memory in MB."""
    try:
        return proc.memory_info().rss // (1024 * 1024)
    except Exception:
        return 0

def get_process_cpu_usage(proc: psutil.Process) -> float:
    """Get CPU percentage. Non-blocking: measures since the previous call
    on the same Process object (first call returns 0.0)."""
    try:
        return proc.cpu_percent(interval=None)
    except Exception:
        return 0.0

def kill_app_gracefully(exe_path: str, timeout: int = 5) -> bool:
    """Kill app with terminate first, then kill."""
    running, proc = is_app_running(exe_path)
    if not running or not proc:
        return False

    try:
        proc.terminate()
        try:
            proc.wait(timeout=timeout)
            return True
        except psutil.TimeoutExpired:
            proc.kill()
            proc.wait()
            return True
    except Exception as e:
        log_error("kill_app_gracefully", e)
        return False

# --- ECOQOS (Windows 11 Power Throttling) ---
PROCESS_POWER_THROTTLING_CURRENT_VERSION = 1
PROCESS_POWER_THROTTLING_EXECUTION_SPEED = 0x1
PROCESS_SET_INFORMATION = 0x0200
ProcessPowerThrottling = 4  # PROCESS_INFORMATION_CLASS value

class PROCESS_POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.wintypes.ULONG),
        ("ControlMask", ctypes.wintypes.ULONG),
        ("StateMask", ctypes.wintypes.ULONG),
    ]

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
# Explicit prototypes: without them ctypes passes HANDLE as a 32-bit int,
# which truncates the 64-bit pseudo-handle and fails with ERROR_INVALID_HANDLE
_kernel32.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.SetProcessInformation.restype = ctypes.wintypes.BOOL
_kernel32.SetProcessInformation.argtypes = [ctypes.wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]

def set_eco_qos(pid: Optional[int] = None, enable: bool = True) -> bool:
    """Apply EcoQoS (Task Manager's "Efficiency mode") to a process: the
    scheduler prefers E-cores at low clocks. pid=None targets this process.
    Returns False on older Windows or insufficient access."""
    try:
        kernel32 = _kernel32
        close_handle = False
        if pid is None:
            handle = kernel32.GetCurrentProcess()
        else:
            handle = kernel32.OpenProcess(PROCESS_SET_INFORMATION, False, int(pid))
            if not handle:
                return False
            close_handle = True
        try:
            state = PROCESS_POWER_THROTTLING_STATE(
                Version=PROCESS_POWER_THROTTLING_CURRENT_VERSION,
                ControlMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED,
                StateMask=PROCESS_POWER_THROTTLING_EXECUTION_SPEED if enable else 0,
            )
            ok = kernel32.SetProcessInformation(handle, ProcessPowerThrottling,
                                                ctypes.byref(state), ctypes.sizeof(state))
            return bool(ok)
        finally:
            if close_handle:
                kernel32.CloseHandle(handle)
    except Exception as e:
        log_error("set_eco_qos", e)
        return False

def apply_performance_settings(proc: psutil.Process, app_data: dict) -> bool:
    """Apply priority and affinity to a running process."""
    if not proc:
        return False
    success = True
    try:
        p_name = app_data.get('priority', 'Normal')
        if p_name in PRIORITY_MAP:
            try:
                proc.nice(PRIORITY_MAP[p_name])
            except PermissionError:
                log_error("Priority", "Access denied - may need admin")
                success = False
        affinity = app_data.get('affinity', []) or []
        if affinity:
            max_cores = psutil.cpu_count()
            valid = [c for c in affinity if 0 <= c < max_cores]
            if valid:
                try:
                    proc.cpu_affinity(valid)
                except PermissionError:
                    log_error("Affinity", "Access denied")
                    success = False
        if app_data.get('eco_mode'):
            if not set_eco_qos(proc.pid):
                log_error("EcoQoS", f"Could not enable efficiency mode for {app_data.get('name')}")
                success = False
    except Exception as e:
        log_error(f"Apply Settings {app_data.get('name')}", e)
        return False
    return success

def collect_race_mode_kill_targets(profiles: dict, active_profile: str, sim_exes: list) -> list:
    """Paths to close when entering race mode: apps in every profile OTHER than
    the active one, minus any path also used by the active profile, minus any
    path whose exe basename matches a configured sim exe (never kill the sim).
    Deduped, order-stable, returns original paths."""
    sim_lower = {(s or "").lower() for s in (sim_exes or [])}
    active_paths = set()
    for app in profiles.get(active_profile, []):
        p = app.get('path')
        if p:
            active_paths.add(os.path.normpath(p).lower())
    targets = []
    seen = set()
    for name, apps in profiles.items():
        if name == active_profile:
            continue
        for app in apps:
            p = app.get('path')
            if not p:
                continue
            norm = os.path.normpath(p).lower()
            if norm in active_paths or norm in seen:
                continue
            if os.path.basename(norm) in sim_lower:
                continue
            seen.add(norm)
            targets.append(p)
    return targets

def compute_perf_transitions(old: dict, new: dict) -> list:
    """Extra actions needed when re-applying settings to an already-running
    process, beyond apply_performance_settings (which only *enables* eco and
    skips empty affinity). Returns action tags: 'eco_disable', 'affinity_reset'."""
    actions = []
    if old.get('eco_mode') and not new.get('eco_mode'):
        actions.append('eco_disable')
    old_aff = old.get('affinity') or []
    new_aff = new.get('affinity') or []
    if old_aff and not new_aff:
        actions.append('affinity_reset')
    return actions

def apply_settings_after_admin_launch(parent, exe_path: str, app_data: dict, delay_ms: int = 2000, max_retries: int = 10):
    """Retry applying settings after admin-launched app starts (ShellExecute)."""
    def retry(attempt=0):
        running, proc = is_app_running(exe_path)
        if proc:
            apply_performance_settings(proc, app_data)
            return
        try:
            if attempt < max_retries and parent.winfo_exists():
                parent.after(delay_ms, lambda: retry(attempt + 1))
        except tk.TclError:
            pass
    parent.after(delay_ms, retry)

# --- PROCESS TRACKER ---
class ProcessTracker:
    """Cache running exe paths -> process for fast lookups."""
    def __init__(self, refresh_interval=1.0):
        self.cache = {}  # norm_path -> (True, proc)
        self.refresh_interval = refresh_interval
        self.last_update = 0.0
        self.lock = threading.Lock()

    def get_app_status(self, exe_path: str) -> Tuple[bool, Optional[psutil.Process]]:
        now = time.time()
        if now - self.last_update > self.refresh_interval:
            self._refresh_cache()
            self.last_update = now
        norm = os.path.normpath(exe_path).lower() if exe_path else ""
        with self.lock:
            entry = self.cache.get(norm)
            return entry if entry else (False, None)

    def _refresh_cache(self):
        with self.lock:
            old_cache = self.cache
            new_cache = {}
            try:
                for proc in psutil.process_iter(['exe', 'pid']):
                    try:
                        exe = proc.info.get('exe')
                        if exe:
                            norm = os.path.normpath(exe).lower()
                            # store only first match; psutil.Process object is live
                            if norm not in new_cache:
                                prev = old_cache.get(norm)
                                # Reuse the old Process object when the PID is unchanged
                                # so cpu_percent(interval=None) keeps its baseline.
                                if prev and prev[1].pid == proc.pid:
                                    new_cache[norm] = prev
                                else:
                                    new_cache[norm] = (True, proc)
                    except (psutil.NoSuchProcess, psutil.AccessDenied, FileNotFoundError):
                        continue
            except Exception as e:
                log_error("ProcessTracker._refresh_cache", e)
            self.cache = new_cache

# --- EDIT DIALOG ---
class AppSettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, app_data, callback):
        super().__init__(parent)
        self.title("Edit App Settings")
        self.geometry("550x750")
        self.app_data = app_data
        self.callback = callback
        self.transient(parent)
        self.grab_set()
        self.resizable(False, False)

        main_scroll = ctk.CTkScrollableFrame(self)
        main_scroll.pack(fill="both", expand=True, padx=20, pady=20)

        self._section("General Info", main_scroll)
        self.entry_name = self._add_field(main_scroll, "Name:", app_data.get('name', ''))

        path_frame = ctk.CTkFrame(main_scroll, fg_color="transparent")
        path_frame.pack(fill="x", pady=5)
        ctk.CTkLabel(path_frame, text="Path:").pack(anchor="w")
        path_input_frame = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_input_frame.pack(fill="x")
        self.entry_path = ctk.CTkEntry(path_input_frame)
        self.entry_path.insert(0, app_data.get('path', ''))
        self.entry_path.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(path_input_frame, text="Browse", width=80, command=self._browse_path).pack(side="right")

        self.entry_delay = self._add_field(main_scroll, "Delay (seconds):", str(app_data.get('delay', 0)))
        self.var_admin = ctk.BooleanVar(value=app_data.get('admin', False))
        self.var_enabled = ctk.BooleanVar(value=app_data.get('enabled', True))
        ctk.CTkCheckBox(main_scroll, text="Run as Administrator (UAC Prompt)", variable=self.var_admin).pack(anchor="w", pady=5)
        ctk.CTkCheckBox(main_scroll, text="Enabled", variable=self.var_enabled).pack(anchor="w", pady=5)

        self._section("Performance Tuning", main_scroll)
        ctk.CTkLabel(main_scroll, text="CPU Priority:").pack(anchor="w", pady=(10, 5))
        self.combo_priority = ctk.CTkOptionMenu(main_scroll, values=list(PRIORITY_MAP.keys()))
        self.combo_priority.set(app_data.get('priority', 'Normal'))
        self.combo_priority.pack(fill="x", pady=5)

        self.var_eco = ctk.BooleanVar(value=app_data.get('eco_mode', False))
        ctk.CTkCheckBox(main_scroll, text="Efficiency Mode (EcoQoS) — run on E-cores at low power",
                        variable=self.var_eco).pack(anchor="w", pady=5)
        ctk.CTkLabel(main_scroll, text="Good for background helpers; don't use on latency-sensitive apps.",
                     font=("Roboto", 10), text_color="gray").pack(anchor="w")

        ctk.CTkLabel(main_scroll, text="CPU Affinity:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        affinity_btn_frame = ctk.CTkFrame(main_scroll, fg_color="transparent")
        affinity_btn_frame.pack(fill="x", pady=5)

        ctk.CTkButton(affinity_btn_frame, text="All", width=50, height=25, 
                     command=lambda: [c.select() for c in self.core_checks]).pack(side="left", padx=2)
        ctk.CTkButton(affinity_btn_frame, text="None", width=50, height=25,
                     command=lambda: [c.deselect() for c in self.core_checks]).pack(side="left", padx=2)
        ctk.CTkButton(affinity_btn_frame, text="P-Cores Only", width=100, height=25,
                     command=self._select_p_cores).pack(side="left", padx=2)
        ctk.CTkButton(affinity_btn_frame, text="E-Cores Only", width=100, height=25,
                     command=self._select_e_cores).pack(side="left", padx=2)

        # create core checkboxes after determining core count
        self.affinity_frame = ctk.CTkScrollableFrame(main_scroll, height=150)
        self.affinity_frame.pack(fill="x", pady=5)
        self.core_checks = []
        saved_aff = app_data.get('affinity', []) or []
        p_core_count = psutil.cpu_count(logical=False) or 0
        total_cores = psutil.cpu_count() or 1

        for i in range(total_cores):
            core_type = "P-Core" if i < p_core_count else "E-Core"
            chk = ctk.CTkCheckBox(self.affinity_frame, text=f"CPU {i} ({core_type})")
            chk.pack(anchor="w")
            if not saved_aff or i in saved_aff:
                chk.select()
            else:
                chk.deselect()
            self.core_checks.append(chk)

        # --- BUTTONS ---
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(pady=15, fill="x", padx=20)
        ctk.CTkButton(btn_row, text="Save", command=self.save, fg_color="#27AE60", width=150).pack(side="left", padx=5)
        ctk.CTkButton(btn_row, text="Cancel", command=self.destroy, fg_color="#C0392B", width=150).pack(side="right", padx=5)

    def _section(self, title: str, parent):
        """Add a section header."""
        ctk.CTkLabel(parent, text=title, font=("Roboto", 14, "bold")).pack(anchor="w", pady=(15, 5))

    def _add_field(self, parent, label: str, value: str):
        """Add labeled entry field."""
        ctk.CTkLabel(parent, text=label).pack(anchor="w", pady=(5, 0))
        entry = ctk.CTkEntry(parent)
        entry.insert(0, value)
        entry.pack(fill="x", pady=5)
        return entry

    def _browse_path(self):
        """Browse for executable."""
        path = filedialog.askopenfilename(filetypes=[("Executables", "*.exe"), ("All", "*.*")])
        if path:
            self.entry_path.delete(0, "end")
            self.entry_path.insert(0, path)

    def _select_p_cores(self):
        """Select only P-cores."""
        p_core_count = psutil.cpu_count(logical=False)
        for i, c in enumerate(self.core_checks):
            if i < p_core_count:
                c.select()
            else:
                c.deselect()

    def _select_e_cores(self):
        """Select only E-cores."""
        p_core_count = psutil.cpu_count(logical=False)
        for i, c in enumerate(self.core_checks):
            if i >= p_core_count:
                c.select()
            else:
                c.deselect()

    def save(self):
        """Validate and save."""
        try:
            delay = float(self.entry_delay.get())
            if delay < 0:
                raise ValueError("Delay cannot be negative")
        except ValueError as e:
            messagebox.showerror("Error", f"Invalid delay: {str(e)}")
            return
        if not os.path.exists(self.entry_path.get()):
            messagebox.showerror("Error", "Executable path does not exist")
            return
        sel_cores = [i for i, c in enumerate(self.core_checks) if c.get()]
        if len(sel_cores) == len(self.core_checks):
            sel_cores = []
        # Start from the existing data so fields without UI (e.g. auto_restart,
        # last_run) survive an edit instead of being silently dropped.
        new_data = dict(self.app_data)
        new_data.update({
            "name": self.entry_name.get().strip() or "Unknown",
            "path": self.entry_path.get(),
            "delay": int(delay),
            "priority": self.combo_priority.get(),
            "affinity": sel_cores,
            "admin": self.var_admin.get(),
            "enabled": self.var_enabled.get(),
            "eco_mode": self.var_eco.get()
        })
        self.callback(new_data)
        self.destroy()

# --- UI ROWS ---
class DraggableRow(ctk.CTkFrame):
    def __init__(self, parent, app_data, actions, index, **kwargs):
        super().__init__(parent, corner_radius=8, fg_color=("gray90", "#2B2B2B"), **kwargs)
        self.app_data = app_data
        self.index = index
        self.actions = actions
        self.last_state = None
        self.last_pid = None
        self.grid_columnconfigure(2, weight=1)
        self.lbl_status = ctk.CTkLabel(self, text="○", font=("Arial", 16), text_color="gray")
        self.lbl_status.grid(row=0, column=0, padx=(10, 5), pady=10)
        self.icon_image = get_icon_from_exe(app_data.get('path'))
        self.lbl_icon = ctk.CTkLabel(self, text="", image=self.icon_image)
        self.lbl_icon.grid(row=0, column=1, padx=5, pady=10)
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.grid(row=0, column=2, sticky="ew", padx=5)
        name_txt = app_data.get('name', 'Unknown')
        if app_data.get('admin'):
            name_txt += " 🔐"
        if not app_data.get('enabled', True):
            name_txt += " (Disabled)"
        self.lbl_name = ctk.CTkLabel(self.info_frame, text=name_txt, font=("Roboto Medium", 14), anchor="w")
        self.lbl_name.pack(fill="x")
        meta = f"Wait: {app_data.get('delay')}s | {app_data.get('priority')}"
        if app_data.get('eco_mode'):
            meta += " | 🍃 Eco"
        self.lbl_meta = ctk.CTkLabel(self.info_frame, text=meta, font=("Roboto", 10), text_color="gray", anchor="w")
        self.lbl_meta.pack(fill="x")
        self.lbl_stats = ctk.CTkLabel(self.info_frame, text="", font=("Roboto", 9), text_color="#888888", anchor="w")
        self.lbl_stats.pack(fill="x")
        self.ctrl_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.ctrl_frame.grid(row=0, column=3, padx=5)
        self.btn_edit = ctk.CTkButton(self.ctrl_frame, text="⚙", width=30, height=30, fg_color="#34495E", command=lambda: self.actions['edit'](self))
        self.btn_edit.pack(side="left", padx=2)
        self.btn_play = ctk.CTkButton(self.ctrl_frame, text="▶", width=30, height=30, fg_color="#27AE60", command=lambda: self.actions['launch_one'](self.app_data))
        self.btn_play.pack(side="left", padx=2)
        self.btn_stop = ctk.CTkButton(self.ctrl_frame, text="■", width=30, height=30, fg_color="#E74C3C", command=lambda: self.actions['kill_one'](self.app_data.get('path')))
        self.btn_stop.pack(side="left", padx=2)
        self.lbl_drag = ctk.CTkLabel(self, text="≡", font=("Arial", 20), cursor="hand2")
        self.lbl_drag.grid(row=0, column=4, padx=5)
        self.btn_del = ctk.CTkButton(self, text="×", width=30, height=30, fg_color="transparent", hover_color="#C0392B", border_width=1, command=lambda: self.actions['delete'](self))
        self.btn_del.grid(row=0, column=5, padx=5)
        for w in [self, self.lbl_name, self.lbl_meta, self.lbl_drag, self.info_frame, self.lbl_status]:
            w.bind("<Button-1>", self.on_drag_start)
            w.bind("<ButtonRelease-1>", self.on_drag_end)
        self.update_visuals(is_running=False, force=True)

    def update_visuals(self, is_running: bool, proc: Optional[psutil.Process] = None, force: bool = False):
        if not force and is_running == self.last_state:
            if is_running and proc:
                self._update_stats(proc)
            return
        self.last_state = is_running
        try:
            if is_running:
                self.lbl_status.configure(text="●", text_color="#2ECC71")
                self.btn_stop.pack(side="left", padx=2)
                self.btn_play.pack_forget()
                self.btn_del.configure(state="disabled")
                self.btn_edit.configure(state="disabled")
                if proc:
                    self._update_stats(proc)
            else:
                self.lbl_status.configure(text="○", text_color="gray")
                self.lbl_stats.configure(text="")
                self.btn_stop.pack_forget()
                self.btn_play.pack(side="left", padx=2)
                self.btn_del.configure(state="normal")
                self.btn_edit.configure(state="normal")
        except Exception:
            pass

    def _update_stats(self, proc: psutil.Process):
        try:
            mem = get_process_memory_usage(proc)
            cpu = get_process_cpu_usage(proc)
            self.lbl_stats.configure(text=f"Mem: {mem}MB | CPU: {cpu:.1f}%")
        except Exception:
            pass

    def on_drag_start(self, event):
        self.actions['drag_start'](self, event)

    def on_drag_end(self, event):
        self.actions['drag_end'](self, event)

# --- CRASH DETECTION & STATISTICS ---
class CrashDetector:
    def __init__(self):
        self.crash_history = self._load_crash_history()
        self.watch_list = {}

    def _load_crash_history(self) -> dict:
        if os.path.exists(CRASH_LOG_FILE):
            try:
                with open(CRASH_LOG_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return defaultdict(list)
        return defaultdict(list)

    def _save_crash_history(self):
        try:
            with open(CRASH_LOG_FILE, 'w') as f:
                json.dump(dict(self.crash_history), f, indent=2)
        except Exception as e:
            log_error("save_crash_history", e)

    def register_app(self, exe_path: str, pid: int, name: str = None, popen: Optional[subprocess.Popen] = None,
                     auto_restart: bool = False, max_retries: int = 3):
        self.watch_list[exe_path] = {
            'pid': pid,
            'name': name or os.path.basename(exe_path),
            'popen': popen,
            'started': time.time(),
            'retries': 0,
            'auto_restart': auto_restart,
            'max_retries': max_retries
        }

    def check_crashes(self, callback_on_crash=None):
        crashed = []
        for exe_path, data in list(self.watch_list.items()):
            running, _ = is_app_running(exe_path)
            if not running:
                del self.watch_list[exe_path]
                runtime = time.time() - data['started']
                popen = data.get('popen')
                if popen is not None:
                    # Exit code 0 = user closed the app normally, not a crash
                    exit_code = popen.poll()
                    is_crash = exit_code is not None and exit_code != 0
                else:
                    # No exit code available; only treat very short runs as crashes
                    exit_code = None
                    is_crash = runtime < 30
                if not is_crash:
                    continue
                crash_record = {
                    'app': data['name'],
                    'timestamp': datetime.now().isoformat(),
                    'runtime_seconds': runtime,
                    'exit_code': exit_code,
                    'retry_count': data['retries']
                }
                self.crash_history.setdefault(exe_path, []).append(crash_record)
                crashed.append((exe_path, data, runtime))
        if crashed:
            self._save_crash_history()
            if callback_on_crash:
                callback_on_crash(crashed)
        return crashed

class AppStatistics:
    def __init__(self):
        self.stats = self._load_stats()

    def _load_stats(self) -> dict:
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, 'r') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_stats(self):
        try:
            with open(STATS_FILE, 'w') as f:
                json.dump(self.stats, f, indent=2)
        except Exception as e:
            log_error("save_stats", e)

    def record_launch(self, app_name: str):
        if app_name not in self.stats:
            self.stats[app_name] = {
                'total_launches': 0,
                'total_runtime_seconds': 0,
                'avg_runtime': 0,
                'first_launched': None,
                'last_launched': None,
                'crashes': 0
            }
        s = self.stats[app_name]
        s['total_launches'] += 1
        s['last_launched'] = datetime.now().isoformat()
        if not s['first_launched']:
            s['first_launched'] = s['last_launched']
        self._save_stats()

    def record_runtime(self, app_name: str, runtime_seconds: float):
        if app_name in self.stats:
            s = self.stats[app_name]
            s['total_runtime_seconds'] += runtime_seconds
            s['avg_runtime'] = s['total_runtime_seconds'] / s['total_launches'] if s['total_launches'] else 0
            self._save_stats()

    def record_crash(self, app_name: str):
        if app_name in self.stats:
            self.stats[app_name]['crashes'] += 1
            self._save_stats()

    def rename(self, old_name: str, new_name: str):
        """Move stats to a new key when an app is renamed (INV-5). No-op if the
        old key is absent or the new key already exists (don't merge histories)."""
        if old_name and new_name and old_name in self.stats and new_name not in self.stats:
            self.stats[new_name] = self.stats.pop(old_name)
            self._save_stats()

    def get_stats(self, app_name: str) -> dict:
        s = self.stats.get(app_name)
        if not s:
            return {}
        return {
            'launches': s['total_launches'],
            'avg_runtime': f"{s['avg_runtime']:.0f}s" if s.get('avg_runtime') else "N/A",
            'crashes': s.get('crashes', 0),
            'last_run': s['last_launched'][-8:] if s.get('last_launched') else "Never"
        }

# --- HOTKEY MANAGER ---
class HotkeyManager:
    def __init__(self):
        self.hotkeys = {}
    def register(self, hotkey_str: str, callback):
        try:
            import keyboard
            keyboard.add_hotkey(hotkey_str, callback)
            self.hotkeys[hotkey_str] = callback
            return True
        except Exception as e:
            log_error("HotkeyManager.register", e)
            return False
    def unregister_all(self):
        try:
            import keyboard
            for hk in list(self.hotkeys.keys()):
                keyboard.remove_hotkey(hk)
        except Exception:
            pass
        self.hotkeys.clear()

# --- SETTINGS DIALOG ---
class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, settings, callback):
        super().__init__(parent)
        self.title("Settings")
        self.geometry("600x500")
        self.transient(parent)
        self.grab_set()
        self.settings = settings
        self.callback = callback
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=20, pady=20)
        ctk.CTkLabel(scroll, text="Theme:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        theme_var = ctk.StringVar(value=settings.get('theme', 'Dark'))
        ctk.CTkOptionMenu(scroll, values=["Light", "Dark", "System"], variable=theme_var).pack(fill="x", pady=5)
        ctk.CTkLabel(scroll, text="Monitor Interval (ms):", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        entry_monitor = ctk.CTkEntry(scroll)
        entry_monitor.insert(0, str(settings.get('monitor_interval', 2000)))
        entry_monitor.pack(fill="x", pady=5)
        ctk.CTkLabel(scroll, text="Reliability:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        var_crash = ctk.BooleanVar(value=settings.get('crash_detection', True))
        ctk.CTkCheckBox(scroll, text="Enable crash detection & recovery", variable=var_crash).pack(anchor="w", pady=5)
        var_autosave = ctk.BooleanVar(value=settings.get('auto_save_interval', 5000) > 0)
        ctk.CTkCheckBox(scroll, text="Auto-save config every 5 seconds", variable=var_autosave).pack(anchor="w", pady=5)
        var_minimize = ctk.BooleanVar(value=settings.get('minimize_on_launch', False))
        ctk.CTkCheckBox(scroll, text="Minimize app when launching sequence", variable=var_minimize).pack(anchor="w", pady=5)
        ctk.CTkLabel(scroll, text="Performance:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        var_launcher_eco = ctk.BooleanVar(value=settings.get('launcher_eco_mode', True))
        ctk.CTkCheckBox(scroll, text="Run launcher in Efficiency Mode (EcoQoS) — leaves more CPU for the sim",
                        variable=var_launcher_eco).pack(anchor="w", pady=5)

        ctk.CTkLabel(scroll, text="Startup:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        # INV-6: initial state comes from the registry, never launcher_settings.json
        var_startup = ctk.BooleanVar(value=is_run_at_startup())
        ctk.CTkCheckBox(scroll, text="Start with Windows (minimized to tray)", variable=var_startup).pack(anchor="w", pady=5)

        ctk.CTkLabel(scroll, text="Hotkeys (global):", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        var_hotkeys = ctk.BooleanVar(value=settings.get('global_hotkeys', True))
        ctk.CTkCheckBox(scroll, text="Enable global hotkeys", variable=var_hotkeys).pack(anchor="w", pady=5)
        ks = settings.get('keyboard_shortcuts', {})
        hotkey_labels = {
            'launch_seq': 'Launch sequence',
            'kill_all': 'Kill all',
            'race_mode': 'Race mode',
            'toggle_window': 'Show / hide window',
        }
        hotkey_entries = {}
        for key, label in hotkey_labels.items():
            row = ctk.CTkFrame(scroll, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
            e = ctk.CTkEntry(row)
            e.insert(0, ks.get(key, ''))
            e.pack(side="left", fill="x", expand=True)
            hotkey_entries[key] = e

        ctk.CTkLabel(scroll, text="Race Mode:", font=("Roboto", 12, "bold")).pack(anchor="w", pady=(10, 5))
        var_rm_close = ctk.BooleanVar(value=settings.get('race_mode_close_other_profiles', True))
        ctk.CTkCheckBox(scroll, text="Close apps from other profiles", variable=var_rm_close).pack(anchor="w", pady=3)
        var_rm_present = ctk.BooleanVar(value=settings.get('race_mode_presentation_mode', True))
        ctk.CTkCheckBox(scroll, text="Suppress notifications (Presentation Mode)", variable=var_rm_present).pack(anchor="w", pady=3)
        var_rm_boost = ctk.BooleanVar(value=settings.get('race_mode_boost_sim', True))
        ctk.CTkCheckBox(scroll, text="Boost sim to High priority", variable=var_rm_boost).pack(anchor="w", pady=3)
        ctk.CTkLabel(scroll, text="Sim executables (comma-separated):", font=("Roboto", 10)).pack(anchor="w", pady=(5, 0))
        entry_rm_sim = ctk.CTkEntry(scroll)
        entry_rm_sim.insert(0, ", ".join(settings.get('race_mode_sim_exes', [])))
        entry_rm_sim.pack(fill="x", pady=3)
        ctk.CTkLabel(scroll, text="Also kill these exes (comma-separated):", font=("Roboto", 10)).pack(anchor="w", pady=(5, 0))
        entry_rm_kill = ctk.CTkEntry(scroll)
        entry_rm_kill.insert(0, ", ".join(settings.get('race_mode_custom_kill', [])))
        entry_rm_kill.pack(fill="x", pady=3)

        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=15, fill="x", padx=20)
        def save_settings():
            new_hotkeys = {k: e.get().strip() for k, e in hotkey_entries.items()}
            try:
                import keyboard
                for combo in new_hotkeys.values():
                    if combo:
                        keyboard.parse_hotkey(combo)  # raises on invalid combo
            except ImportError:
                pass  # can't validate without the module; accept as entered
            except Exception:
                messagebox.showerror("Error", "One or more hotkeys are invalid.\nUse a format like 'ctrl+alt+l'.")
                return
            settings['theme'] = theme_var.get()
            try:
                settings['monitor_interval'] = int(entry_monitor.get() or 2000)
            except ValueError:
                settings['monitor_interval'] = 2000
            settings['crash_detection'] = var_crash.get()
            settings['auto_save_interval'] = 5000 if var_autosave.get() else 0
            settings['minimize_on_launch'] = var_minimize.get()
            settings['launcher_eco_mode'] = var_launcher_eco.get()
            settings['global_hotkeys'] = var_hotkeys.get()
            settings['keyboard_shortcuts'] = new_hotkeys
            settings['race_mode_close_other_profiles'] = var_rm_close.get()
            settings['race_mode_presentation_mode'] = var_rm_present.get()
            settings['race_mode_boost_sim'] = var_rm_boost.get()
            settings['race_mode_sim_exes'] = [x.strip() for x in entry_rm_sim.get().split(',') if x.strip()]
            settings['race_mode_custom_kill'] = [x.strip() for x in entry_rm_kill.get().split(',') if x.strip()]
            # Startup entry lives in the registry, not in settings (INV-6)
            try:
                set_run_at_startup(var_startup.get())
            except Exception as e:
                log_error("set_run_at_startup", e)
                messagebox.showwarning("Warning", "Couldn't update the Windows startup setting.")
            self.callback(settings)
            self.destroy()
        ctk.CTkButton(btn_frame, text="Save", command=save_settings, fg_color="#27AE60", width=150).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Cancel", command=self.destroy, fg_color="#C0392B", width=150).pack(side="right", padx=5)

# --- STATS VIEW ---
def fmt_duration(seconds) -> str:
    """Humanize a duration: '5h 32m' / '47m' / '12s'."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "0s"
    if s < 0:
        s = 0
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{sec}s"

def fmt_iso_datetime(iso: str) -> str:
    if not iso:
        return "Never"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso

class StatsDialog(ctk.CTkToplevel):
    def __init__(self, parent, app_stats, on_reset):
        super().__init__(parent)
        self.title("Statistics")
        self.geometry("700x600")
        self.transient(parent)
        self.grab_set()
        self.on_reset = on_reset
        stats = app_stats.stats

        total_launches = sum(s.get('total_launches', 0) for s in stats.values())
        total_runtime = sum(s.get('total_runtime_seconds', 0) for s in stats.values())
        most = max(stats.items(), key=lambda kv: kv[1].get('total_launches', 0), default=(None, None))
        most_name = most[0] or "—"
        firsts = [s.get('first_launched') for s in stats.values() if s.get('first_launched')]
        since = fmt_iso_datetime(min(firsts)) if firsts else "—"

        summary = ctk.CTkFrame(self)
        summary.pack(fill="x", padx=20, pady=(20, 10))
        summary_text = (f"Total launches: {total_launches}     "
                        f"Total tracked time: {fmt_duration(total_runtime)}\n"
                        f"Most launched: {most_name}     Tracking since: {since}")
        ctk.CTkLabel(summary, text=summary_text, font=("Roboto", 12), anchor="w",
                     justify="left", wraplength=640).pack(fill="x", padx=12, pady=10)

        table = ctk.CTkScrollableFrame(self, label_text="Per-app")
        table.pack(fill="both", expand=True, padx=20, pady=10)
        headers = ["App", "Launches", "Total time", "Avg session", "Crashes", "Last launched"]
        for c, h in enumerate(headers):
            table.grid_columnconfigure(c, weight=1 if c == 0 else 0)
            ctk.CTkLabel(table, text=h, font=("Roboto", 11, "bold"), anchor="w").grid(row=0, column=c, sticky="w", padx=5, pady=4)
        rows = sorted(stats.items(), key=lambda kv: kv[1].get('total_launches', 0), reverse=True)
        for r, (name, s) in enumerate(rows, start=1):
            bg = ("gray85", "#2B2B2B") if r % 2 == 0 else "transparent"
            cells = [
                name,
                str(s.get('total_launches', 0)),
                fmt_duration(s.get('total_runtime_seconds', 0)),
                fmt_duration(s.get('avg_runtime', 0)),
                str(s.get('crashes', 0)),
                fmt_iso_datetime(s.get('last_launched')),
            ]
            for c, val in enumerate(cells):
                ctk.CTkLabel(table, text=val, font=("Roboto", 10), anchor="w",
                             fg_color=bg, corner_radius=0).grid(row=r, column=c, sticky="ew", padx=5, pady=1)
        if not rows:
            ctk.CTkLabel(table, text="No statistics yet.", text_color="gray").grid(row=1, column=0, columnspan=6, pady=20)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 15))
        ctk.CTkButton(footer, text="Reset Statistics", fg_color="#C0392B", hover_color="#E74C3C",
                      command=self._reset).pack(side="left")
        ctk.CTkButton(footer, text="Close", command=self.destroy).pack(side="right")

    def _reset(self):
        if messagebox.askyesno("Confirm", "Reset all statistics? This cannot be undone."):
            self.on_reset()
            self.destroy()

# --- ENHANCED ROW WITH STATS ---
class EnhancedDraggableRow(DraggableRow):
    def __init__(self, parent, app_data, actions, index, stats: AppStatistics = None, **kwargs):
        super().__init__(parent, app_data, actions, index, **kwargs)
        self.stats = stats
        self.start_time = None

    def update_visuals(self, is_running: bool, proc: Optional[psutil.Process] = None, force: bool = False):
        prev = self.last_state
        super().update_visuals(is_running, proc, force)
        try:
            if is_running and not self.start_time:
                self.start_time = time.time()
            if not is_running and self.start_time:
                runtime = time.time() - self.start_time
                if self.stats:
                    self.stats.record_runtime(self.app_data['name'], runtime)
                self.start_time = None
        except Exception:
            pass

# --- MAIN APP ---
class SimLauncherApp(ctk.CTk):
    def __init__(self, ipc_socket=None, cli_profile=None, auto_launch=False, start_minimized=False):
        super().__init__()
        self._closing = False
        self._seq_running = False
        self._ipc_socket = ipc_socket
        self.race_mode = False
        self._boosted_pids = []
        self._race_boost_pending = False
        self.report_callback_exception = self.show_error_popup
        self.title(f"{APP_NAME} v{VERSION}")
        try:
            # Give the app its own taskbar identity so Windows shows our icon
            # instead of grouping under the Python interpreter's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SimLaunch")
            if os.path.exists(ICON_FILE):
                self.iconbitmap(ICON_FILE)
        except Exception as e:
            log_error("set_app_icon", e)
        self.geometry("750x900")
        self.minsize(600, 700)
        self.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        self.data = {"current_profile": "Default", "profiles": {"Default": []}}
        self.settings = self._load_settings()
        ctk.set_appearance_mode(self.settings.get('theme', 'Dark'))
        if self.settings.get('launcher_eco_mode', True):
            set_eco_qos()
        self.process_tracker = ProcessTracker()
        self.crash_detector = CrashDetector()
        self.app_stats = AppStatistics()
        self.hotkey_manager = HotkeyManager()
        self.load_data()
        self.create_widgets()
        self.toast_lbl = ctk.CTkLabel(self, text="", height=30, corner_radius=10, fg_color="#333333", text_color="white")
        self.drag_data = {"item": None}
        self.ui_queue = queue.Queue()
        self._poll_ui_queue()
        self.refresh_list_ui()
        self.monitor_processes()
        self.setup_autosave()
        self.setup_hotkeys()
        if self._ipc_socket is not None:
            threading.Thread(target=self._ipc_accept_loop, daemon=True).start()
        # CLI-driven startup (from a shortcut like the iRacing one)
        if cli_profile:
            match = self._match_profile(cli_profile)
            if match:
                self.change_profile(match)
            else:
                self.notify(f"Profile '{cli_profile}' not found", "#E67E22")
        if start_minimized:
            self.after(200, self.minimize_to_tray)
        if auto_launch:
            self.after(800, self.launch_sequence)
        logging.info(f"App start v{VERSION} (profile={cli_profile}, launch={auto_launch}, minimized={start_minimized})")

    def _alive(self) -> bool:
        """True while the window exists and isn't shutting down. winfo_exists
        raises TclError once the app is destroyed, so it can't be called bare
        in after-callbacks."""
        if self._closing:
            return False
        try:
            return bool(self.winfo_exists())
        except tk.TclError:
            return False

    def ui_call(self, fn, *args, **kwargs):
        """Schedule a callable to run on the Tk main thread. Tkinter is not
        thread-safe, so worker/tray threads must route UI work through this."""
        self.ui_queue.put((fn, args, kwargs))

    def _poll_ui_queue(self):
        try:
            while True:
                fn, args, kwargs = self.ui_queue.get_nowait()
                try:
                    fn(*args, **kwargs)
                except Exception as e:
                    log_error("ui_call", e)
        except queue.Empty:
            pass
        if self._alive():
            self.after(100, self._poll_ui_queue)

    def _ipc_accept_loop(self):
        """Primary-instance listener. Blocking accept() → zero idle CPU.
        Exits when exit_app() closes the socket (accept raises OSError)."""
        while not self._closing:
            try:
                conn, _ = self._ipc_socket.accept()
            except OSError:
                break
            try:
                conn.settimeout(2)
                conn.sendall((json.dumps({"app": IPC_BANNER_APP, "version": VERSION}) + "\n").encode())
                line = conn.recv(_IPC_MAX_BYTES).decode("utf-8", "replace").split("\n", 1)[0]
                msg = parse_ipc_message(line)
                if msg and not self._closing:
                    self.ui_call(self._handle_ipc, msg)
            except Exception as e:
                log_error("ipc_accept", e)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    def _handle_ipc(self, msg: dict):
        """Dispatch a forwarded command on the main thread."""
        action = msg.get("action")
        if action == "show":
            self._do_restore()
        elif action == "launch":
            profile = msg.get("profile")
            if profile:
                match = self._match_profile(profile)
                if not match:
                    self.notify(f"Profile '{profile}' not found", "#E67E22")
                    return
                self.change_profile(match)
            self.launch_sequence()

    def _match_profile(self, name: str) -> Optional[str]:
        """Case-insensitive profile-name match; returns the actual key or None."""
        if not name:
            return None
        low = name.lower()
        for key in self.data["profiles"]:
            if key.lower() == low:
                return key
        return None

    def _load_settings(self) -> dict:
        settings = DEFAULT_SETTINGS.copy()
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    loaded = json.load(f)
                    settings = {**DEFAULT_SETTINGS, **loaded}
            except Exception:
                log_error("_load_settings", "Failed to read settings")
        return migrate_hotkey_settings(settings)

    def _save_settings(self):
        try:
            with open(SETTINGS_FILE, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            log_error("save_settings", e)

    def setup_autosave(self):
        # Cancel any previous loop so re-saving settings doesn't stack loops
        if getattr(self, '_autosave_after_id', None):
            try:
                self.after_cancel(self._autosave_after_id)
            except Exception:
                pass
            self._autosave_after_id = None
        interval = self.settings.get('auto_save_interval', 0)
        if interval > 0:
            def autosave():
                try:
                    self.save_data()
                finally:
                    if self._alive():
                        self._autosave_after_id = self.after(interval, autosave)
            self._autosave_after_id = self.after(interval, autosave)

    def setup_hotkeys(self):
        """(Re)register global hotkeys. keyboard invokes callbacks on its own
        thread, so every callback routes through ui_call (INV-8)."""
        if not self.settings.get('global_hotkeys', True):
            return
        try:
            import keyboard  # noqa: F401
        except ImportError:
            self.show_toast("Install 'keyboard' package for global hotkeys", "#E67E22")
            return
        keys = self.settings.get('keyboard_shortcuts', {})
        bindings = {
            'launch_seq': lambda: self.ui_call(self.launch_sequence),
            'kill_all': lambda: self.ui_call(self._hotkey_kill_all),
            'toggle_window': lambda: self.ui_call(self._hotkey_toggle_window),
        }
        # race_mode gains a handler in a later commit; register it once it exists
        if hasattr(self, 'toggle_race_mode'):
            bindings['race_mode'] = lambda: self.ui_call(self.toggle_race_mode)
        for action, cb in bindings.items():
            combo = keys.get(action)
            if combo:
                self.hotkey_manager.register(combo, cb)

    def _hotkey_kill_all(self):
        # INV-2: restore the window first so the confirm dialog is visible
        if getattr(self, 'is_minimized_to_tray', False):
            self._do_restore()
        self.kill_all()

    def _hotkey_toggle_window(self):
        if getattr(self, 'is_minimized_to_tray', False):
            self._do_restore()
        else:
            self.minimize_to_tray()

    def show_error_popup(self, exc, val, tb):
        log_error("Fatal", val)
        # During shutdown, stray callbacks hitting the destroyed window are
        # expected — log them but don't pop a dialog
        if self._closing or (isinstance(val, tk.TclError) and "destroyed" in str(val)):
            return
        messagebox.showerror("Error", f"An error occurred:\n{str(val)}\n\nCheck {LOG_FILE} for details.")

    def show_toast(self, message: str, color: str = "#333333", duration: int = 3000):
        try:
            self.toast_lbl.configure(text=message, fg_color=color)
            self.toast_lbl.place(relx=0.5, rely=0.95, anchor="center")
            self.toast_lbl.lift()
            self.after(duration, lambda: (self.toast_lbl.place_forget() if self._alive() else None))
        except Exception:
            pass

    def notify(self, message: str, color: str = "#333333", duration: int = 3000):
        """In-window toast when visible; native tray notification when minimized.
        Use for events that matter while trayed (sequence done, crashes, race
        mode). Callers may race the tray-thread startup — falls back to toast."""
        if getattr(self, 'is_minimized_to_tray', False) and getattr(self, 'tray_icon', None):
            try:
                self.tray_icon.notify(message, APP_NAME)
                return
            except Exception as e:
                log_error("tray_notify", e)
        self.show_toast(message, color, duration)

    def create_widgets(self):
        self.header = ctk.CTkFrame(self, height=90, fg_color="#1a1a1a", corner_radius=0)
        self.header.pack(fill="x")
        self.header.pack_propagate(False)
        left_header = ctk.CTkFrame(self.header, fg_color="transparent")
        left_header.pack(side="left", padx=20, pady=10)
        ctk.CTkLabel(left_header, text=APP_NAME, font=("Montserrat", 26, "bold")).pack()
        ctk.CTkLabel(left_header, text=f"v{VERSION}", font=("Roboto", 10), text_color="gray").pack(anchor="w")
        self.profile_frame = ctk.CTkFrame(self.header, fg_color="transparent")
        self.profile_frame.pack(side="right", padx=20, pady=15)
        ctk.CTkLabel(self.profile_frame, text="Profile:", font=("Roboto", 11)).pack(side="left", padx=5)
        self.profile_var = ctk.StringVar(value=self.data["current_profile"])
        self.combo_profiles = ctk.CTkOptionMenu(self.profile_frame, values=self.get_profile_names(), variable=self.profile_var, command=self.change_profile, width=160)
        self.combo_profiles.pack(side="left", padx=5)
        self.btn_profile_menu = ctk.CTkButton(self.profile_frame, text="⋮", width=35, fg_color="#34495E", command=self._show_profile_menu)
        self.btn_profile_menu.pack(side="left", padx=2)
        ctk.CTkButton(self.profile_frame, text="⚙", width=35, fg_color="#34495E", command=self.open_settings).pack(side="left", padx=2)
        ctk.CTkButton(self.profile_frame, text="⏻", width=35, fg_color="transparent", hover_color="#C0392B", border_width=1, command=self.exit_app).pack(side="left", padx=(8, 2))
        self.actions = ctk.CTkFrame(self, fg_color="transparent")
        self.actions.pack(fill="x", padx=20, pady=12)
        ctk.CTkButton(self.actions, text="➕ Add App", command=self.add_app, fg_color="#34495E", width=120).pack(side="left", padx=5)
        self.btn_launch_seq = ctk.CTkButton(self.actions, text="🚀 LAUNCH SEQUENCE", fg_color="#27AE60", hover_color="#2ECC71", command=self.launch_sequence, font=("Roboto", 14, "bold"))
        self.btn_launch_seq.pack(side="left", padx=20, fill="x", expand=True)
        ctk.CTkButton(self.actions, text="☠ KILL ALL", fg_color="#C0392B", hover_color="#E74C3C", command=self.kill_all, width=100).pack(side="right", padx=5)
        self.btn_race_mode = ctk.CTkButton(self.actions, text="🏁 RACE MODE", fg_color="transparent", border_width=1,
                                           hover_color="#C0392B", width=130, command=self.toggle_race_mode)
        self.btn_race_mode.pack(side="right", padx=5)
        ctk.CTkButton(self.actions, text="📊", width=40, fg_color="#34495E", command=self.open_stats).pack(side="right", padx=5)
        self.scroll = ctk.CTkScrollableFrame(self, label_text="Apps")
        self.scroll.pack(fill="both", expand=True, padx=20, pady=10)

    def open_stats(self):
        def do_reset():
            self.app_stats.stats.clear()
            self.app_stats._save_stats()
            self.show_toast("Statistics reset", "#C0392B")
        StatsDialog(self, self.app_stats, do_reset)

    def open_settings(self):
        def save_settings(new_settings):
            self.settings = new_settings
            self._save_settings()
            self.setup_autosave()
            ctk.set_appearance_mode(self.settings.get('theme', 'Dark'))
            set_eco_qos(enable=self.settings.get('launcher_eco_mode', True))
            self.hotkey_manager.unregister_all()
            self.setup_hotkeys()
            self.show_toast("Settings saved", "#27AE60")
        SettingsDialog(self, self.settings, save_settings)

    def load_data(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
            except Exception as e:
                log_error("load_data", e)
                # Preserve the broken file under a separate name — never touch
                # the .bak, which holds the last known-good config.
                corrupt_copy = f"{CONFIG_FILE}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
                try:
                    shutil.copy(CONFIG_FILE, corrupt_copy)
                except Exception:
                    pass
                restored = False
                backup = f"{CONFIG_FILE}.bak"
                if os.path.exists(backup):
                    try:
                        with open(backup, 'r') as f:
                            self.data.update(json.load(f))
                        restored = True
                    except Exception as e2:
                        log_error("load_data.backup", e2)
                if restored:
                    messagebox.showwarning("Warning", f"Config corrupted — restored from backup.\nBroken file saved as {os.path.basename(corrupt_copy)}.")
                else:
                    messagebox.showwarning("Warning", f"Config corrupted and no usable backup found.\nBroken file saved as {os.path.basename(corrupt_copy)}.")
        if self.data["current_profile"] not in self.data["profiles"]:
            self.data["current_profile"] = list(self.data["profiles"].keys())[0]

    def save_data(self):
        try:
            if os.path.exists(CONFIG_FILE):
                try:
                    shutil.copy(CONFIG_FILE, f"{CONFIG_FILE}.bak")
                except Exception:
                    pass
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            log_error("save_data", e)
            self.show_toast("Save Failed", "#C0392B")

    def get_current_apps(self) -> list:
        return self.data["profiles"][self.data["current_profile"]]

    def get_profile_names(self) -> list:
        return list(self.data["profiles"].keys())

    def change_profile(self, new_p: str):
        self.data["current_profile"] = new_p
        # Keep the combo in sync when switched programmatically (IPC/CLI)
        if hasattr(self, 'profile_var'):
            self.profile_var.set(new_p)
        self.save_data()
        self.refresh_list_ui()

    def add_profile(self):
        name = simpledialog.askstring("New Profile", "Profile Name:", parent=self)
        if name and name.strip():
            name = name.strip()
            if name in self.data["profiles"]:
                messagebox.showwarning("Warning", "Profile already exists")
                return
            self.data["profiles"][name] = []
            self.data["current_profile"] = name
            self.combo_profiles.configure(values=self.get_profile_names())
            self.profile_var.set(name)
            self.save_data()
            self.refresh_list_ui()
            self.show_toast(f"Profile '{name}' created", "#27AE60")

    def delete_profile(self):
        if len(self.data["profiles"]) <= 1:
            messagebox.showwarning("Warning", "Can't delete the last profile")
            return
        current = self.data["current_profile"]
        if messagebox.askyesno("Confirm", f"Delete '{current}'?"):
            del self.data["profiles"][current]
            self.data["current_profile"] = self.get_profile_names()[0]
            self.combo_profiles.configure(values=self.get_profile_names())
            self.profile_var.set(self.data["current_profile"])
            self.save_data()
            self.refresh_list_ui()
            self.show_toast("Profile deleted", "#C0392B")

    def _show_profile_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="New Profile…", command=self.add_profile)
        menu.add_command(label="Rename Profile…", command=self.rename_profile)
        menu.add_command(label="Duplicate Profile", command=self.duplicate_profile)
        menu.add_command(label="Delete Profile", command=self.delete_profile)
        menu.add_separator()
        menu.add_command(label="Export Profile…", command=self.export_profile)
        menu.add_command(label="Import Profile…", command=self.import_profile)
        try:
            x = self.btn_profile_menu.winfo_rootx()
            y = self.btn_profile_menu.winfo_rooty() + self.btn_profile_menu.winfo_height()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def rename_profile(self):
        old = self.data["current_profile"]
        name = simpledialog.askstring("Rename Profile", "New name:", initialvalue=old, parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name == old:
            return
        if any(k.lower() == name.lower() for k in self.data["profiles"]):
            messagebox.showwarning("Warning", "A profile with that name already exists")
            return
        # Rebuild preserving key order, renamed key in place
        self.data["profiles"] = {(name if k == old else k): v for k, v in self.data["profiles"].items()}
        self.data["current_profile"] = name
        self.combo_profiles.configure(values=self.get_profile_names())
        self.profile_var.set(name)
        self.save_data()
        self.refresh_list_ui()
        # INV-4: the desktop shortcut freezes the old profile name
        self.show_toast(f"Renamed to '{name}'. If it had a shortcut, re-run install_shortcut.py --profile", "#27AE60", duration=6000)

    def duplicate_profile(self):
        src = self.data["current_profile"]
        base = f"{src} (copy)"
        name, i = base, 2
        while name in self.data["profiles"]:
            name = f"{base} ({i})"
            i += 1
        self.data["profiles"][name] = copy.deepcopy(self.data["profiles"][src])
        self.data["current_profile"] = name
        self.combo_profiles.configure(values=self.get_profile_names())
        self.profile_var.set(name)
        self.save_data()
        self.refresh_list_ui()
        self.show_toast(f"Duplicated to '{name}'", "#27AE60")

    def export_profile(self):
        name = self.data["current_profile"]
        path = filedialog.asksaveasfilename(defaultextension=".json", initialfile=f"{name}.launchprofile.json",
                                            filetypes=[("Launch Profile", "*.json")], parent=self)
        if not path:
            return
        try:
            payload = {"launch_profile_version": 1, "name": name, "apps": self.get_current_apps()}
            with open(path, 'w') as f:
                json.dump(payload, f, indent=2)
            self.show_toast(f"Exported '{name}'", "#27AE60")
        except Exception as e:
            log_error("export_profile", e)
            messagebox.showerror("Error", f"Export failed: {e}")

    def import_profile(self):
        path = filedialog.askopenfilename(filetypes=[("Launch Profile", "*.json"), ("All", "*.*")], parent=self)
        if not path:
            return
        try:
            with open(path, 'r') as f:
                payload = json.load(f)
            apps, missing = parse_profile_import(payload)
        except Exception as e:
            log_error("import_profile", e)
            messagebox.showerror("Error", f"Import failed: {e}")
            return
        base = payload.get("name") or os.path.splitext(os.path.basename(path))[0]
        name, i = base, 2
        while name in self.data["profiles"]:
            name = f"{base} ({i})"
            i += 1
        self.data["profiles"][name] = apps
        self.data["current_profile"] = name
        self.combo_profiles.configure(values=self.get_profile_names())
        self.profile_var.set(name)
        self.save_data()
        self.refresh_list_ui()
        msg = f"Imported '{name}'"
        if missing:
            msg += f" — {missing} app(s) have missing paths"
        self.show_toast(msg, "#E67E22" if missing else "#27AE60", duration=5000)

    def add_app(self):
        path = filedialog.askopenfilename(filetypes=[("Executables", "*.exe"), ("All Files", "*.*")], parent=self)
        if path:
            name = os.path.splitext(os.path.basename(path))[0]
            self.get_current_apps().append({**DEFAULT_APP, "name": name, "path": os.path.abspath(path)})
            self.save_data()
            self.refresh_list_ui()
            self.show_toast(f"Added {name}", "#27AE60")

    def edit_app_row(self, row):
        old_data = dict(self.get_current_apps()[row.index])
        def save(new_d):
            self.get_current_apps()[row.index] = new_d
            self.save_data()
            # INV-5: keep stats history attached across an app rename
            if old_data.get('name') != new_d.get('name'):
                self.app_stats.rename(old_data.get('name'), new_d.get('name'))
            self._apply_edits_to_running(old_data, new_d)
            self.refresh_list_ui()
            self.show_toast("Settings saved", "#27AE60")
        AppSettingsDialog(self, self.get_current_apps()[row.index], save)

    def _apply_edits_to_running(self, old_data: dict, new_data: dict):
        """If the edited app is running, apply priority/affinity/eco changes
        immediately so they don't wait for a relaunch. Failures log, never
        block the save."""
        try:
            running, proc = is_app_running(new_data.get('path'))
            if not running or not proc:
                return
            apply_performance_settings(proc, new_data)
            for action in compute_perf_transitions(old_data, new_data):
                try:
                    if action == 'eco_disable':
                        set_eco_qos(proc.pid, enable=False)
                    elif action == 'affinity_reset':
                        proc.cpu_affinity(list(range(psutil.cpu_count())))
                except Exception as e:
                    log_error("apply_edits_to_running.transition", e)
            self.show_toast("Applied to running process", "#2980B9")
        except Exception as e:
            log_error("apply_edits_to_running", e)

    def delete_app_row(self, row):
        app_name = self.get_current_apps()[row.index].get('name', 'App')
        if messagebox.askyesno("Confirm", f"Delete {app_name}?"):
            del self.get_current_apps()[row.index]
            self.save_data()
            self.refresh_list_ui()
            self.show_toast(f"Deleted {app_name}", "#C0392B")

    def launch_one(self, app_data: dict):
        if not app_data.get('enabled', True):
            self.show_toast("App is disabled", "#E67E22")
            return
        if is_app_running(app_data.get('path'))[0]:
            self.show_toast(f"{app_data['name']} already running", "#E67E22")
            return
        try:
            logging.info(f"launch_one: {app_data.get('name')}")
            self.app_stats.record_launch(app_data['name'])
            proc = launch_executable(app_data)
            self.show_toast(f"Launching {app_data['name']}...", "#2980B9")
            if proc:
                try:
                    apply_performance_settings(psutil.Process(proc.pid), app_data)
                    if self.settings.get('crash_detection'):
                        self.crash_detector.register_app(app_data.get('path'), proc.pid, name=app_data.get('name'),
                                                         popen=proc, auto_restart=app_data.get('auto_restart', False))
                except Exception as e:
                    log_error("Performance settings", e)
            else:
                apply_settings_after_admin_launch(self, app_data.get('path'), app_data, delay_ms=2000)
            app_data['last_run'] = datetime.now().isoformat()
            self.save_data()
        except Exception as e:
            log_error("launch_one", e)
            self.notify("Launch failed", "#C0392B")
        self.after(500, self.monitor_processes_once)

    def launch_sequence(self):
        # INV-1: button, hotkey, tray menu, IPC, and --launch all reach here;
        # the disabled button no longer guards re-entry, so gate on a flag.
        if self._seq_running:
            self.notify("Sequence already running", "#E67E22")
            return
        apps = self.get_current_apps()
        enabled_apps = [a for a in apps if a.get('enabled', True)]
        if not enabled_apps:
            self.show_toast("No enabled apps", "#E67E22")
            return
        self._seq_running = True
        if self.settings.get('minimize_on_launch'):
            self.withdraw()
        self.btn_launch_seq.configure(state="disabled")
        # The worker thread must not touch Tk directly; UI work goes via ui_call
        def run_seq():
            launched = 0
            logging.info(f"Launch sequence start: {len(enabled_apps)} enabled apps")
            try:
                for app in enabled_apps:
                    if is_app_running(app.get('path'))[0]:
                        self.ui_call(self.show_toast, f"{app['name']} already running, skipping", "#E67E22")
                        continue
                    if not os.path.exists(app['path']):
                        self.ui_call(self.show_toast, f"{app['name']} not found", "#C0392B")
                        continue
                    try:
                        self.app_stats.record_launch(app['name'])
                        self.ui_call(self.show_toast, f"Starting {app['name']}...", "#2980B9")
                        proc = launch_executable(app)
                        if proc:
                            try:
                                apply_performance_settings(psutil.Process(proc.pid), app)
                                if self.settings.get('crash_detection'):
                                    self.crash_detector.register_app(app.get('path'), proc.pid, name=app.get('name'),
                                                                     popen=proc, auto_restart=app.get('auto_restart', False))
                            except Exception:
                                pass
                        else:
                            self.ui_call(apply_settings_after_admin_launch, self, app.get('path'), app, 2000)
                        launched += 1
                        app['last_run'] = datetime.now().isoformat()
                    except Exception as e:
                        log_error(f"Seq {app['name']}", e)
                    delay = app.get('delay', 0)
                    time.sleep(delay if delay > 0 else 1.5)
            finally:
                # INV-1: always clear the guard and re-enable the button, even
                # if the loop raised, so the launcher can never wedge.
                self._seq_running = False
                logging.info(f"Launch sequence complete: launched {launched} apps")
                def finish():
                    self.save_data()
                    self.notify(f"Sequence complete! Launched {launched} apps", "#27AE60")
                    self.btn_launch_seq.configure(state="normal")
                    self.after(500, self.monitor_processes_once)
                self.ui_call(finish)
        threading.Thread(target=run_seq, daemon=True).start()

    def kill_one(self, exe_path: str):
        # kill_app_gracefully can block up to 5s waiting on the process,
        # so run it off the UI thread
        def worker():
            kill_app_gracefully(exe_path)
            self.ui_call(self.monitor_processes_once)
        threading.Thread(target=worker, daemon=True).start()

    def kill_all(self):
        apps = list(self.get_current_apps())
        if messagebox.askyesno("Confirm", "Stop all running apps?"):
            def worker():
                count = sum(1 for app in apps if kill_app_gracefully(app.get('path')))
                self.ui_call(self.notify, f"Stopped {count} apps", "#C0392B")
                self.ui_call(self.monitor_processes_once)
            threading.Thread(target=worker, daemon=True).start()

    # --- RACE MODE ---
    def toggle_race_mode(self):
        if self.race_mode:
            self._exit_race_mode()
        else:
            self._enter_race_mode()

    def _set_race_button(self):
        if not hasattr(self, 'btn_race_mode'):
            return
        if self.race_mode:
            self.btn_race_mode.configure(text="🏁 RACE MODE ON", fg_color="#C0392B", border_width=0)
        else:
            self.btn_race_mode.configure(text="🏁 RACE MODE", fg_color="transparent", border_width=1)

    def _enter_race_mode(self):
        self.race_mode = True
        self._set_race_button()
        logging.info("Race mode ON")
        s = self.settings
        targets = []
        if s.get('race_mode_close_other_profiles', True):
            targets = collect_race_mode_kill_targets(
                self.data["profiles"], self.data["current_profile"], s.get('race_mode_sim_exes', []))
        # INV-3: notify BEFORE presentation mode, which would suppress the toast
        self.notify(f"Race mode ON — closing {len(targets)} apps", "#C0392B")
        custom_kill = {x.strip().lower() for x in s.get('race_mode_custom_kill', []) if x and x.strip()}
        if targets or custom_kill:
            def worker():
                for p in targets:
                    kill_app_gracefully(p)
                if custom_kill:
                    for proc in psutil.process_iter(['name']):
                        try:
                            if (proc.info.get('name') or '').lower() in custom_kill:
                                proc.terminate()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            continue
                self.ui_call(self.monitor_processes_once)
            threading.Thread(target=worker, daemon=True).start()
        if s.get('race_mode_presentation_mode', True):
            self._set_presentation_mode(True)
        self._boosted_pids = []
        self._race_boost_pending = False
        if s.get('race_mode_boost_sim', True):
            if not self._boost_sim_now():
                self._race_boost_pending = True  # re-checked each monitor tick

    def _exit_race_mode(self):
        # INV-3: release presentation mode BEFORE the notification
        if self.settings.get('race_mode_presentation_mode', True):
            self._set_presentation_mode(False)
        for pid in getattr(self, '_boosted_pids', []):
            try:
                psutil.Process(pid).nice(psutil.NORMAL_PRIORITY_CLASS)
            except Exception:
                pass
        self._boosted_pids = []
        self._race_boost_pending = False
        self.race_mode = False
        self._set_race_button()
        logging.info("Race mode OFF")
        self.notify("Race mode OFF — apps left running", "#27AE60")

    def _set_presentation_mode(self, on: bool):
        """Toggle Windows Presentation Mode, which suppresses toasts/popups.
        The only supported mechanism (no public Focus Assist API)."""
        try:
            exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "PresentationSettings.exe")
            subprocess.run([exe, "/start" if on else "/stop"], timeout=10)
        except Exception as e:
            log_error("presentation_mode", e)
            self.notify("Presentation mode unavailable", "#E67E22")

    def _boost_sim_now(self) -> bool:
        """Set HIGH priority on any running sim process. Returns True if a sim
        was found (or there's nothing configured to look for)."""
        sim_lower = {(x or "").lower() for x in self.settings.get('race_mode_sim_exes', []) if x}
        if not sim_lower:
            return True
        found = False
        for proc in psutil.process_iter(['name', 'pid']):
            try:
                if (proc.info.get('name') or '').lower() in sim_lower:
                    proc.nice(psutil.HIGH_PRIORITY_CLASS)
                    self._boosted_pids.append(proc.info['pid'])
                    found = True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return found

    def monitor_processes(self):
        minimized = getattr(self, 'is_minimized_to_tray', False)
        delay = 10000 if minimized else self.settings.get('monitor_interval', 2000)
        if not minimized:
            # Row visuals only matter while the window is visible
            self.monitor_processes_once()
        if self.settings.get('crash_detection'):
            try:
                self.crash_detector.check_crashes(self.on_app_crashed)
            except Exception as e:
                log_error("monitor_processes.crash_check", e)
        # Apply a pending sim boost once the sim shows up (race mode)
        if getattr(self, '_race_boost_pending', False):
            try:
                if self._boost_sim_now():
                    self._race_boost_pending = False
            except Exception as e:
                log_error("monitor_processes.sim_boost", e)
        if self._alive():
            self.after(delay, self.monitor_processes)

    def on_app_crashed(self, crashed_apps: list):
        for exe_path, data, runtime in crashed_apps:
            # Use the configured app name so it matches the stats key
            app_name = data.get('name', os.path.basename(exe_path))
            self.app_stats.record_crash(app_name)
            self.notify(f"⚠ {app_name} crashed after {runtime:.0f}s", "#E74C3C", duration=5000)

    def monitor_processes_once(self):
        try:
            for w in self.scroll.winfo_children():
                if isinstance(w, (DraggableRow, EnhancedDraggableRow)):
                    running, proc = self.process_tracker.get_app_status(w.app_data.get('path'))
                    w.update_visuals(running, proc)
        except Exception as e:
            log_error("monitor_processes_once", e)

    def minimize_to_tray(self):
        self.withdraw()
        self.is_minimized_to_tray = True
        threading.Thread(target=self.run_tray, daemon=True).start()

    # Tray menu callbacks run on the pystray thread → route through ui_call (INV-8)
    def _tray_launch(self, icon=None, item=None):
        self.ui_call(self.launch_sequence)

    def _tray_race_mode(self, icon=None, item=None):
        self.ui_call(self.toggle_race_mode)

    def _tray_kill_all(self, icon=None, item=None):
        # _hotkey_kill_all restores the window first (INV-2) so the confirm shows
        self.ui_call(self._hotkey_kill_all)

    def run_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("Show", self.restore, default=True),
            pystray.MenuItem("Launch Sequence", self._tray_launch),
            pystray.MenuItem("Race Mode", self._tray_race_mode, checked=lambda item: self.race_mode),
            pystray.MenuItem("Kill All", self._tray_kill_all),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self.quit_app),
        )
        self.tray_icon = pystray.Icon("SL", create_tray_icon_image(), "/Launch", menu)
        try:
            self.tray_icon.run()
        except Exception as e:
            log_error("run_tray", e)

    def _do_restore(self):
        """Bring the window back from the tray. Main thread only — pystray's
        stop() posts a message to the tray thread, so it's safe cross-thread."""
        try:
            if getattr(self, 'tray_icon', None):
                self.tray_icon.stop()
        except Exception:
            pass
        self.deiconify()
        self.is_minimized_to_tray = False
        self.monitor_processes_once()

    def restore(self, i=None, item=None):
        # Runs on the pystray thread — Tk work must go through ui_call
        self.ui_call(self._do_restore)

    def exit_app(self):
        """Shut down cleanly. Must run on the main thread."""
        if self._closing:
            return
        logging.info("App exit")
        # Never leave Presentation Mode / boosted priorities stuck after quit
        if getattr(self, 'race_mode', False):
            try:
                self._exit_race_mode()
            except Exception as e:
                log_error("exit_app.race_mode", e)
        self.save_data()
        self._closing = True
        self.hotkey_manager.unregister_all()
        try:
            if getattr(self, 'tray_icon', None):
                self.tray_icon.stop()
        except Exception:
            pass
        # Close the IPC socket so the accept loop unblocks and the thread dies
        try:
            if getattr(self, '_ipc_socket', None):
                self._ipc_socket.close()
        except Exception:
            pass
        self.destroy()

    def quit_app(self, i=None, item=None):
        # Runs on the pystray thread — Tk work must go through ui_call
        self.ui_call(self.exit_app)

    def refresh_list_ui(self):
        for w in self.scroll.winfo_children():
            w.destroy()
        actions = {
            'delete': self.delete_app_row,
            'edit': self.edit_app_row,
            'launch_one': self.launch_one,
            'kill_one': self.kill_one,
            'drag_start': self.drag_start,
            'drag_end': self.drag_end
        }
        apps = self.get_current_apps()
        if not apps:
            ctk.CTkLabel(self.scroll, text="No apps added. Click '+Add App' to get started!", text_color="gray", font=("Roboto", 12)).pack(pady=50)
            return
        for i, app in enumerate(apps):
            EnhancedDraggableRow(self.scroll, app, actions, i, stats=self.app_stats).pack(fill="x", pady=2, padx=2)

    def drag_start(self, w, e):
        self.drag_data["item"] = w
        w.configure(fg_color="#404040")

    def drag_end(self, w, e):
        if not self.drag_data["item"]:
            return
        src = self.drag_data["item"]
        src.configure(fg_color=("gray90", "#2B2B2B"))
        y = self.scroll.winfo_pointery() - self.scroll.winfo_rooty()
        rows = [x for x in self.scroll.winfo_children() if isinstance(x, (DraggableRow, EnhancedDraggableRow))]
        new_idx = len(rows) - 1
        cy = 0
        for i, r in enumerate(rows):
            if y < cy + r.winfo_height() / 2:
                new_idx = i
                break
            cy += r.winfo_height()
        if src.index != new_idx:
            l = self.get_current_apps()
            l.insert(new_idx, l.pop(src.index))
            self.save_data()
            self.refresh_list_ui()
        self.drag_data["item"] = None

def validate_app_data(app):
    required = ['name', 'path', 'delay', 'priority', 'affinity', 'admin']
    return all(k in app for k in required)

# Canonical defaults for a new app entry — shared by add_app and profile import
DEFAULT_APP = {
    "name": "", "path": "", "delay": 0, "priority": "Normal", "affinity": [],
    "admin": False, "enabled": True, "eco_mode": False, "last_run": None, "auto_restart": False,
}

def parse_profile_import(payload) -> tuple:
    """Validate an imported profile payload. Returns (apps, missing_count) or
    raises ValueError on malformed input. Absent per-app fields are filled from
    DEFAULT_APP; missing exe paths are allowed and counted, not rejected."""
    if not isinstance(payload, dict) or not isinstance(payload.get("apps"), list):
        raise ValueError("Not a valid launch profile file")
    apps = []
    missing = 0
    for entry in payload["apps"]:
        if not isinstance(entry, dict) or not entry.get("name") or "path" not in entry:
            raise ValueError("Profile contains an invalid app entry")
        app = {**DEFAULT_APP, **entry}
        if not validate_app_data(app):
            raise ValueError("Profile contains an invalid app entry")
        if not app.get("path") or not os.path.exists(app["path"]):
            missing += 1
        apps.append(app)
    return apps, missing

if __name__ == "__main__":
    # add_help=False + parse_known_args: under pythonw.exe there is no console,
    # so argparse's error/exit on unknown args would be an invisible no-launch.
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--minimized", action="store_true")
    args, _ = parser.parse_known_args()

    ipc_socket = acquire_single_instance()
    if ipc_socket is None:
        # Port already bound — forward our intent to the running instance.
        if forward_to_primary(cli_args_to_ipc_message(args)):
            sys.exit(0)
        # The port is held by something that isn't us: fail closed with a
        # clear message rather than run a second GUI fighting over the config.
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror(APP_NAME,
                f"Port {IPC_PORT} is already in use by another program, so /Launch can't start.\n\n"
                f"Close the conflicting program (or change IPC_PORT) and try again.")
            root.destroy()
        except Exception:
            pass
        sys.exit(1)
    app = SimLauncherApp(ipc_socket=ipc_socket, cli_profile=args.profile,
                         auto_launch=args.launch, start_minimized=args.minimized)
    app.mainloop()



