"""Pure-logic tests for /Launch. No Tk windows are constructed — importing
launch_dashboard must stay side-effect-free (INV-7). All file-backed classes
are pointed at tmp_path so the user's real data is never touched."""
import os
import sys
import time
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import launch_dashboard as ld


@pytest.fixture
def isolated_files(tmp_path, monkeypatch):
    """Redirect every data-file constant into tmp_path before any file-backed
    class is instantiated."""
    monkeypatch.setattr(ld, "CRASH_LOG_FILE", str(tmp_path / "crash_history.json"))
    monkeypatch.setattr(ld, "STATS_FILE", str(tmp_path / "app_statistics.json"))
    monkeypatch.setattr(ld, "SETTINGS_FILE", str(tmp_path / "launcher_settings.json"))
    monkeypatch.setattr(ld, "CONFIG_FILE", str(tmp_path / "launch_config.json"))
    return tmp_path


class FakePopen:
    """Stands in for subprocess.Popen so crash tests need no real process."""
    def __init__(self, code):
        self._code = code

    def poll(self):
        return self._code


# --- CrashDetector exit-code semantics ---

def test_crash_detector_normal_exit_is_not_a_crash(isolated_files):
    cd = ld.CrashDetector()
    cd.register_app(r"C:\fake\normal.exe", 123, name="NormalApp", popen=FakePopen(0))
    crashed = cd.check_crashes()
    assert crashed == []
    assert r"C:\fake\normal.exe" not in cd.crash_history


def test_crash_detector_nonzero_exit_is_a_crash(isolated_files):
    cd = ld.CrashDetector()
    cd.register_app(r"C:\fake\crashy.exe", 124, name="CrashyApp", popen=FakePopen(1))
    crashed = cd.check_crashes()
    assert len(crashed) == 1
    rec = cd.crash_history[r"C:\fake\crashy.exe"][0]
    assert rec["app"] == "CrashyApp"
    assert rec["exit_code"] == 1


def test_crash_detector_no_popen_short_runtime_is_a_crash(isolated_files):
    cd = ld.CrashDetector()
    cd.register_app(r"C:\fake\short.exe", 125, name="ShortApp")  # no popen handle
    crashed = cd.check_crashes()
    assert len(crashed) == 1


def test_crash_detector_no_popen_long_runtime_is_not_a_crash(isolated_files):
    cd = ld.CrashDetector()
    cd.register_app(r"C:\fake\long.exe", 126, name="LongApp")
    cd.watch_list[r"C:\fake\long.exe"]["started"] = time.time() - 3600
    crashed = cd.check_crashes()
    assert crashed == []


# --- AppStatistics ---

def test_app_statistics_record_and_get(isolated_files):
    stats = ld.AppStatistics()
    stats.record_launch("MyApp")
    stats.record_launch("MyApp")
    stats.record_runtime("MyApp", 120)
    s = stats.get_stats("MyApp")
    assert s["launches"] == 2
    assert s["crashes"] == 0


def test_app_statistics_unknown_returns_empty(isolated_files):
    stats = ld.AppStatistics()
    assert stats.get_stats("Nonexistent") == {}


# --- perf transitions on edit ---

def test_perf_transition_eco_disable_detected():
    old = {"eco_mode": True, "affinity": []}
    new = {"eco_mode": False, "affinity": []}
    assert ld.compute_perf_transitions(old, new) == ["eco_disable"]


def test_perf_transition_affinity_reset_detected():
    old = {"eco_mode": False, "affinity": [0, 1]}
    new = {"eco_mode": False, "affinity": []}
    assert ld.compute_perf_transitions(old, new) == ["affinity_reset"]


def test_perf_transition_none_when_enabling():
    old = {"eco_mode": False, "affinity": []}
    new = {"eco_mode": True, "affinity": [0, 1]}
    assert ld.compute_perf_transitions(old, new) == []


def test_perf_transition_both():
    old = {"eco_mode": True, "affinity": [2, 3]}
    new = {"eco_mode": False, "affinity": []}
    assert set(ld.compute_perf_transitions(old, new)) == {"eco_disable", "affinity_reset"}


# --- icon cache ---

def test_icon_cache_returns_same_object_for_missing_path():
    ld._icon_cache.clear()
    a = ld.get_icon_from_exe(r"C:\fake\does-not-exist.exe")
    b = ld.get_icon_from_exe(r"C:\fake\does-not-exist.exe")
    assert a is b


# --- hotkey settings migration ---

def test_migrate_replaces_legacy_tk_hotkeys():
    settings = {"keyboard_shortcuts": {"launch_seq": "<Control-l>", "kill_all": "<Control-k>", "minimize": "<Control-m>"}}
    ld.migrate_hotkey_settings(settings)
    ks = settings["keyboard_shortcuts"]
    assert ks["launch_seq"] == "ctrl+alt+l"
    assert ks["kill_all"] == "ctrl+alt+k"
    assert "minimize" not in ks           # obsolete key dropped
    assert "race_mode" in ks and "toggle_window" in ks  # new keys present


def test_migrate_keeps_modern_user_combos():
    settings = {"keyboard_shortcuts": {"launch_seq": "f9", "kill_all": "f10"}}
    ld.migrate_hotkey_settings(settings)
    ks = settings["keyboard_shortcuts"]
    assert ks["launch_seq"] == "f9"
    assert ks["kill_all"] == "f10"
    assert ks["race_mode"] == "ctrl+alt+r"  # filled from default


def test_migrate_empty_settings_uses_defaults():
    settings = {}
    ld.migrate_hotkey_settings(settings)
    assert settings["keyboard_shortcuts"] == ld.DEFAULT_SETTINGS["keyboard_shortcuts"]


# --- IPC message parsing ---

def test_parse_ipc_show():
    assert ld.parse_ipc_message('{"action": "show"}') == {"action": "show"}


def test_parse_ipc_launch_with_profile():
    msg = ld.parse_ipc_message('{"action": "launch", "profile": "iRacing"}')
    assert msg["action"] == "launch"
    assert msg["profile"] == "iRacing"


def test_parse_ipc_rejects_unknown_action():
    assert ld.parse_ipc_message('{"action": "delete_everything"}') is None


def test_parse_ipc_rejects_garbage():
    assert ld.parse_ipc_message("not json at all") is None
    assert ld.parse_ipc_message("") is None
    assert ld.parse_ipc_message("[1,2,3]") is None


def test_parse_ipc_rejects_oversized():
    huge = '{"action": "show", "pad": "' + "x" * 5000 + '"}'
    assert ld.parse_ipc_message(huge) is None


# --- CLI args -> IPC message ---

class _Args:
    def __init__(self, profile=None, launch=False, minimized=False):
        self.profile = profile
        self.launch = launch
        self.minimized = minimized


def test_cli_launch_with_profile_maps_to_launch():
    msg = ld.cli_args_to_ipc_message(_Args(profile="iRacing", launch=True))
    assert msg == {"action": "launch", "profile": "iRacing"}


def test_cli_launch_without_profile():
    assert ld.cli_args_to_ipc_message(_Args(launch=True)) == {"action": "launch"}


def test_cli_no_launch_maps_to_show():
    assert ld.cli_args_to_ipc_message(_Args(profile="iRacing")) == {"action": "show"}
    assert ld.cli_args_to_ipc_message(_Args()) == {"action": "show"}


# --- validate_app_data ---

def test_validate_app_data_accepts_complete_record():
    good = {"name": "a", "path": "p", "delay": 0, "priority": "Normal",
            "affinity": [], "admin": False}
    assert ld.validate_app_data(good) is True


def test_validate_app_data_rejects_missing_fields():
    assert ld.validate_app_data({"name": "a", "path": "p"}) is False
