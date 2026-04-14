#!/usr/bin/env python3
import ctypes
import glob
import importlib.util
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import integration
except Exception:
    integration = None
    try:
        _integration_path = Path(__file__).resolve().parent / "integration.py"
        if _integration_path.exists():
            _s = importlib.util.spec_from_file_location("integration", str(_integration_path))
            if _s and _s.loader:
                _m = importlib.util.module_from_spec(_s)
                _s.loader.exec_module(_m)
                integration = _m
    except Exception:
        integration = None
try:
    import analysis_entry_tools
except Exception:
    analysis_entry_tools = None
    try:
        _analysis_entry_path = Path(__file__).resolve().parent / "analysis_entry_tools.py"
        if _analysis_entry_path.exists():
            _sa = importlib.util.spec_from_file_location("analysis_entry_tools", str(_analysis_entry_path))
            if _sa and _sa.loader:
                _ma = importlib.util.module_from_spec(_sa)
                _sa.loader.exec_module(_ma)
                analysis_entry_tools = _ma
    except Exception:
        analysis_entry_tools = None
try:
    import capture_entry_tools
except Exception:
    capture_entry_tools = None
    try:
        _capture_entry_path = Path(__file__).resolve().parent / "capture_entry_tools.py"
        if _capture_entry_path.exists():
            _sc = importlib.util.spec_from_file_location("capture_entry_tools", str(_capture_entry_path))
            if _sc and _sc.loader:
                _mc = importlib.util.module_from_spec(_sc)
                _sc.loader.exec_module(_mc)
                capture_entry_tools = _mc
    except Exception:
        capture_entry_tools = None
try:
    import server_runtime
except Exception:
    server_runtime = None
    try:
        _runtime_path = Path(__file__).resolve().parent / "server_runtime.py"
        if _runtime_path.exists():
            _s2 = importlib.util.spec_from_file_location("server_runtime", str(_runtime_path))
            if _s2 and _s2.loader:
                _m2 = importlib.util.module_from_spec(_s2)
                _s2.loader.exec_module(_m2)
                server_runtime = _m2
    except Exception:
        server_runtime = None


SERVER_NAME = "renderdoc-mcp"
SERVER_VERSION = "0.1.0"
VK_F12 = 0x7B
KEYEVENTF_KEYUP = 0x0002
_COMPAT_TOOLS = integration.load_compat_module() if integration is not None else None
_EXTERNAL_PROXY_MODULE = integration.load_external_proxy_module() if integration is not None else None
_EXTERNAL_PROXY = integration.create_external_proxy(_EXTERNAL_PROXY_MODULE) if integration is not None else None


class _RdcStr(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint64), ("b", ctypes.c_uint64), ("c", ctypes.c_uint64)]


_RDCSTR_FIXED_STATE = 1 << 63


def _tool_definitions() -> List[Dict[str, Any]]:
    tools = [
        {
            "name": "capture_game",
            "description": "Capture a game with RenderDoc (launch new process or attach to an existing process) and auto-trigger a frame.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "capture_mode": {
                        "type": "string",
                        "enum": ["launch", "attach"],
                        "description": "launch: start executable with RenderDoc. attach: inject into running process.",
                        "default": "launch",
                    },
                    "game_path": {
                        "type": "string",
                        "description": "Absolute path to the game executable. Required for launch mode.",
                    },
                    "game_args": {
                        "oneOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ],
                        "description": "Optional game launch arguments.",
                    },
                    "working_dir": {
                        "type": "string",
                        "description": "Optional working directory for the game process.",
                    },
                    "capture_output": {
                        "type": "string",
                        "description": "Capture file path template. Defaults to ./captures/<game>_<timestamp>.rdc",
                    },
                    "auto_trigger": {
                        "type": "boolean",
                        "description": "Whether to auto-trigger a frame capture.",
                        "default": True,
                    },
                    "trigger_backend": {
                        "type": "string",
                        "enum": ["auto", "qrenderdoc", "targetcontrol", "hotkey"],
                        "description": "auto: for MuMu prefer qrenderdoc python trigger, otherwise targetcontrol, fallback hotkey only if allowed.",
                        "default": "auto",
                    },
                    "trigger_delay_sec": {
                        "type": "number",
                        "description": "Delay before auto-triggering F12.",
                        "default": 5,
                    },
                    "allow_focus_hotkey": {
                        "type": "boolean",
                        "description": "Allow focusing a window and sending F12 as fallback.",
                        "default": False,
                    },
                    "cycle_active_window_count": {
                        "type": "integer",
                        "description": "How many times to call TargetControl.CycleActiveWindow before trigger.",
                        "default": 0,
                    },
                    "qrenderdoc_trigger_count": {
                        "type": "integer",
                        "description": "How many TriggerCapture calls to send in qrenderdoc backend.",
                        "default": 8,
                    },
                    "qrenderdoc_poll_timeout_sec": {
                        "type": "number",
                        "description": "How long qrenderdoc backend polls for NewCapture messages.",
                        "default": 45,
                    },
                    "wait_for_exit": {
                        "type": "boolean",
                        "description": "Wait until game exits before returning.",
                        "default": False,
                    },
                    "open_in_qrenderdoc": {
                        "type": "boolean",
                        "description": "Open latest captured rdc in qrenderdoc.",
                        "default": False,
                    },
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing renderdoccmd.exe and qrenderdoc.exe. Defaults to script directory.",
                    },
                    "target_pid": {
                        "type": "integer",
                        "description": "Target process PID for attach mode.",
                    },
                    "target_process_name": {
                        "type": "string",
                        "description": "Target process name for attach mode if PID is unknown, e.g. 'Game.exe'.",
                    },
                    "focus_process_name": {
                        "type": "string",
                        "description": "Process name to focus before trigger key. Useful when injected process has no window.",
                    },
                    "emulator_profile": {
                        "type": "string",
                        "enum": ["none", "mumu"],
                        "description": "Helper preset for emulator targets.",
                        "default": "none",
                    },
                    "mumu_two_stage": {
                        "type": "boolean",
                        "description": "MuMu workflow: launch MuMuNxDevice first, then inject MuMuVMMHeadless.",
                        "default": False,
                    },
                    "second_stage_delay_sec": {
                        "type": "number",
                        "description": "Delay before second-stage attach in mumu_two_stage mode.",
                        "default": 8,
                    },
                    "hook_children": {
                        "type": "boolean",
                        "description": "Enable RenderDoc child-process hook.",
                        "default": True,
                    },
                    "api_validation": {
                        "type": "boolean",
                        "description": "Enable API validation.",
                        "default": False,
                    },
                    "capture_callstacks": {
                        "type": "boolean",
                        "description": "Capture API callstacks.",
                        "default": False,
                    },
                    "capture_callstacks_only_actions": {
                        "type": "boolean",
                        "description": "Capture callstacks only for actions.",
                        "default": False,
                    },
                    "ref_all_resources": {
                        "type": "boolean",
                        "description": "Reference all resources in capture.",
                        "default": False,
                    },
                    "capture_all_cmd_lists": {
                        "type": "boolean",
                        "description": "Capture all command lists (D3D11).",
                        "default": False,
                    },
                    "verify_buffer_access": {
                        "type": "boolean",
                        "description": "Verify buffer access.",
                        "default": False,
                    },
                    "disallow_fullscreen": {
                        "type": "boolean",
                        "description": "Ask RenderDoc to disallow fullscreen.",
                        "default": False,
                    },
                    "disallow_vsync": {
                        "type": "boolean",
                        "description": "Ask RenderDoc to disallow vsync.",
                        "default": False,
                    },
                    "delay_for_debugger_sec": {
                        "type": "integer",
                        "description": "RenderDoc delay-for-debugger option (seconds).",
                        "default": 0,
                    },
                    "timeout_sec": {
                        "type": "number",
                        "description": "Timeout for renderdoccmd process communication in seconds.",
                        "default": 60,
                    },
                    "collect_unsaved_temp_capture": {
                        "type": "boolean",
                        "description": "If no capture is written to capture_output, collect latest unsaved capture from %TEMP%\\\\RenderDoc.",
                        "default": True,
                    },
                    "temp_capture_lookback_sec": {
                        "type": "number",
                        "description": "How far back to look for unsaved temp captures.",
                        "default": 7200,
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
        },
        {
            "name": "analyze_rdc",
            "description": "Analyze a RenderDoc .rdc capture and return render-flow, texture usage and hotspot summary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rdc_path": {
                        "type": "string",
                        "description": "Absolute path to .rdc file.",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Top-N rows for hotspots/textures/pipeline traces.",
                        "default": 12,
                    },
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing qrenderdoc.exe. Defaults to script parent root.",
                    },
                    "save_json": {
                        "type": "boolean",
                        "description": "Whether to persist analysis artifacts to disk.",
                        "default": True,
                    },
                    "save_root_dir": {
                        "type": "string",
                        "description": "Root directory for saved artifacts. Default: Desktop\\\\RENDERDOC-MCP-SAVE",
                    },
                    "open_report": {
                        "type": "boolean",
                        "description": "Whether to open generated HTML report after analysis.",
                        "default": True,
                    },
                },
                "required": ["rdc_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "focus_rdc_event",
            "description": "Open/switch to an .rdc in qrenderdoc and jump to a specified or hotspot event.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rdc_path": {
                        "type": "string",
                        "description": "Absolute path to .rdc file.",
                    },
                    "event_id": {
                        "type": "integer",
                        "description": "Target eventId to jump to. If omitted, picks hotspot by rank.",
                    },
                    "hotspot_rank": {
                        "type": "integer",
                        "description": "When event_id omitted, choose this hotspot rank (1-based).",
                        "default": 1,
                    },
                    "hotspot_top_n": {
                        "type": "integer",
                        "description": "Hotspot pool size used for auto-pick.",
                        "default": 12,
                    },
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing qrenderdoc.exe. Defaults to script parent root.",
                    },
                    "show_event_browser": {
                        "type": "boolean",
                        "description": "Show Event Browser after jump.",
                        "default": True,
                    },
                    "keep_qrenderdoc_open": {
                        "type": "boolean",
                        "description": "Keep qrenderdoc window open after focus action.",
                        "default": True,
                    },
                    "persist_context": {
                        "type": "boolean",
                        "description": "Persist focused-event context summary to analysis folder.",
                        "default": True,
                    },
                    "context_top_n": {
                        "type": "integer",
                        "description": "Top-N rows used to build focused-event context.",
                        "default": 24,
                    },
                    "save_root_dir": {
                        "type": "string",
                        "description": "Root directory for saved artifacts. Default: Desktop\\\\RENDERDOC-MCP-SAVE",
                    },
                },
                "required": ["rdc_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "analyze_event",
            "description": "Analyze one specific event in an .rdc and export related textures/context for diagnosis.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rdc_path": {
                        "type": "string",
                        "description": "Absolute path to .rdc file.",
                    },
                    "event_id": {
                        "type": "integer",
                        "description": "Target eventId to analyze.",
                    },
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing qrenderdoc.exe. Defaults to script parent root.",
                    },
                    "save_root_dir": {
                        "type": "string",
                        "description": "Root directory for saved artifacts. Default: Desktop\\\\RENDERDOC-MCP-SAVE",
                    },
                    "export_images": {
                        "type": "boolean",
                        "description": "Export input/output textures to PNG.",
                        "default": True,
                    },
                },
                "required": ["rdc_path", "event_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_event_state",
            "description": "Get one-call state summary for an event (shader/resources/hotspot stats).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rdc_path": {"type": "string", "description": "Absolute path to .rdc file."},
                    "event_id": {"type": "integer", "description": "Target eventId."},
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing qrenderdoc.exe. Defaults to script parent root.",
                    },
                    "save_root_dir": {
                        "type": "string",
                        "description": "Root directory for saved artifacts. Default: Desktop\\\\RENDERDOC-MCP-SAVE",
                    },
                },
                "required": ["rdc_path", "event_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "compare_events",
            "description": "Compare two events in one capture and report differences in shaders/resources/perf.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "rdc_path": {"type": "string", "description": "Absolute path to .rdc file."},
                    "event_a": {"type": "integer", "description": "Event A id."},
                    "event_b": {"type": "integer", "description": "Event B id."},
                    "renderdoc_dir": {
                        "type": "string",
                        "description": "Directory containing qrenderdoc.exe. Defaults to script parent root.",
                    },
                    "save_root_dir": {
                        "type": "string",
                        "description": "Root directory for saved artifacts. Default: Desktop\\\\RENDERDOC-MCP-SAVE",
                    },
                },
                "required": ["rdc_path", "event_a", "event_b"],
                "additionalProperties": False,
            },
        },
    ]
    if integration is None:
        return tools
    return integration.merge_tool_definitions(
        base_tools=tools,
        compat_module=_COMPAT_TOOLS,
        external_proxy=_EXTERNAL_PROXY,
    )


def _normalize_game_args(game_args: Any) -> List[str]:
    if game_args is None:
        return []
    if isinstance(game_args, list):
        return [str(x) for x in game_args]
    if isinstance(game_args, str):
        return [game_args]
    raise ValueError("game_args must be string or string array")


def _resolve_renderdoc_paths(renderdoc_dir: Optional[str]) -> Tuple[Path, Path]:
    base = Path(renderdoc_dir) if renderdoc_dir else Path(__file__).resolve().parent.parent
    cmd = base / "renderdoccmd.exe"
    gui = base / "qrenderdoc.exe"
    return cmd, gui


def _default_capture_path(game_path: Path) -> Path:
    captures_dir = Path(__file__).resolve().parent / "captures"
    captures_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return captures_dir / f"{game_path.stem}_{stamp}.rdc"


def _set_foreground_for_process_name(process_name: str) -> bool:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    target = process_name.lower()
    found = {"hwnd": None}

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def enum_windows(hwnd: int, lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True

        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True

        h_process = kernel32.OpenProcess(0x1000, False, pid.value)
        if not h_process:
            return True

        try:
            exe_path_buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if ctypes.windll.psapi.GetModuleFileNameExW(
                h_process, None, exe_path_buf, size
            ):
                if os.path.basename(exe_path_buf.value).lower() == target:
                    found["hwnd"] = hwnd
                    return False
        finally:
            kernel32.CloseHandle(h_process)
        return True

    user32.EnumWindows(EnumWindowsProc(enum_windows), 0)

    if not found["hwnd"]:
        return False

    user32.ShowWindow(found["hwnd"], 5)
    user32.SetForegroundWindow(found["hwnd"])
    return True


def _send_f12() -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(VK_F12, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_F12, 0, KEYEVENTF_KEYUP, 0)


def _make_rdcstr(text: str) -> Tuple[_RdcStr, Any]:
    encoded = text.encode("utf-8")
    buf = ctypes.create_string_buffer(encoded + b"\x00")
    val = _RdcStr(ctypes.addressof(buf), len(encoded), _RDCSTR_FIXED_STATE)
    return val, buf


def _extract_inject_ident(return_code: Optional[int], stdout: str, stderr: str) -> Optional[int]:
    merged = (stdout or "") + "\n" + (stderr or "")
    m = re.search(r"Launched as ID\s+(\d+)", merged)
    if m:
        return int(m.group(1))
    if return_code is not None and return_code > 0 and "Injecting into PID" in merged:
        return int(return_code)
    return None


def _targetcontrol_trigger(
    renderdoc_dll: Path,
    ident: int,
    trigger_frames: int,
    cycle_active_window_count: int,
    client_name: str,
) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "attempted": True,
        "ident": ident,
        "connected": False,
        "target_pid": None,
        "triggered": False,
        "error": None,
    }
    try:
        lib = ctypes.WinDLL(str(renderdoc_dll))
        create = lib.RENDERDOC_CreateTargetControl
        create.argtypes = [
            ctypes.POINTER(_RdcStr),
            ctypes.c_uint32,
            ctypes.POINTER(_RdcStr),
            ctypes.c_bool,
        ]
        create.restype = ctypes.c_void_p

        url, url_buf = _make_rdcstr("")
        client, client_buf = _make_rdcstr(client_name)
        target_ptr = create(ctypes.byref(url), int(ident), ctypes.byref(client), True)

        # Keep references alive until we're done.
        _ = (url_buf, client_buf)

        if not target_ptr:
            details["error"] = "RENDERDOC_CreateTargetControl returned null"
            return details

        vtable = ctypes.cast(
            target_ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents

        shutdown = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(vtable[0])
        connected = ctypes.CFUNCTYPE(ctypes.c_bool, ctypes.c_void_p)(vtable[1])
        getpid = ctypes.CFUNCTYPE(ctypes.c_uint32, ctypes.c_void_p)(vtable[4])
        trigger = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint32)(vtable[6])
        cycle = ctypes.CFUNCTYPE(None, ctypes.c_void_p)(vtable[11])

        details["connected"] = bool(connected(target_ptr))
        details["target_pid"] = int(getpid(target_ptr))

        if details["connected"]:
            for _idx in range(max(cycle_active_window_count, 0)):
                cycle(target_ptr)
                time.sleep(0.1)
            trigger(target_ptr, max(int(trigger_frames), 1))
            details["triggered"] = True

        shutdown(target_ptr)
        return details
    except Exception as exc:
        details["error"] = str(exc)
        return details


def _qrenderdoc_python_trigger(
    qrenderdoc: Path,
    target_process_name: str,
    capture_output: Path,
    trigger_count: int,
    poll_timeout_sec: float,
    trigger_delay_sec: float,
) -> Dict[str, Any]:
    details: Dict[str, Any] = {
        "attempted": True,
        "backend": "qrenderdoc",
        "target_process_name": target_process_name,
        "triggered": False,
        "copied_count": 0,
        "copied_files": [],
        "target_ident": None,
        "target_pid": None,
        "target_api": "",
        "log_path": None,
        "error": None,
    }
    if not qrenderdoc.exists():
        details["error"] = f"qrenderdoc.exe not found: {qrenderdoc}"
        return details

    run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_mcp_qrd_"))
    script_path = run_dir / "qrd_trigger.py"
    result_path = run_dir / "qrd_result.json"
    log_path = run_dir / "qrd_trigger.log"
    details["log_path"] = str(log_path)

    config = {
        "target_name": target_process_name,
        "output_dir": str(capture_output.parent),
        "output_stem": capture_output.stem,
        "trigger_count": max(int(trigger_count), 1),
        "poll_timeout_sec": max(float(poll_timeout_sec), 1.0),
        "trigger_delay_sec": max(float(trigger_delay_sec), 0.0),
        "result_path": str(result_path),
        "log_path": str(log_path),
    }
    config_json = json.dumps(config, ensure_ascii=True)

    script = f"""import json
import os
import time
import traceback

cfg = json.loads({json.dumps(config_json)})
target_name = str(cfg["target_name"]).lower()
output_dir = cfg["output_dir"]
output_stem = cfg["output_stem"]
trigger_count = int(cfg["trigger_count"])
poll_timeout_sec = float(cfg["poll_timeout_sec"])
trigger_delay_sec = float(cfg["trigger_delay_sec"])
result_path = cfg["result_path"]
log_path = cfg["log_path"]

def _log(msg):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{{time.strftime('%H:%M:%S')}}] {{msg}}\\n")

result = {{
    "triggered": False,
    "copied_files": [],
    "target_ident": None,
    "target_pid": None,
    "target_api": "",
    "error": None,
}}

try:
    import renderdoc as rd
    ids = []
    cur = 0
    for _ in range(256):
        nxt = rd.EnumerateRemoteTargets("", cur)
        if nxt == 0:
            break
        ids.append(nxt)
        cur = nxt
    _log(f"targets: {{ids}}")

    target = None
    for ident in ids:
        try:
            t = rd.CreateTargetControl("", ident, "renderdoc-mcp-qrd", True)
            if t is None:
                continue
            tgt = str(t.GetTarget())
            pid = int(t.GetPID())
            api = str(t.GetAPI())
            _log(f"ident={{ident}} target={{tgt}} pid={{pid}} api={{api}} connected={{t.Connected()}}")
            if t.Connected() and tgt.lower() == target_name:
                target = t
                result["target_ident"] = ident
                result["target_pid"] = pid
                result["target_api"] = api
                break
            t.Shutdown()
        except Exception as e:
            _log(f"probe ident={{ident}} error: {{e}}")

    if target is None:
        raise RuntimeError(f"target '{{cfg['target_name']}}' not found in target control list")

    if trigger_delay_sec > 0:
        time.sleep(trigger_delay_sec)
    for _ in range(3):
        target.CycleActiveWindow()
        time.sleep(0.1)

    for i in range(trigger_count):
        target.TriggerCapture(1)
        _log(f"trigger #{{i+1}}")
        time.sleep(1.0)
    result["triggered"] = True

    deadline = time.time() + poll_timeout_sec
    while time.time() < deadline:
        msg = target.ReceiveMessage(None)
        if msg.type == rd.TargetControlMessageType.NewCapture:
            cap = msg.newCapture
            local = os.path.join(output_dir, f"{{output_stem}}_qrd_{{int(time.time())}}_{{cap.captureId}}.rdc")
            target.CopyCapture(cap.captureId, local)
            result["copied_files"].append(local)
            _log(f"new capture id={{cap.captureId}} remote={{cap.path}} local={{local}}")
        time.sleep(0.1)

    target.Shutdown()
except Exception:
    result["error"] = traceback.format_exc()
    _log(result["error"])

with open(result_path, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
"""
    script_path.write_text(script, encoding="utf-8")
    try:
        (event_dir / "_debug_analyze_event_script.py").write_text(script, encoding="utf-8")
    except Exception:
        pass

    proc = subprocess.Popen(
        [str(qrenderdoc), "--python", str(script_path)],
        cwd=str(qrenderdoc.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    wait_timeout = max(int(poll_timeout_sec + trigger_count + trigger_delay_sec + 25), 30)
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        if result_path.exists():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    out, err = "", ""
    if proc.stdout:
        out = proc.stdout.read()
    if proc.stderr:
        err = proc.stderr.read()
    details["process_return_code"] = proc.returncode
    details["stdout"] = (out or "").strip()
    details["stderr"] = (err or "").strip()

    if result_path.exists():
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
            details["triggered"] = bool(payload.get("triggered"))
            copied = payload.get("copied_files", [])
            details["copied_files"] = copied
            details["copied_count"] = len(copied)
            details["target_ident"] = payload.get("target_ident")
            details["target_pid"] = payload.get("target_pid")
            details["target_api"] = payload.get("target_api", "")
            if payload.get("error"):
                details["error"] = payload.get("error")
        except Exception as exc:
            details["error"] = f"failed to parse qrenderdoc result: {exc}"
    elif not details.get("error"):
        details["error"] = "qrenderdoc result file not produced"
    return details


def _default_analysis_save_root() -> Path:
    return Path.home() / "Desktop" / "RENDERDOC-MCP-SAVE"


def _safe_dir_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name)).strip("._")
    return cleaned or "capture"


def _build_texture_lookup(result: Dict[str, Any], top_n: int) -> List[Dict[str, Any]]:
    hotspot_rows = result.get("hotspots", {}).get("topByGpuDuration", []) or []
    duration_by_event: Dict[int, float] = {}
    for row in hotspot_rows:
        try:
            duration_by_event[int(row.get("eventId", 0))] = float(row.get("gpuDuration_us", 0.0))
        except Exception:
            continue

    usage_count_hint: Dict[str, int] = {}
    for row in result.get("textures", {}).get("topByUsageCount", []) or []:
        rid = str(row.get("resourceId", ""))
        if rid:
            try:
                usage_count_hint[rid] = int(row.get("usageCount", 0))
            except Exception:
                usage_count_hint[rid] = 0

    lookup: Dict[str, Dict[str, Any]] = {}
    for row in result.get("pipeline_trace", []) or []:
        try:
            event_id = int(row.get("eventId", 0))
        except Exception:
            event_id = 0
        for sampled in row.get("psSampledResources", []) or []:
            rid = str(sampled.get("resourceId", ""))
            if not rid or rid in ("0", "ResourceId::0"):
                continue
            one = lookup.setdefault(
                rid,
                {
                    "resourceId": rid,
                    "name": sampled.get("name", ""),
                    "sampledByEvents": set(),
                    "outputByEvents": set(),
                    "maxGpuDuration_us": 0.0,
                    "usageCountHint": usage_count_hint.get(rid),
                },
            )
            one["sampledByEvents"].add(event_id)
            one["maxGpuDuration_us"] = max(one["maxGpuDuration_us"], duration_by_event.get(event_id, 0.0))
        for out in row.get("outputTargets", []) or []:
            rid = str(out.get("resourceId", ""))
            if not rid or rid in ("0", "ResourceId::0"):
                continue
            one = lookup.setdefault(
                rid,
                {
                    "resourceId": rid,
                    "name": out.get("name", ""),
                    "sampledByEvents": set(),
                    "outputByEvents": set(),
                    "maxGpuDuration_us": 0.0,
                    "usageCountHint": usage_count_hint.get(rid),
                },
            )
            one["outputByEvents"].add(event_id)
            one["maxGpuDuration_us"] = max(one["maxGpuDuration_us"], duration_by_event.get(event_id, 0.0))

    rows: List[Dict[str, Any]] = []
    for _, one in lookup.items():
        sampled_events = sorted([int(x) for x in one["sampledByEvents"] if int(x) > 0])
        output_events = sorted([int(x) for x in one["outputByEvents"] if int(x) > 0])
        rows.append(
            {
                "resourceId": one["resourceId"],
                "name": one.get("name", ""),
                "sampledByEventIds": sampled_events,
                "outputByEventIds": output_events,
                "hotspotEventIds": sorted(set(sampled_events + output_events)),
                "maxGpuDuration_us": round(float(one.get("maxGpuDuration_us", 0.0)), 3),
                "usageCountHint": one.get("usageCountHint"),
            }
        )

    rows.sort(
        key=lambda x: (
            x.get("maxGpuDuration_us", 0.0),
            len(x.get("hotspotEventIds", [])),
            len(x.get("sampledByEventIds", [])),
        ),
        reverse=True,
    )
    return rows[: max(top_n * 4, top_n)]


def _write_report_helpers(
    *,
    analysis_dir: Path,
    rdc_path: Path,
    qrenderdoc: Path,
    hotspots: List[Dict[str, Any]],
) -> Dict[str, Any]:
    helpers: Dict[str, Any] = {
        "open_capture_bat": None,
        "jump_bats": [],
        "runner_url": None,
        "runner_token": None,
        "generic_jump_script": None,
    }

    open_bat = analysis_dir / "open_capture.bat"
    if qrenderdoc.exists():
        open_bat.write_text(
            f'@echo off\r\nstart "" "{qrenderdoc}" "{rdc_path}"\r\n',
            encoding="utf-8",
        )
    else:
        open_bat.write_text(
            f'@echo off\r\nstart "" "{rdc_path}"\r\n',
            encoding="utf-8",
        )
    helpers["open_capture_bat"] = open_bat.name

    qrenderdoc_lit = repr(str(qrenderdoc))
    rdc_path_lit = repr(str(rdc_path))
    rdc_stem_lit = repr(str(rdc_path.stem).lower())
    generic_jump_py = analysis_dir / "jump_to_event.py"
    jump_code = f"""import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

qrenderdoc = Path({qrenderdoc_lit})
rdc_path = Path({rdc_path_lit})
if len(sys.argv) < 2:
    raise SystemExit("missing event_id")
event_id = int(sys.argv[1])


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False))


def _has_qrenderdoc_running():
    try:
        p = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq qrenderdoc.exe"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        out = (p.stdout or "")
        return "qrenderdoc.exe" in out.lower()
    except Exception:
        return False


def _active_titles() -> str:
    try:
        p = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-Process qrenderdoc -ErrorAction SilentlyContinue | "
                "Where-Object {{$_.MainWindowTitle}} | "
                "Select-Object -ExpandProperty MainWindowTitle) -join '||'",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return (p.stdout or "").strip().lower()
    except Exception:
        return ""


def _make_ui_script(target_rdc: str, target_event: int, result_path: str) -> str:
    cfg = {{"rdc_path": target_rdc, "event_id": int(target_event), "result_path": result_path}}
    cfg_json = json.dumps(cfg, ensure_ascii=True)
    return (
        "import json, os, time, traceback\\n"
        "cfg = json.loads(" + repr(cfg_json) + ")\\n"
        "rdc_path = os.path.abspath(cfg['rdc_path'])\\n"
        "event_id = int(cfg['event_id'])\\n"
        "result_path = cfg['result_path']\\n"
        "res = {{'ok': False, 'event': event_id, 'jumped': 0, 'error': ''}}\\n"
        "try:\\n"
        "    import renderdoc as rd\\n"
        "    ctx = pyrenderdoc\\n"
        "    def _norm(p):\\n"
        "        return os.path.normcase(os.path.abspath(str(p))).replace('/', '\\\\\\\\')\\n"
        "    cur = str(ctx.GetCaptureFilename() or '') if ctx.IsCaptureLoaded() else ''\\n"
        "    if (not ctx.IsCaptureLoaded()) or (_norm(cur) != _norm(rdc_path)):\\n"
        "        ctx.LoadCapture(rdc_path, rd.ReplayOptions(), rdc_path, False, True)\\n"
        "        dl = time.time() + 45.0\\n"
        "        while ctx.IsCaptureLoading() and time.time() < dl:\\n"
        "            time.sleep(0.1)\\n"
        "    first_a = ctx.GetFirstAction()\\n"
        "    last_a = ctx.GetLastAction()\\n"
        "    first_e = int(first_a.eventId) if first_a else 1\\n"
        "    last_e = int(last_a.eventId) if last_a else event_id\\n"
        "    target = max(first_e, min(event_id, last_e))\\n"
        "    ctx.SetEventID([], target, target, True)\\n"
        "    time.sleep(0.1)\\n"
        "    try:\\n"
        "        ctx.ShowTextureViewer()\\n"
        "    except Exception:\\n"
        "        pass\\n"
        "    jumped = int(ctx.CurEvent())\\n"
        "    res['jumped'] = jumped\\n"
        "    res['ok'] = bool(jumped == target)\\n"
        "except Exception:\\n"
        "    res['error'] = traceback.format_exc()\\n"
        "with open(result_path, 'w', encoding='utf-8') as f:\\n"
        "    json.dump(res, f, ensure_ascii=False, indent=2)\\n"
    )


def _run_ui_jump(mode: str, pass_filename: bool):
    run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_mcp_jump_"))
    script_path = run_dir / "jump.py"
    result_path = run_dir / "jump_result.json"
    script_path.write_text(_make_ui_script(str(rdc_path), int(event_id), str(result_path)), encoding="utf-8")
    cmd = [str(qrenderdoc), "--ui-python", str(script_path)]
    if pass_filename:
        cmd.append(str(rdc_path))
    subprocess.Popen(cmd, cwd=str(qrenderdoc.parent))
    deadline = time.time() + 14.0
    while time.time() < deadline:
        if result_path.exists():
            break
        time.sleep(0.2)
    if not result_path.exists():
        return {{"ok": False, "mode": mode, "event": event_id, "error": "timeout_waiting_result"}}
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        return {{"ok": False, "mode": mode, "event": event_id, "error": "bad_result_json"}}
    payload["mode"] = mode
    if "event" not in payload:
        payload["event"] = event_id
    return payload


target_hint = {rdc_stem_lit}
if _has_qrenderdoc_running():
    titles = _active_titles()
    same_capture_open = bool(target_hint and (target_hint in titles))
    if same_capture_open:
        reuse = _run_ui_jump("reuse_ui_python", pass_filename=False)
        if reuse.get("ok"):
            _emit(reuse)
            raise SystemExit(0)
        launch = _run_ui_jump("launch_ui_python_fallback", pass_filename=True)
        _emit(launch)
        raise SystemExit(0)
    reuse = _run_ui_jump("reuse_ui_python", pass_filename=False)
    if reuse.get("ok"):
        _emit(reuse)
        raise SystemExit(0)

launch = _run_ui_jump("launch_ui_python", pass_filename=True)
_emit(launch)
"""
    generic_jump_py.write_text(jump_code, encoding="utf-8")
    helpers["generic_jump_script"] = generic_jump_py.name

    # Local runner server: enables one-click execution from report opened via file://
    seed = str(analysis_dir.resolve()) + "|runner_v17"
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()
    port = 38000 + (int(digest[:4], 16) % 1000)
    token = digest[4:20]
    runner_py = analysis_dir / "report_runner.py"
    runner_code = f"""import argparse
import json
import os
import sys
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, required=True)
parser.add_argument("--root", type=str, required=True)
parser.add_argument("--token", type=str, required=True)
args = parser.parse_args()

ROOT = Path(args.root).resolve()
TOKEN = args.token

class H(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/ping":
            return self._send(200, {{"ok": True}})
        q = parse_qs(u.query or "")
        if (q.get("token", [""])[0] or "") != TOKEN:
            return self._send(403, {{"ok": False, "error": "bad_token"}})
        if u.path == "/run":
            bat = (q.get("bat", [""])[0] or "").strip()
            if not bat:
                return self._send(400, {{"ok": False, "error": "missing_bat"}})
            p = (ROOT / bat).resolve()
            if not str(p).lower().endswith(".bat"):
                return self._send(400, {{"ok": False, "error": "invalid_target"}})
            if not str(p).startswith(str(ROOT)) or not p.exists():
                return self._send(404, {{"ok": False, "error": "bat_not_found"}})
            try:
                subprocess.Popen(["cmd", "/c", "start", "", str(p)], cwd=str(ROOT), shell=False)
                return self._send(200, {{"ok": True, "bat": bat}})
            except Exception as e:
                return self._send(500, {{"ok": False, "error": str(e)}})
        if u.path == "/jump":
            eid_raw = (q.get("event", [""])[0] or "").strip()
            try:
                eid = int(eid_raw)
            except Exception:
                return self._send(400, {{"ok": False, "error": "invalid_event"}})
            if eid <= 0:
                return self._send(400, {{"ok": False, "error": "invalid_event"}})
            jp = (ROOT / "jump_to_event.py").resolve()
            if not str(jp).startswith(str(ROOT)) or not jp.exists():
                return self._send(404, {{"ok": False, "error": "jump_script_missing"}})
            try:
                cp = subprocess.run(
                    [sys.executable, str(jp), str(eid)],
                    cwd=str(ROOT),
                    shell=False,
                    capture_output=True,
                    text=True,
                    timeout=85,
                    check=False,
                )
                raw = (cp.stdout or "").strip()
                line = raw.splitlines()[-1] if raw else ""
                try:
                    payload = json.loads(line) if line else {{"ok": False, "error": "empty_jump_result"}}
                except Exception:
                    payload = {{"ok": False, "error": "invalid_jump_result", "stdout": raw}}
                if "event" not in payload:
                    payload["event"] = eid
                return self._send(200, payload)
            except Exception as e:
                return self._send(500, {{"ok": False, "error": str(e)}})
        return self._send(404, {{"ok": False, "error": "not_found"}})

    def log_message(self, *a):
        return

HTTPServer(("127.0.0.1", args.port), H).serve_forever()
"""
    runner_py.write_text(runner_code, encoding="utf-8")
    try:
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        subprocess.Popen(
            [sys.executable, str(runner_py), "--port", str(port), "--root", str(analysis_dir), "--token", token],
            cwd=str(analysis_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
    except Exception:
        pass
    helpers["runner_url"] = f"http://127.0.0.1:{port}"
    helpers["runner_token"] = token

    for row in (hotspots or [])[:20]:
        try:
            eid = int(row.get("eventId", 0) or 0)
        except Exception:
            continue
        if eid <= 0:
            continue
        bat_name = f"jump_event_{eid}.bat"
        bat_path = analysis_dir / bat_name
        bat_path.write_text(
            f'@echo off\r\npython "{generic_jump_py}" {eid}\r\n',
            encoding="utf-8",
        )
        helpers["jump_bats"].append({"eventId": eid, "bat": bat_name})

    return helpers


def _render_analysis_html(
    result: Dict[str, Any],
    texture_lookup: List[Dict[str, Any]],
    helpers: Optional[Dict[str, Any]] = None,
) -> str:
    flow = result.get("flow", {}) or {}
    hotspots = result.get("hotspots", {}).get("topByGpuDuration", []) or []
    traces = result.get("pipeline_trace", []) or []
    algorithms = result.get("algorithms", {}).get("hints", []) or []
    textures_top = result.get("textures", {}).get("topByUsageCount", []) or []

    def _table(headers: List[str], rows: List[List[str]]) -> str:
        thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body_rows = []
        for row in rows:
            body_rows.append("<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>")
        tbody = "".join(body_rows) if body_rows else "<tr><td colspan='99'>No data</td></tr>"
        return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"

    jump_bats = (helpers or {}).get("jump_bats", []) or []

    def _ev_btn(event_id: Any) -> str:
        try:
            eid = int(event_id)
        except Exception:
            return ""
        return (
            f"<button class='mini' onclick='jumpEvent({eid})'>"
            f"一键跳转到事件 #{eid}</button>"
        )

    trace_event_ids = set()
    for t in traces:
        try:
            trace_event_ids.add(int(t.get("eventId", -1)))
        except Exception:
            pass

    top_hot = hotspots[:5]
    top_hot_gpu = 0.0
    for h in top_hot:
        try:
            top_hot_gpu += float(h.get("gpuDuration_us", 0.0) or 0.0)
        except Exception:
            pass

    # Build evidence-first conclusions
    conclusions: List[Dict[str, Any]] = []
    if top_hot:
        h0 = top_hot[0]
        eid = int(h0.get("eventId", 0) or 0)
        val = float(h0.get("gpuDuration_us", 0.0) or 0.0)
        in_trace = eid in trace_event_ids
        conclusions.append(
            {
                "title": "主要性能热点已定位",
                "confidence": 0.95 if in_trace else 0.75,
                "status": "rule_based",
                "summary": f"当前最重事件是 event {eid}，GPU 时长约 {val:.3f} us。",
                "evidence": [
                    f"来源: hotspots.topByGpuDuration[0]",
                    f"eventId={eid}, gpuDuration_us={val:.3f}",
                    f"在 pipeline_trace 中{'已' if in_trace else '未'}找到该事件",
                ],
                "event": eid,
            }
        )

    if len(top_hot) >= 2:
        a = top_hot[0]
        b = top_hot[1]
        try:
            va = float(a.get("gpuDuration_us", 0.0) or 0.0)
            vb = float(b.get("gpuDuration_us", 0.0) or 0.0)
            ratio = va / vb if vb > 1e-6 else 0.0
            conclusions.append(
                {
                    "title": "热点集中度",
                    "confidence": 0.9,
                    "status": "rule_based",
                    "summary": f"Top1/Top2 时长比约 {ratio:.2f}x，说明热点{'高度集中' if ratio >= 1.3 else '相对分散'}。",
                    "evidence": [
                        f"top1 event={a.get('eventId')} gpu={va:.3f}us",
                        f"top2 event={b.get('eventId')} gpu={vb:.3f}us",
                        "计算: top1/top2",
                    ],
                    "event": int(a.get("eventId", 0) or 0),
                }
            )
        except Exception:
            pass

    if texture_lookup:
        x0 = texture_lookup[0]
        rid = str(x0.get("resourceId", ""))
        max_gpu = float(x0.get("maxGpuDuration_us", 0.0) or 0.0)
        hs = x0.get("hotspotEventIds", []) or []
        conclusions.append(
            {
                "title": "关键资源关联热点",
                "confidence": 0.88,
                "status": "rule_based",
                "summary": f"资源 {rid} 关联多个热点事件，最大关联时长约 {max_gpu:.3f} us。",
                "evidence": [
                    "来源: texture_lookup[0]",
                    f"resourceId={rid}",
                    f"hotspotEventIds={hs[:8]}",
                    f"maxGpuDuration_us={max_gpu:.3f}",
                ],
                "event": int(hs[0]) if hs else None,
            }
        )

    # Consistency checks for trust
    checks: List[Dict[str, Any]] = []
    checks.append(
        {
            "name": "hotspots 非空",
            "ok": bool(hotspots),
            "detail": f"count={len(hotspots)}",
        }
    )
    checks.append(
        {
            "name": "pipeline_trace 非空",
            "ok": bool(traces),
            "detail": f"count={len(traces)}",
        }
    )
    miss_in_trace = []
    for h in hotspots[:10]:
        try:
            eid = int(h.get("eventId", -1))
            if eid not in trace_event_ids:
                miss_in_trace.append(eid)
        except Exception:
            pass
    checks.append(
        {
            "name": "Top10 热点事件可在 trace 找到",
            "ok": len(miss_in_trace) == 0,
            "detail": "missing=" + (",".join(str(x) for x in miss_in_trace) if miss_in_trace else "none"),
        }
    )
    bad_lookup = 0
    for row in texture_lookup[:20]:
        try:
            if not str(row.get("resourceId", "")).strip():
                bad_lookup += 1
        except Exception:
            bad_lookup += 1
    checks.append(
        {
            "name": "texture_lookup 关键字段完整",
            "ok": bad_lookup == 0,
            "detail": f"invalid_rows={bad_lookup}",
        }
    )
    check_ok = sum(1 for c in checks if c["ok"])
    check_score = check_ok / max(len(checks), 1)

    confidence_overall = 0.6
    if conclusions:
        confidence_overall = sum(float(c.get("confidence", 0.0)) for c in conclusions) / len(conclusions)
    confidence_overall = round((confidence_overall * 0.7 + check_score * 0.3), 3)

    hotspot_rows = [
        [
            str(x.get("eventId", "")),
            str(x.get("gpuDuration_us", "")),
            str(x.get("numIndices", "")),
            str(x.get("numInstances", "")),
            str(x.get("name", "")),
            "Yes" if int(x.get("eventId", -1) or -1) in trace_event_ids else "No",
        ]
        for x in hotspots
    ]
    texture_rows = [
        [
            str(x.get("resourceId", "")),
            str(x.get("usageCount", "")),
            str(x.get("width", "")),
            str(x.get("height", "")),
            str(x.get("format", "")),
            str(x.get("name", "")),
        ]
        for x in textures_top
    ]
    lookup_rows = [
        [
            str(x.get("resourceId", "")),
            str(x.get("maxGpuDuration_us", "")),
            ",".join(str(i) for i in x.get("hotspotEventIds", [])),
            ",".join(str(i) for i in x.get("sampledByEventIds", [])),
            ",".join(str(i) for i in x.get("outputByEventIds", [])),
            str(x.get("name", "")),
        ]
        for x in texture_lookup
    ]
    trace_rows = []
    for row in traces:
        ps_inputs = ",".join(str(x.get("resourceId", "")) for x in row.get("psSampledResources", []) or [])
        outputs = ",".join(str(x.get("resourceId", "")) for x in row.get("outputTargets", []) or [])
        trace_rows.append(
            [
                str(row.get("eventId", "")),
                str((row.get("vs", {}) or {}).get("id", "")),
                str((row.get("ps", {}) or {}).get("id", "")),
                ps_inputs,
                outputs,
            ]
        )

    conclusion_cards = []
    for c in conclusions:
        evid = "".join(f"<li>{escape(str(e))}</li>" for e in (c.get("evidence") or []))
        event_btn = _ev_btn(c.get("event")) if c.get("event") else ""
        conclusion_cards.append(
            "<div class='conclusion'>"
            f"<div class='c-head'><b>{escape(str(c.get('title', '')))}</b>"
            f"<span class='tag'>{escape(str(c.get('status', '')))}</span>"
            f"<span class='conf'>confidence={float(c.get('confidence', 0.0)):.2f}</span></div>"
            f"<div class='c-body'>{escape(str(c.get('summary', '')))}</div>"
            f"<div class='c-actions'>{event_btn}</div>"
            f"<ul>{evid}</ul>"
            "</div>"
        )
    if not conclusion_cards:
        conclusion_cards.append("<div class='conclusion'><b>未生成结论</b><div class='c-body'>数据不足，建议先确认 capture 是否完整。</div></div>")

    check_rows = [[x["name"], "PASS" if x["ok"] else "FAIL", x["detail"]] for x in checks]
    open_bat = (helpers or {}).get("open_capture_bat")
    quick_items: List[str] = []
    runner_url = str((helpers or {}).get("runner_url") or "")
    runner_token = str((helpers or {}).get("runner_token") or "")
    if open_bat:
        quick_items.append(
            f"<button class='mini' onclick='runAction({json.dumps(str(open_bat), ensure_ascii=False)})'>"
            f"一键打开本地 RenderDoc</button>"
        )
    for jb in jump_bats[:10]:
        eid = int(jb.get("eventId", 0))
        quick_items.append(
            f"<button class='mini' onclick='jumpEvent({eid})'>"
            f"一键跳转到事件 #{eid}</button>"
        )
    quick_ops_html = "".join(f"<li>{x}</li>" for x in quick_items) or "<li>无可用快捷操作</li>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>RenderDoc MCP Evidence Report</title>
  <style>
    :root {{
      --bg: #f3f7fa;
      --card: #ffffff;
      --text: #12212f;
      --muted: #5f7283;
      --line: #d7e0e7;
      --accent: #0b6d7a;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: "Segoe UI", "Noto Sans SC", sans-serif; background: var(--bg); color: var(--text); }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 20px; }}
    .hero {{ background: linear-gradient(120deg, #dceff2, #f6fbff); border: 1px solid var(--line); border-radius: 14px; padding: 16px; margin-bottom: 16px; }}
    .hero h1 {{ margin: 0 0 6px; font-size: 22px; }}
    .muted {{ color: var(--muted); font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin-top: 10px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 10px; }}
    .kpi b {{ font-size: 18px; display: block; margin-top: 4px; }}
    .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px; margin-bottom: 12px; overflow: auto; }}
    h2 {{ margin: 0 0 8px; font-size: 16px; color: var(--accent); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #e8eef3; text-align: left; padding: 7px 6px; vertical-align: top; }}
    th {{ position: sticky; top: 0; background: #f7fbff; z-index: 1; }}
    ul {{ margin: 6px 0 0 18px; padding: 0; }}
    .conclusion {{ border: 1px solid #dce7ef; border-radius: 10px; padding: 10px; margin-bottom: 10px; background: #fbfdff; }}
    .c-head {{ display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }}
    .tag {{ font-size: 11px; color: #0f6674; background: #e6f4f6; border: 1px solid #cbe6ea; border-radius: 999px; padding: 2px 8px; }}
    .conf {{ font-size: 11px; color: #29465c; background: #eef4fb; border: 1px solid #d7e3f0; border-radius: 999px; padding: 2px 8px; }}
    .c-body {{ color: #163245; font-size: 13px; }}
    .c-actions {{ margin-top: 6px; }}
    .mini {{ border: 1px solid #bfd2e2; background: #f7fbff; color: #114b66; border-radius: 6px; padding: 3px 8px; font-size: 11px; cursor: pointer; }}
    .score {{ font-size: 22px; font-weight: 700; color: #0b6d7a; }}
    .source-note {{ font-size: 12px; color: #4d6475; }}
  </style>
  <script>
    function runAction(path) {{
      try {{
        const url = {json.dumps(runner_url, ensure_ascii=False)};
        const token = {json.dumps(runner_token, ensure_ascii=False)};
        if (!url || !token) {{
          throw new Error("runner_unavailable");
        }}
        fetch(url + "/run?token=" + encodeURIComponent(token) + "&bat=" + encodeURIComponent(path), {{
          method: "GET",
          mode: "cors"
        }}).then(r => r.json()).then(j => {{
          if (!j.ok) {{
            alert("自动执行失败: " + JSON.stringify(j));
          }}
        }}).catch(() => {{
          alert("自动执行失败");
        }});
      }} catch (e) {{
        alert("自动执行失败");
      }}
    }}
    function jumpEvent(eventId) {{
      try {{
        const eid = Number(eventId);
        if (!Number.isFinite(eid) || eid <= 0) {{
          alert("无效事件ID");
          return;
        }}
        const url = {json.dumps(runner_url, ensure_ascii=False)};
        const token = {json.dumps(runner_token, ensure_ascii=False)};
        if (!url || !token) {{
          throw new Error("runner_unavailable");
        }}
        fetch(url + "/jump?token=" + encodeURIComponent(token) + "&event=" + encodeURIComponent(String(eid)), {{
          method: "GET",
          mode: "cors"
        }}).then(r => r.json()).then(j => {{
          if (!j.ok) {{
            alert("事件跳转失败: " + JSON.stringify(j));
            return;
          }}
          console.log("事件跳转回执:", j);
        }}).catch(() => {{
          alert("事件跳转失败");
        }});
      }} catch (e) {{
        alert("事件跳转失败");
      }}
    }}
  </script>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>RenderDoc MCP 证据报告</h1>
      <div class="muted">{escape(str(result.get("rdc_path", "")))}</div>
      <div class="grid">
        <div class="kpi">Pipeline<b>{escape(str(result.get("pipeline", "")))}</b></div>
        <div class="kpi">Events<b>{escape(str(flow.get("eventCount", 0)))}</b></div>
        <div class="kpi">Draws<b>{escape(str(flow.get("drawCount", 0)))}</b></div>
        <div class="kpi">Begin/EndPass<b>{escape(str(flow.get("beginPassCount", 0)))}/{escape(str(flow.get("endPassCount", 0)))}</b></div>
        <div class="kpi">Top5 Hotspot 累计(us)<b>{escape(str(round(top_hot_gpu, 3)))}</b></div>
        <div class="kpi">整体可信度<b class="score">{confidence_overall:.2f}</b></div>
      </div>
      <div class="source-note">
        数据来源: RenderDoc API 原始字段（flow/hotspots/pipeline_trace/texture_lookup）。每条结论下都附可追溯证据。
      </div>
    </div>

    <div class="card">
      <h2>结论（可追溯）</h2>
      {"".join(conclusion_cards)}
    </div>

    <div class="card">
      <h2>快速操作</h2>
      <ul>{quick_ops_html}</ul>
    </div>

    <div class="card">
      <h2>一致性检查</h2>
      {_table(["检查项", "结果", "详情"], check_rows)}
    </div>

    <div class="card">
      <h2>算法提示（辅助）</h2>
      <ul>{"".join(f"<li>{escape(str(x))}</li>" for x in algorithms) or "<li>No hint</li>"}</ul>
    </div>

    <div class="card">
      <h2>Top GPU Hotspots（含可追溯标记）</h2>
      {_table(["eventId", "gpuDuration_us", "numIndices", "numInstances", "name", "in_trace"], hotspot_rows)}
    </div>

    <div class="card">
      <h2>Top Texture Usage（仅决策相关）</h2>
      {_table(["resourceId", "usageCount", "width", "height", "format", "name"], texture_rows)}
    </div>

    <div class="card">
      <h2>资源-热点映射（证据）</h2>
      {_table(["resourceId", "maxGpuDuration_us", "hotspotEventIds", "sampledByEventIds", "outputByEventIds", "name"], lookup_rows)}
    </div>

    <div class="card">
      <h2>Pipeline Trace（热点事件上下文）</h2>
      {_table(["eventId", "vsShaderId", "psShaderId", "psSampledResources", "outputTargets"], trace_rows)}
    </div>
  </div>
</body>
</html>"""


def _render_event_analysis_html(payload: Dict[str, Any]) -> str:
    event = payload.get("event", {}) or {}
    pipeline = payload.get("pipeline", {}) or {}
    stats = payload.get("stats", {}) or {}
    analysis = payload.get("analysis", {}) or {}
    resources = payload.get("resources", []) or []

    def _table(headers: List[str], rows: List[List[str]]) -> str:
        thead = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in row) + "</tr>"
            for row in rows
        ) or "<tr><td colspan='99'>No data</td></tr>"
        return f"<table><thead><tr>{thead}</tr></thead><tbody>{body}</tbody></table>"

    res_rows = [
        [
            str(r.get("kind", "")),
            str(r.get("slot", "")),
            str(r.get("resourceId", "")),
            str(r.get("name", "")),
            str(r.get("imagePath", "")),
            str(r.get("saveTextureResult", "")),
        ]
        for r in resources
    ]
    reason_items = "".join(f"<li>{escape(str(x))}</li>" for x in (analysis.get("reasons", []) or [])) or "<li>无</li>"
    suggestion_items = "".join(f"<li>{escape(str(x))}</li>" for x in (analysis.get("suggestions", []) or [])) or "<li>无</li>"

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Event Analysis</title>
  <style>
    body {{ font-family: "Segoe UI","Noto Sans SC",sans-serif; margin: 0; background: #f4f8fb; color: #15202b; }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 18px; }}
    .card {{ background: #fff; border: 1px solid #dbe5ee; border-radius: 12px; padding: 12px; margin-bottom: 12px; }}
    .kpi {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(170px,1fr)); gap: 10px; }}
    .kpi div {{ background: #f8fbff; border: 1px solid #e3edf5; border-radius: 10px; padding: 8px; }}
    h1 {{ margin: 0 0 10px; font-size: 22px; }}
    h2 {{ margin: 0 0 8px; font-size: 16px; color: #0f6674; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #edf2f7; text-align: left; padding: 7px 6px; vertical-align: top; }}
    th {{ background: #f8fbff; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>事件分析 Event {escape(str(event.get("eventId", "")))}</h1>
      <div>{escape(str(payload.get("rdc_path", "")))}</div>
    </div>
    <div class="card kpi">
      <div>DrawCall<br><b>{escape(str(event.get("drawcall", "")))}</b></div>
      <div>GPU Duration(us)<br><b>{escape(str(stats.get("gpuDuration_us", "")))}</b></div>
      <div>numIndices<br><b>{escape(str(event.get("numIndices", "")))}</b></div>
      <div>numInstances<br><b>{escape(str(event.get("numInstances", "")))}</b></div>
      <div>Hotspot Rank<br><b>{escape(str(stats.get("hotspotRank", "")))}</b></div>
    </div>
    <div class="card">
      <h2>Pipeline</h2>
      <div>VS: {escape(str((pipeline.get("vs", {}) or {}).get("id", "")))} / {escape(str((pipeline.get("vs", {}) or {}).get("entry", "")))}</div>
      <div>PS: {escape(str((pipeline.get("ps", {}) or {}).get("id", "")))} / {escape(str((pipeline.get("ps", {}) or {}).get("entry", "")))}</div>
    </div>
    <div class="card">
      <h2>原因</h2>
      <ul>{reason_items}</ul>
      <h2>建议</h2>
      <ul>{suggestion_items}</ul>
    </div>
    <div class="card">
      <h2>相关资源</h2>
      {_table(["kind", "slot", "resourceId", "name", "imagePath", "saveResult"], res_rows)}
    </div>
  </div>
</body>
</html>"""


def _analyze_rdc_with_qrenderdoc(
    qrenderdoc: Path,
    rdc_path: Path,
    top_n: int = 12,
    save_json: bool = True,
    save_root_dir: Optional[Path] = None,
    open_report: bool = True,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "rdc_path": str(rdc_path),
        "open_ok": False,
        "pipeline": None,
        "flow": {},
        "hotspots": {},
        "textures": {},
        "pipeline_trace": [],
        "resource_map": {},
        "algorithms": {},
        "texture_lookup": [],
        "errors": [],
        "analysis_json": None,
        "analysis_dir": None,
        "analysis_files": {},
        "report_path": None,
    }
    if not qrenderdoc.exists():
        raise ValueError(f"qrenderdoc.exe not found: {qrenderdoc}")
    if not rdc_path.exists():
        raise ValueError(f"rdc_path not found: {rdc_path}")
    if rdc_path.suffix.lower() != ".rdc":
        raise ValueError(f"rdc_path must be .rdc: {rdc_path}")

    run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_mcp_analyze_"))
    script_path = run_dir / "analyze_rdc.py"
    result_path = run_dir / "analyze_result.json"
    log_path = run_dir / "analyze.log"
    top_n = max(int(top_n), 1)

    output_json: Optional[Path] = None
    analysis_dir: Optional[Path] = None
    if save_json:
        root = (save_root_dir or _default_analysis_save_root()).expanduser().resolve()
        analysis_dir = root / _safe_dir_name(rdc_path.stem)
        analysis_dir.mkdir(parents=True, exist_ok=True)
        output_json = analysis_dir / "analysis.full.json"

    cfg = {
        "rdc_path": str(rdc_path),
        "result_path": str(result_path),
        "log_path": str(log_path),
        "top_n": top_n,
        "persist_output_path": str(output_json) if output_json else "",
    }
    cfg_json = json.dumps(cfg, ensure_ascii=True)
    script = f"""import json
import traceback
import time

cfg = json.loads({json.dumps(cfg_json)})
rdc_path = cfg["rdc_path"]
result_path = cfg["result_path"]
log_path = cfg["log_path"]
top_n = int(cfg["top_n"])
persist_output_path = cfg["persist_output_path"]

def _log(msg):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{{time.strftime('%H:%M:%S')}}] {{msg}}\\n")

res = {{
    "rdc_path": rdc_path,
    "open_ok": False,
    "pipeline": None,
    "flow": {{}},
    "hotspots": {{}},
    "textures": {{}},
    "pipeline_trace": [],
    "resource_map": {{}},
    "algorithms": {{}},
    "errors": [],
}}

try:
    import renderdoc as rd

    cap = rd.OpenCaptureFile()
    st = cap.OpenFile(rdc_path, "", None)
    if st != rd.ResultCode.Succeeded:
        raise RuntimeError(f"OpenFile failed: {{st}}")

    st2, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if st2 != rd.ResultCode.Succeeded:
        raise RuntimeError(f"OpenCapture failed: {{st2}}")
    res["open_ok"] = True
    res["pipeline"] = str(ctrl.GetAPIProperties().pipelineType)

    rmap = {{}}
    for r in ctrl.GetResources():
        rmap[str(r.resourceId)] = str(r.name)
    res["resource_map"] = rmap

    roots = list(ctrl.GetRootActions())
    queue = roots[:]
    actions = []
    draw_ids = []
    begin_pass = 0
    end_pass = 0
    present_ids = []
    while queue:
        a = queue.pop(0)
        flags = a.flags
        name = ""
        try:
            name = str(a.customName)
        except Exception:
            name = ""
        if not name:
            try:
                name = str(a.GetName(ctrl.GetStructuredFile()))
            except Exception:
                name = ""
        one = {{
            "eventId": int(a.eventId),
            "flags": str(flags),
            "name": name,
            "numIndices": int(getattr(a, "numIndices", 0)),
            "numInstances": int(getattr(a, "numInstances", 0)),
        }}
        actions.append(one)
        if (flags & rd.ActionFlags.Drawcall) != 0:
            draw_ids.append(int(a.eventId))
        if (flags & rd.ActionFlags.BeginPass) != 0:
            begin_pass += 1
        if (flags & rd.ActionFlags.EndPass) != 0:
            end_pass += 1
        if (flags & rd.ActionFlags.Present) != 0:
            present_ids.append(int(a.eventId))
        for c in a.children:
            queue.append(c)

    res["flow"] = {{
        "eventCount": len(actions),
        "drawCount": len(draw_ids),
        "beginPassCount": begin_pass,
        "endPassCount": end_pass,
        "presentEventIds": present_ids,
    }}

    # pass segmentation
    pass_segments = []
    stack = []
    for a in actions:
        f = a["flags"]
        if "BeginPass" in f:
            stack.append({{"beginEvent": a["eventId"], "beginName": a["name"], "draws": 0}})
        elif "EndPass" in f and stack:
            seg = stack.pop()
            seg["endEvent"] = a["eventId"]
            seg["endName"] = a["name"]
            pass_segments.append(seg)
        elif "Drawcall" in f and stack:
            stack[-1]["draws"] += 1
    pass_segments.sort(key=lambda x: x.get("draws", 0), reverse=True)
    res["flow"]["topPassesByDraws"] = pass_segments[:top_n]

    # GPU duration hotspots
    gpu_counter = None
    for c in ctrl.EnumerateCounters():
        d = ctrl.DescribeCounter(c)
        if str(d.name) == "GPU Duration":
            gpu_counter = c
            break
    hotspot_rows = []
    if gpu_counter is not None:
        vals = ctrl.FetchCounters([gpu_counter])
        duration_by_event = {{}}
        for v in vals:
            eid = int(v.eventId)
            if eid not in draw_ids:
                continue
            dur = 0.0
            try:
                dur = float(v.value.d)
            except Exception:
                try:
                    dur = float(v.value.u)
                except Exception:
                    dur = 0.0
            duration_by_event[eid] = dur
        for a in actions:
            eid = a["eventId"]
            if eid in duration_by_event:
                hotspot_rows.append({{
                    "eventId": eid,
                    "name": a["name"],
                    "gpuDuration_us": round(duration_by_event[eid] * 1e6, 3),
                    "numIndices": a["numIndices"],
                    "numInstances": a["numInstances"],
                }})
    hotspot_rows.sort(key=lambda x: x.get("gpuDuration_us", 0), reverse=True)
    res["hotspots"] = {{
        "topByGpuDuration": hotspot_rows[:top_n]
    }}

    # texture usage by usage count
    tex_rows = []
    for t in ctrl.GetTextures():
        try:
            u = ctrl.GetUsage(t.resourceId)
            c = len(u)
            if c <= 0:
                continue
            tex_rows.append({{
                "resourceId": str(t.resourceId),
                "name": rmap.get(str(t.resourceId), str(t.name)),
                "usageCount": c,
                "width": int(t.width),
                "height": int(t.height),
                "format": str(t.format),
            }})
        except Exception:
            continue
    tex_rows.sort(key=lambda x: x.get("usageCount", 0), reverse=True)
    res["textures"] = {{
        "totalTextures": len(ctrl.GetTextures()),
        "topByUsageCount": tex_rows[:top_n],
    }}

    # pipeline trace for top gpu events
    trace_rows = []
    top_event_ids = [x["eventId"] for x in hotspot_rows[:top_n]]
    for eid in top_event_ids:
        try:
            ctrl.SetFrameEvent(eid, True)
            p = ctrl.GetPipelineState()
            row = {{
                "eventId": eid,
                "vs": {{
                    "id": str(p.GetShader(rd.ShaderStage.Vertex)),
                    "entry": str(p.GetShaderEntryPoint(rd.ShaderStage.Vertex)),
                }},
                "ps": {{
                    "id": str(p.GetShader(rd.ShaderStage.Pixel)),
                    "entry": str(p.GetShaderEntryPoint(rd.ShaderStage.Pixel)),
                }},
                "psSampledResources": [],
                "outputTargets": [],
            }}
            for u in p.GetReadOnlyResources(rd.ShaderStage.Pixel):
                rid = str(u.descriptor.resource)
                row["psSampledResources"].append({{
                    "resourceId": rid,
                    "name": rmap.get(rid, ""),
                    "slot": int(u.access.index),
                }})
            for d in p.GetOutputTargets():
                rid = str(d.resource)
                row["outputTargets"].append({{
                    "resourceId": rid,
                    "name": rmap.get(rid, ""),
                }})
            trace_rows.append(row)
        except Exception as e:
            res["errors"].append(f"trace event {{eid}}: {{e}}")
    res["pipeline_trace"] = trace_rows

    hints = []
    names = [a["name"].lower() for a in actions]
    if any("drawindexed" in n for n in names):
        hints.append("classic raster forward/deferred draw path")
    if any("copybuffertoimage" in n for n in names):
        hints.append("buffer-to-image upload stage observed")
    if not any("dispatch" in n for n in names):
        hints.append("no explicit compute dispatch in this frame")
    if begin_pass >= 20:
        hints.append("many render passes, likely composition/post-processing chain")
    res["algorithms"] = {{"hints": hints}}

    ctrl.Shutdown()
    cap.Shutdown()
except Exception:
    res["errors"].append(traceback.format_exc())

with open(result_path, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
if persist_output_path:
    with open(persist_output_path, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
"""
    script_path.write_text(script, encoding="utf-8")

    proc = subprocess.Popen(
        [str(qrenderdoc), "--python", str(script_path)],
        cwd=str(qrenderdoc.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        if result_path.exists():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if result_path.exists():
        payload = None
        decode_err = None
        for _ in range(25):
            try:
                payload = json.loads(result_path.read_text(encoding="utf-8"))
                decode_err = None
                break
            except Exception as e:
                decode_err = e
                time.sleep(0.12)
        if payload is not None:
            result.update(payload)
        else:
            result["errors"].append(f"analyze result json parse failed: {decode_err}")
    else:
        result["errors"].append("analyze result file not produced")

    texture_lookup = _build_texture_lookup(result, top_n=top_n)
    result["texture_lookup"] = texture_lookup

    if save_json and analysis_dir is not None:
        artifacts: Dict[str, Path] = {
            "analysis.full.json": analysis_dir / "analysis.full.json",
            "flow.json": analysis_dir / "flow.json",
            "hotspots.json": analysis_dir / "hotspots.json",
            "textures.json": analysis_dir / "textures.json",
            "pipeline_trace.json": analysis_dir / "pipeline_trace.json",
            "resource_map.json": analysis_dir / "resource_map.json",
            "texture_lookup.json": analysis_dir / "texture_lookup.json",
            "algorithms.json": analysis_dir / "algorithms.json",
            "errors.json": analysis_dir / "errors.json",
            "report.html": analysis_dir / "report.html",
        }
        artifacts["analysis.full.json"].write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["flow.json"].write_text(
            json.dumps(result.get("flow", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["hotspots.json"].write_text(
            json.dumps(result.get("hotspots", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["textures.json"].write_text(
            json.dumps(result.get("textures", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["pipeline_trace.json"].write_text(
            json.dumps(result.get("pipeline_trace", []), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["resource_map.json"].write_text(
            json.dumps(result.get("resource_map", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["texture_lookup.json"].write_text(
            json.dumps(texture_lookup, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["algorithms.json"].write_text(
            json.dumps(result.get("algorithms", {}), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        artifacts["errors.json"].write_text(
            json.dumps(result.get("errors", []), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        helpers = _write_report_helpers(
            analysis_dir=analysis_dir,
            rdc_path=rdc_path,
            qrenderdoc=qrenderdoc,
            hotspots=result.get("hotspots", {}).get("topByGpuDuration", []) or [],
        )
        html = _render_analysis_html(result=result, texture_lookup=texture_lookup, helpers=helpers)
        artifacts["report.html"].write_text(html, encoding="utf-8")

        result["analysis_json"] = str(artifacts["analysis.full.json"])
        result["analysis_dir"] = str(analysis_dir)
        result["report_path"] = str(artifacts["report.html"])
        result["analysis_files"] = {k: str(v) for k, v in artifacts.items()}

        if open_report:
            try:
                os.startfile(str(artifacts["report.html"]))  # type: ignore[attr-defined]
            except Exception as exc:
                result["errors"].append(f"open report failed: {exc}")
    return result


def _focus_rdc_event_with_qrenderdoc(
    qrenderdoc: Path,
    rdc_path: Path,
    event_id: int,
    show_event_browser: bool = True,
    keep_qrenderdoc_open: bool = True,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "ok": False,
        "rdc_path": str(rdc_path),
        "requested_event_id": int(event_id),
        "jumped_event_id": None,
        "capture_loaded": False,
        "already_loaded": False,
        "loaded_capture": None,
        "qrenderdoc_pid": None,
        "log_path": None,
        "error": None,
    }
    if not qrenderdoc.exists():
        raise ValueError(f"qrenderdoc.exe not found: {qrenderdoc}")
    if not rdc_path.exists():
        raise ValueError(f"rdc_path not found: {rdc_path}")
    if rdc_path.suffix.lower() != ".rdc":
        raise ValueError(f"rdc_path must be .rdc: {rdc_path}")
    if int(event_id) <= 0:
        raise ValueError("event_id must be > 0")

    run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_mcp_focus_"))
    script_path = run_dir / "focus_event.py"
    result_path = run_dir / "focus_event_result.json"
    log_path = run_dir / "focus_event.log"
    result["log_path"] = str(log_path)

    cfg = {
        "rdc_path": str(rdc_path),
        "event_id": int(event_id),
        "result_path": str(result_path),
        "log_path": str(log_path),
        "show_event_browser": bool(show_event_browser),
    }
    cfg_json = json.dumps(cfg, ensure_ascii=True)
    script = f"""import json
import os
import time
import traceback

cfg = json.loads({json.dumps(cfg_json)})
rdc_path = os.path.abspath(cfg["rdc_path"])
event_id = int(cfg["event_id"])
result_path = cfg["result_path"]
log_path = cfg["log_path"]
show_event_browser = bool(cfg["show_event_browser"])

def _norm(p):
    return os.path.normcase(os.path.abspath(str(p))).replace("/", "\\\\")

def _log(msg):
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{{time.strftime('%H:%M:%S')}}] {{msg}}\\n")

res = {{
    "ok": False,
    "requested_event_id": event_id,
    "jumped_event_id": None,
    "capture_loaded": False,
    "already_loaded": False,
    "loaded_capture": "",
    "error": None,
}}

try:
    import renderdoc as rd
    ctx = pyrenderdoc
    cur_path = ""
    if ctx.IsCaptureLoaded():
        cur_path = str(ctx.GetCaptureFilename() or "")
    _log(f"current capture: {{cur_path}}")

    if ctx.IsCaptureLoaded() and _norm(cur_path) == _norm(rdc_path):
        res["already_loaded"] = True
    else:
        ctx.LoadCapture(rdc_path, rd.ReplayOptions(), rdc_path, False, True)
        deadline = time.time() + 45.0
        while ctx.IsCaptureLoading() and time.time() < deadline:
            time.sleep(0.1)
        if ctx.IsCaptureLoading():
            raise RuntimeError("timeout waiting for capture load")
    if not ctx.IsCaptureLoaded():
        raise RuntimeError("capture not loaded")
    res["capture_loaded"] = True
    res["loaded_capture"] = str(ctx.GetCaptureFilename() or "")

    # clamp to valid action range if needed
    first_a = ctx.GetFirstAction()
    last_a = ctx.GetLastAction()
    first_e = int(first_a.eventId) if first_a else 1
    last_e = int(last_a.eventId) if last_a else event_id
    target = max(first_e, min(event_id, last_e))
    ctx.SetEventID([], target, target, True)
    time.sleep(0.05)
    jumped = int(ctx.CurEvent())
    res["jumped_event_id"] = jumped

    if show_event_browser:
        try:
            ctx.ShowEventBrowser()
        except Exception as e:
            _log(f"ShowEventBrowser failed: {{e}}")

    if jumped <= 0:
        raise RuntimeError("jump failed, current event invalid")
    res["ok"] = True
except Exception:
    res["error"] = traceback.format_exc()

with open(result_path, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=2)
"""
    script_path.write_text(script, encoding="utf-8")

    proc = subprocess.Popen(
        [str(qrenderdoc), "--python", str(script_path)],
        cwd=str(qrenderdoc.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    result["qrenderdoc_pid"] = int(proc.pid)
    deadline = time.time() + 65
    while time.time() < deadline:
        if result_path.exists():
            break
        if proc.poll() is not None:
            break
        time.sleep(0.2)

    if proc.poll() is None and (not keep_qrenderdoc_open):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if result_path.exists():
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        result.update(payload)
        if keep_qrenderdoc_open:
            try:
                _set_foreground_for_process_name("qrenderdoc.exe")
            except Exception:
                pass
    else:
        out = (proc.stdout.read() if proc.stdout else "").strip()
        err = (proc.stderr.read() if proc.stderr else "").strip()
        result["error"] = "focus result file not produced"
        if out:
            result["stdout"] = out
        if err:
            result["stderr"] = err

    return result


def _build_focus_event_context(
    analysis: Dict[str, Any], selected_event_id: int
) -> Dict[str, Any]:
    hotspots = analysis.get("hotspots", {}).get("topByGpuDuration", []) or []
    pipeline_trace = analysis.get("pipeline_trace", []) or []
    texture_lookup = analysis.get("texture_lookup", []) or []

    focus_hotspot = None
    hotspot_rank = None
    for i, row in enumerate(hotspots, start=1):
        try:
            if int(row.get("eventId", 0)) == int(selected_event_id):
                focus_hotspot = row
                hotspot_rank = i
                break
        except Exception:
            continue

    focus_trace = None
    for row in pipeline_trace:
        try:
            if int(row.get("eventId", 0)) == int(selected_event_id):
                focus_trace = row
                break
        except Exception:
            continue

    related_textures: List[Dict[str, Any]] = []
    for row in texture_lookup:
        events = row.get("hotspotEventIds", []) or []
        try:
            if int(selected_event_id) in [int(x) for x in events]:
                related_textures.append(row)
        except Exception:
            continue
    related_textures.sort(
        key=lambda x: (float(x.get("maxGpuDuration_us", 0.0)), len(x.get("hotspotEventIds", []) or [])),
        reverse=True,
    )
    related_textures = related_textures[:20]

    hints: List[str] = []
    if focus_hotspot:
        dur = float(focus_hotspot.get("gpuDuration_us", 0.0))
        idx = int(focus_hotspot.get("numIndices", 0))
        inst = int(focus_hotspot.get("numInstances", 0))
        if dur >= 500:
            hints.append("单次 Draw 的 GPU 时长较高，优先从像素着色开销和带宽压力排查。")
        if idx <= 12 and dur > 200:
            hints.append("几何量很小但耗时高，常见于全屏后处理/复杂材质，建议先看 PS 指令与采样数。")
        if inst > 1 and dur > 200:
            hints.append("存在实例化开销，建议检查每实例数据读取与分支。")
    if focus_trace:
        sampled_count = len(focus_trace.get("psSampledResources", []) or [])
        if sampled_count >= 8:
            hints.append("PS 采样纹理数量较多，可尝试降采样、合并贴图或减少多重采样路径。")
        outputs_count = len(focus_trace.get("outputTargets", []) or [])
        if outputs_count >= 3:
            hints.append("输出目标较多（MRT），可评估是否能减少写出通道或拆分 pass。")
    if not hints:
        hints.append("建议先对该事件做 shader 指令级分析（ALU/TEX 比例）和过绘检查。")

    return {
        "eventId": int(selected_event_id),
        "hotspotRank": hotspot_rank,
        "hotspot": focus_hotspot,
        "pipelineTrace": focus_trace,
        "relatedTextures": related_textures,
        "optimizationHints": hints,
    }


def _write_focus_context_files(
    analysis_dir: Path, focus_ctx: Dict[str, Any], rdc_path: Path
) -> Dict[str, str]:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    eid = int(focus_ctx.get("eventId", 0))
    json_path = analysis_dir / f"focus_event_{eid}.json"
    md_path = analysis_dir / f"focus_event_{eid}.md"
    json_path.write_text(json.dumps(focus_ctx, ensure_ascii=False, indent=2), encoding="utf-8")

    hs = focus_ctx.get("hotspot") or {}
    trace = focus_ctx.get("pipelineTrace") or {}
    rel_tex = focus_ctx.get("relatedTextures", []) or []
    hints = focus_ctx.get("optimizationHints", []) or []
    lines = [
        f"# Focus Event {eid}",
        "",
        f"- rdc: `{rdc_path}`",
        f"- hotspotRank: `{focus_ctx.get('hotspotRank')}`",
        f"- gpuDuration_us: `{hs.get('gpuDuration_us')}`",
        f"- drawName: `{hs.get('name')}`",
        f"- numIndices: `{hs.get('numIndices')}`",
        f"- numInstances: `{hs.get('numInstances')}`",
        "",
        "## Pipeline",
        "",
        f"- vs: `{((trace.get('vs') or {}).get('id'))}`",
        f"- ps: `{((trace.get('ps') or {}).get('id'))}`",
        f"- psSampledCount: `{len(trace.get('psSampledResources', []) or [])}`",
        f"- outputTargetCount: `{len(trace.get('outputTargets', []) or [])}`",
        "",
        "## Related Textures (Top)",
        "",
    ]
    for one in rel_tex[:10]:
        lines.append(
            f"- `{one.get('resourceId')}` | maxGpuDuration_us={one.get('maxGpuDuration_us')} | hotspotEvents={one.get('hotspotEventIds')}"
        )
    lines.extend(["", "## Optimization Hints", ""])
    for h in hints:
        lines.append(f"- {h}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "focus_json": str(json_path),
        "focus_markdown": str(md_path),
    }


def _analyze_event_with_qrenderdoc(
    qrenderdoc: Path,
    rdc_path: Path,
    event_id: int,
    save_root_dir: Optional[Path] = None,
    export_images: bool = True,
) -> Dict[str, Any]:
    if not qrenderdoc.exists():
        raise ValueError(f"qrenderdoc.exe not found: {qrenderdoc}")
    if not rdc_path.exists():
        raise ValueError(f"rdc_path not found: {rdc_path}")
    if rdc_path.suffix.lower() != ".rdc":
        raise ValueError(f"rdc_path must be .rdc: {rdc_path}")
    if int(event_id) <= 0:
        raise ValueError("event_id must be > 0")

    root = (save_root_dir or _default_analysis_save_root()).expanduser().resolve()
    event_dir = root / _safe_dir_name(rdc_path.stem) / f"event_{int(event_id)}"
    event_dir.mkdir(parents=True, exist_ok=True)
    images_dir = event_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Stability mode: reuse the proven analyzer path to avoid qrenderdoc crash popups.
    analysis = _analyze_rdc_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        top_n=96,
        save_json=True,
        save_root_dir=root,
        open_report=False,
    )
    focus_ctx = _build_focus_event_context(analysis, int(event_id))
    hotspot = focus_ctx.get("hotspot") or {}
    trace = focus_ctx.get("pipelineTrace") or {}

    payload: Dict[str, Any] = {
        "ok": True,
        "rdc_path": str(rdc_path),
        "event_id": int(event_id),
        "event": {
            "eventId": int(event_id),
            "drawcall": hotspot.get("name", ""),
            "numIndices": hotspot.get("numIndices"),
            "numInstances": hotspot.get("numInstances"),
        },
        "pipeline": {
            "vs": (trace.get("vs") or {}),
            "ps": (trace.get("ps") or {}),
        },
        "resources": [],
        "stats": {
            "gpuDuration_us": hotspot.get("gpuDuration_us"),
            "hotspotRank": focus_ctx.get("hotspotRank"),
        },
        "analysis": {
            "reasons": focus_ctx.get("optimizationHints", []),
            "suggestions": [
                "Compare this event against adjacent hotspot events to detect redundant post-process passes.",
                "In Texture Viewer, reduce Range and inspect single channels/A channel to avoid false black-white interpretation.",
            ],
        },
        "errors": analysis.get("errors", []),
        "note": "stability mode: image export is temporarily disabled to avoid qrenderdoc crash loops",
    }

    for one in (trace.get("psSampledResources", []) or []):
        payload["resources"].append(
            {
                "kind": "ps_sampled",
                "slot": one.get("slot"),
                "resourceId": one.get("resourceId"),
                "name": one.get("name"),
                "imagePath": None,
                "saveTextureResult": "skipped_stability_mode",
            }
        )
    for idx, one in enumerate(trace.get("outputTargets", []) or []):
        payload["resources"].append(
            {
                "kind": "output",
                "slot": idx,
                "resourceId": one.get("resourceId"),
                "name": one.get("name"),
                "imagePath": None,
                "saveTextureResult": "skipped_stability_mode",
            }
        )

    json_path = event_dir / "event_analysis.json"
    md_path = event_dir / "event_analysis.md"
    html_path = event_dir / "event_analysis.html"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# Event Analysis {event_id}",
        "",
        f"- rdc: `{rdc_path}`",
        f"- eventId: `{payload.get('event', {}).get('eventId')}`",
        f"- drawcall: `{payload.get('event', {}).get('drawcall')}`",
        f"- gpuDuration_us: `{payload.get('stats', {}).get('gpuDuration_us')}`",
        "",
        "## Reasons",
        "",
    ]
    for x in payload.get("analysis", {}).get("reasons", []) or []:
        lines.append(f"- {x}")
    lines.extend(["", "## Suggestions", ""])
    for x in payload.get("analysis", {}).get("suggestions", []) or []:
        lines.append(f"- {x}")
    lines.extend(["", "## Resources", ""])
    for r in payload.get("resources", []) or []:
        lines.append(
            f"- {r.get('kind')} slot={r.get('slot')} rid={r.get('resourceId')} image={r.get('imagePath')} saveResult={r.get('saveTextureResult')}"
        )
    md_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
    html_path.write_text(_render_event_analysis_html(payload), encoding="utf-8")

    payload["event_dir"] = str(event_dir)
    payload["event_files"] = {
        "event_analysis_json": str(json_path),
        "event_analysis_md": str(md_path),
        "event_analysis_html": str(html_path),
        "images_dir": str(images_dir),
    }
    return payload


def _find_pid_by_name(process_name: str) -> Optional[int]:
    name = process_name.lower()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        return None

    for line in out.splitlines():
        cols = [c.strip().strip('"') for c in line.split('","')]
        if len(cols) < 2:
            continue
        if cols[0].lower() == name:
            try:
                return int(cols[1])
            except ValueError:
                continue
    return None


def _find_first_pid(process_names: List[str]) -> Optional[Tuple[str, int]]:
    for name in process_names:
        pid = _find_pid_by_name(name)
        if pid is not None:
            return name, pid
    return None


def _latest_capture(path_template: Path) -> Optional[Path]:
    base = str(path_template.with_suffix(""))
    hits = glob.glob(base + "*.rdc")
    if not hits:
        return None
    hits.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return Path(hits[0])


def _collect_from_temp_renderdoc(
    started_at: float,
    capture_output: Path,
    process_hint: Optional[str] = None,
    lookback_sec: float = 7200,
) -> Optional[Path]:
    temp_renderdoc = Path(os.environ.get("TEMP", "")) / "RenderDoc"
    if not temp_renderdoc.exists():
        return None
    cutoff = started_at - max(lookback_sec, 0)
    candidates = []
    for p in temp_renderdoc.glob("*.rdc"):
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        if process_hint and process_hint.lower() not in p.name.lower():
            continue
        candidates.append((mtime, p))
    if not candidates and process_hint:
        # fallback: pick any recent rdc if process-specific naming changed
        for p in temp_renderdoc.glob("*.rdc"):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if mtime >= started_at:
                candidates.append((mtime, p))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    src = candidates[0][1]
    capture_output.parent.mkdir(parents=True, exist_ok=True)
    dst = capture_output
    try:
        shutil.copy2(src, dst)
    except Exception:
        return None
    return dst


def _capture_game(args: Dict[str, Any]) -> Dict[str, Any]:
    started_at = time.time()
    capture_mode = str(args.get("capture_mode", "launch")).lower()
    if capture_mode not in ("launch", "attach"):
        raise ValueError("capture_mode must be 'launch' or 'attach'")

    game_path: Optional[Path] = None
    game_path_arg = args.get("game_path")
    if game_path_arg:
        game_path = Path(game_path_arg).expanduser().resolve()

    game_args = _normalize_game_args(args.get("game_args"))
    if capture_mode == "launch":
        if not game_path:
            raise ValueError("game_path is required in launch mode")
        if not game_path.exists():
            raise ValueError(f"game_path not found: {game_path}")
        if game_path.suffix.lower() != ".exe":
            raise ValueError(f"game_path must be an .exe file: {game_path}")

    working_dir = (
        Path(args["working_dir"]).resolve()
        if args.get("working_dir")
        else (game_path.parent if game_path else Path.cwd())
    )
    capture_output = (
        Path(args["capture_output"]).resolve()
        if args.get("capture_output")
        else _default_capture_path(game_path if game_path else Path("attached_process.exe"))
    )
    auto_trigger = bool(args.get("auto_trigger", True))
    trigger_backend = str(args.get("trigger_backend", "auto")).lower()
    trigger_delay_sec = float(args.get("trigger_delay_sec", 5))
    allow_focus_hotkey = bool(args.get("allow_focus_hotkey", False))
    cycle_active_window_count = int(args.get("cycle_active_window_count", 0))
    qrenderdoc_trigger_count = int(args.get("qrenderdoc_trigger_count", 8))
    qrenderdoc_poll_timeout_sec = float(args.get("qrenderdoc_poll_timeout_sec", 45))
    wait_for_exit = bool(args.get("wait_for_exit", False))
    open_in_qrenderdoc = bool(args.get("open_in_qrenderdoc", False))
    timeout_sec = float(args.get("timeout_sec", 60))
    collect_unsaved_temp_capture = bool(args.get("collect_unsaved_temp_capture", True))
    temp_capture_lookback_sec = float(args.get("temp_capture_lookback_sec", 7200))
    hook_children = bool(args.get("hook_children", True))
    api_validation = bool(args.get("api_validation", False))
    capture_callstacks = bool(args.get("capture_callstacks", False))
    capture_callstacks_only_actions = bool(args.get("capture_callstacks_only_actions", False))
    ref_all_resources = bool(args.get("ref_all_resources", False))
    capture_all_cmd_lists = bool(args.get("capture_all_cmd_lists", False))
    verify_buffer_access = bool(args.get("verify_buffer_access", False))
    disallow_fullscreen = bool(args.get("disallow_fullscreen", False))
    disallow_vsync = bool(args.get("disallow_vsync", False))
    delay_for_debugger_sec = int(args.get("delay_for_debugger_sec", 0))
    emulator_profile = str(args.get("emulator_profile", "none")).lower()
    mumu_two_stage = bool(args.get("mumu_two_stage", False))
    second_stage_delay_sec = float(args.get("second_stage_delay_sec", 8))
    renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))

    if emulator_profile == "mumu" and capture_mode == "launch":
        # Match common working settings from qrenderdoc launch UI profile.
        disallow_fullscreen = False
        disallow_vsync = False
        hook_children = True
        api_validation = True
        capture_callstacks = True
        capture_callstacks_only_actions = True
        ref_all_resources = True
        capture_all_cmd_lists = True
        verify_buffer_access = True

    if not renderdoccmd.exists():
        raise ValueError(f"renderdoccmd.exe not found: {renderdoccmd}")

    capture_output.parent.mkdir(parents=True, exist_ok=True)

    cmd = [str(renderdoccmd)]

    if capture_mode == "launch":
        cmd.extend(["capture", "-d", str(working_dir), "-c", str(capture_output)])
    else:
        target_pid = args.get("target_pid")
        target_proc_name = args.get("target_process_name")
        if target_pid is None and not target_proc_name and emulator_profile == "mumu":
            hit = _find_first_pid(["MuMuVMMHeadless.exe", "MuMuNxDevice.exe"])
            if hit:
                target_proc_name, target_pid = hit[0], hit[1]
        if target_pid is None and target_proc_name:
            proc_name = str(target_proc_name)
            target_pid = _find_pid_by_name(proc_name)
            if target_pid is None:
                raise ValueError(f"target_process_name not found: {proc_name}")
        if target_pid is None:
            raise ValueError("attach mode requires target_pid or target_process_name")
        cmd.extend(["inject", f"--PID={int(target_pid)}", "-c", str(capture_output)])

    if wait_for_exit:
        cmd.append("-w")
    if disallow_fullscreen:
        cmd.append("--opt-disallow-fullscreen")
    if disallow_vsync:
        cmd.append("--opt-disallow-vsync")
    if hook_children:
        cmd.append("--opt-hook-children")
    if delay_for_debugger_sec > 0:
        cmd.extend(["--opt-delay-for-debugger", str(delay_for_debugger_sec)])
    if api_validation:
        cmd.append("--opt-api-validation")
    if capture_callstacks:
        cmd.append("--opt-capture-callstacks")
    if capture_callstacks_only_actions:
        cmd.append("--opt-capture-callstacks-only-actions")
    if ref_all_resources:
        cmd.append("--opt-ref-all-resources")
    if capture_all_cmd_lists:
        cmd.append("--opt-capture-all-cmd-lists")
    if verify_buffer_access:
        cmd.append("--opt-verify-buffer-access")
    if capture_mode == "launch":
        cmd.append(str(game_path))
        cmd.extend(game_args)

    proc = subprocess.Popen(
        cmd,
        cwd=str(working_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    second_stage = {
        "enabled": False,
        "command": None,
        "return_code": None,
        "stdout": "",
        "stderr": "",
    }

    if (
        mumu_two_stage
        and capture_mode == "launch"
        and emulator_profile == "mumu"
    ):
        time.sleep(max(second_stage_delay_sec, 0))
        mumu_vmm = _find_pid_by_name("MuMuVMMHeadless.exe")
        if mumu_vmm is not None:
            second_cmd = [
                str(renderdoccmd),
                "inject",
                f"--PID={int(mumu_vmm)}",
                "-c",
                str(capture_output),
                "--opt-hook-children",
            ]
            second_proc = subprocess.Popen(
                second_cmd,
                cwd=str(working_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                s_out, s_err = second_proc.communicate(timeout=max(timeout_sec, 1))
            except subprocess.TimeoutExpired:
                s_out, s_err = "", ""
            second_stage = {
                "enabled": True,
                "command": second_cmd,
                "return_code": second_proc.returncode,
                "stdout": s_out.strip(),
                "stderr": s_err.strip(),
            }
        else:
            second_stage = {
                "enabled": True,
                "command": None,
                "return_code": None,
                "stdout": "",
                "stderr": "MuMuVMMHeadless.exe not found for second stage attach",
            }

    if wait_for_exit:
        stdout, stderr = proc.communicate(timeout=max(timeout_sec, 1))
    else:
        try:
            stdout, stderr = proc.communicate(timeout=max(timeout_sec, 1))
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""

    trigger_note = "auto_trigger disabled"
    trigger_details: Dict[str, Any] = {"attempted": False}
    if auto_trigger:
        selected_backend = trigger_backend
        if selected_backend == "auto":
            selected_backend = "qrenderdoc" if emulator_profile == "mumu" else "targetcontrol"

        if selected_backend == "qrenderdoc":
            target_name_for_qrd = "MuMuVMMHeadless"
            trigger_details = _qrenderdoc_python_trigger(
                qrenderdoc=qrenderdoc,
                target_process_name=target_name_for_qrd,
                capture_output=capture_output,
                trigger_count=qrenderdoc_trigger_count,
                poll_timeout_sec=qrenderdoc_poll_timeout_sec,
                trigger_delay_sec=trigger_delay_sec,
            )
            if trigger_details.get("copied_count", 0) > 0:
                trigger_note = (
                    f"qrenderdoc trigger succeeded; copied={trigger_details.get('copied_count')} "
                    f"target={target_name_for_qrd}"
                )
            else:
                trigger_note = (
                    f"qrenderdoc trigger finished without captures; error={trigger_details.get('error')}"
                )

        time.sleep(max(trigger_delay_sec, 0) if selected_backend != "qrenderdoc" else 0)
        ident = _extract_inject_ident(proc.returncode, stdout, stderr)
        use_target_control = selected_backend == "targetcontrol"
        if use_target_control and ident is not None:
            renderdoc_dll = renderdoccmd.parent / "renderdoc.dll"
            if renderdoc_dll.exists():
                trigger_details = _targetcontrol_trigger(
                    renderdoc_dll=renderdoc_dll,
                    ident=ident,
                    trigger_frames=1,
                    cycle_active_window_count=cycle_active_window_count,
                    client_name="renderdoc-mcp",
                )
                if trigger_details.get("triggered"):
                    trigger_note = (
                        f"targetcontrol TriggerCapture(1) after {trigger_delay_sec}s; "
                        f"ident={ident}; connected={trigger_details.get('connected')}"
                    )
                else:
                    trigger_note = (
                        f"targetcontrol failed after {trigger_delay_sec}s; "
                        f"ident={ident}; error={trigger_details.get('error')}"
                    )
            else:
                trigger_note = f"targetcontrol skipped: renderdoc.dll not found at {renderdoc_dll}"

        if (
            not trigger_details.get("triggered")
            and selected_backend == "hotkey"
            and allow_focus_hotkey
        ):
            focus_name = None
            if capture_mode == "launch" and game_path:
                focus_name = game_path.name
            elif args.get("focus_process_name"):
                focus_name = str(args["focus_process_name"])
            elif emulator_profile == "mumu":
                focus_name = "MuMuNxDevice.exe"
            elif args.get("target_process_name"):
                focus_name = str(args["target_process_name"])
            focused = _set_foreground_for_process_name(focus_name) if focus_name else False
            _send_f12()
            trigger_details = {"attempted": True, "backend": "hotkey", "focused": focused}
            trigger_note = (
                f"sent F12 after {trigger_delay_sec}s; foreground_found={focused}"
            )
        elif not trigger_details.get("triggered") and selected_backend == "hotkey":
            trigger_note = (
                f"{trigger_note}; hotkey fallback skipped (allow_focus_hotkey=false)"
            )

    latest = _latest_capture(capture_output)
    temp_collected_from = None
    if latest is None and collect_unsaved_temp_capture:
        process_hint = None
        if args.get("target_process_name"):
            process_hint = str(args.get("target_process_name"))
        elif emulator_profile == "mumu":
            process_hint = "MuMuVMMHeadless"
        temp_latest = _collect_from_temp_renderdoc(
            started_at=started_at,
            capture_output=capture_output,
            process_hint=process_hint,
            lookback_sec=temp_capture_lookback_sec,
        )
        if temp_latest is not None:
            latest = temp_latest
            temp_collected_from = str((Path(os.environ.get("TEMP", "")) / "RenderDoc").resolve())
    if open_in_qrenderdoc and latest and qrenderdoc.exists():
        subprocess.Popen([str(qrenderdoc), str(latest)], cwd=str(qrenderdoc.parent))

    result = {
        "capture_mode": capture_mode,
        "command": cmd,
        "return_code": proc.returncode,
        "second_stage": second_stage,
        "trigger_note": trigger_note,
        "trigger_details": trigger_details,
        "capture_template": str(capture_output),
        "latest_capture": str(latest) if latest else None,
        "temp_collected_from": temp_collected_from,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
    }
    return result


def _analyze_rdc(args: Dict[str, Any]) -> Dict[str, Any]:
    rdc_path_arg = args.get("rdc_path")
    if not rdc_path_arg:
        raise ValueError("rdc_path is required")
    rdc_path = Path(str(rdc_path_arg)).expanduser().resolve()
    top_n = int(args.get("top_n", 12))
    save_json = bool(args.get("save_json", True))
    open_report = bool(args.get("open_report", True))
    save_root_dir_arg = args.get("save_root_dir")
    save_root_dir = (
        Path(str(save_root_dir_arg)).expanduser().resolve()
        if save_root_dir_arg
        else _default_analysis_save_root()
    )
    _renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))
    data = _analyze_rdc_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        top_n=top_n,
        save_json=save_json,
        save_root_dir=save_root_dir,
        open_report=open_report,
    )
    return data


def _focus_rdc_event(args: Dict[str, Any]) -> Dict[str, Any]:
    rdc_path_arg = args.get("rdc_path")
    if not rdc_path_arg:
        raise ValueError("rdc_path is required")
    rdc_path = Path(str(rdc_path_arg)).expanduser().resolve()
    event_id_arg = args.get("event_id")
    hotspot_rank = max(int(args.get("hotspot_rank", 1)), 1)
    hotspot_top_n = max(int(args.get("hotspot_top_n", 12)), 1)
    context_top_n = max(int(args.get("context_top_n", 24)), 1)
    show_event_browser = bool(args.get("show_event_browser", True))
    keep_qrenderdoc_open = bool(args.get("keep_qrenderdoc_open", True))
    persist_context = bool(args.get("persist_context", True))
    save_root_dir_arg = args.get("save_root_dir")
    save_root_dir = (
        Path(str(save_root_dir_arg)).expanduser().resolve()
        if save_root_dir_arg
        else _default_analysis_save_root()
    )
    _renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))

    selected_event_id: Optional[int] = int(event_id_arg) if event_id_arg is not None else None
    auto_pick: Dict[str, Any] = {}
    context_analysis: Optional[Dict[str, Any]] = None
    if selected_event_id is None:
        analysis = _analyze_rdc_with_qrenderdoc(
            qrenderdoc=qrenderdoc,
            rdc_path=rdc_path,
            top_n=max(hotspot_top_n, context_top_n),
            save_json=True,
            save_root_dir=save_root_dir,
            open_report=False,
        )
        context_analysis = analysis
        rows = analysis.get("hotspots", {}).get("topByGpuDuration", []) or []
        idx = hotspot_rank - 1
        if idx < 0 or idx >= len(rows):
            raise ValueError(
                f"hotspot_rank {hotspot_rank} out of range, available hotspot count: {len(rows)}"
            )
        pick = rows[idx]
        selected_event_id = int(pick.get("eventId", 0))
        auto_pick = {
            "hotspot_rank": hotspot_rank,
            "hotspot_event": pick,
            "analysis_dir": analysis.get("analysis_dir"),
            "report_path": analysis.get("report_path"),
        }

    if selected_event_id is None or selected_event_id <= 0:
        raise ValueError("unable to determine target event_id")

    focused = _focus_rdc_event_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        event_id=selected_event_id,
        show_event_browser=show_event_browser,
        keep_qrenderdoc_open=keep_qrenderdoc_open,
    )
    focused["resolved_event_id"] = selected_event_id
    if auto_pick:
        focused["auto_pick"] = auto_pick

    if persist_context:
        if context_analysis is None:
            context_analysis = _analyze_rdc_with_qrenderdoc(
                qrenderdoc=qrenderdoc,
                rdc_path=rdc_path,
                top_n=context_top_n,
                save_json=True,
                save_root_dir=save_root_dir,
                open_report=False,
            )
        focus_ctx = _build_focus_event_context(context_analysis, selected_event_id)
        analysis_dir = context_analysis.get("analysis_dir")
        if analysis_dir:
            files = _write_focus_context_files(Path(str(analysis_dir)), focus_ctx, rdc_path=rdc_path)
            focused["focus_context"] = focus_ctx
            focused["focus_context_files"] = files
        else:
            focused["focus_context"] = focus_ctx
            focused["focus_context_files"] = {}
    return focused


def _analyze_event(args: Dict[str, Any]) -> Dict[str, Any]:
    rdc_path_arg = args.get("rdc_path")
    if not rdc_path_arg:
        raise ValueError("rdc_path is required")
    event_id_arg = args.get("event_id")
    if event_id_arg is None:
        raise ValueError("event_id is required")

    rdc_path = Path(str(rdc_path_arg)).expanduser().resolve()
    event_id = int(event_id_arg)
    export_images = bool(args.get("export_images", True))
    save_root_dir_arg = args.get("save_root_dir")
    save_root_dir = (
        Path(str(save_root_dir_arg)).expanduser().resolve()
        if save_root_dir_arg
        else _default_analysis_save_root()
    )
    _renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))
    return _analyze_event_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        event_id=event_id,
        save_root_dir=save_root_dir,
        export_images=export_images,
    )


def _collect_event_from_analysis(analysis: Dict[str, Any], event_id: int) -> Dict[str, Any]:
    eid = int(event_id)
    hotspots = analysis.get("hotspots", {}).get("topByGpuDuration", []) or []
    trace_rows = analysis.get("pipeline_trace", []) or []
    texture_lookup = analysis.get("texture_lookup", []) or []

    hotspot = None
    rank = None
    for i, row in enumerate(hotspots, start=1):
        try:
            if int(row.get("eventId", -1)) == eid:
                hotspot = row
                rank = i
                break
        except Exception:
            continue

    trace = None
    for row in trace_rows:
        try:
            if int(row.get("eventId", -1)) == eid:
                trace = row
                break
        except Exception:
            continue

    related = []
    for row in texture_lookup:
        evs = row.get("hotspotEventIds", []) or []
        try:
            if eid in [int(x) for x in evs]:
                related.append(row)
        except Exception:
            continue

    return {
        "eventId": eid,
        "hotspotRank": rank,
        "hotspot": hotspot,
        "trace": trace,
        "relatedResources": related,
    }


def _get_event_state(args: Dict[str, Any]) -> Dict[str, Any]:
    rdc_path_arg = args.get("rdc_path")
    if not rdc_path_arg:
        raise ValueError("rdc_path is required")
    event_id_arg = args.get("event_id")
    if event_id_arg is None:
        raise ValueError("event_id is required")

    rdc_path = Path(str(rdc_path_arg)).expanduser().resolve()
    event_id = int(event_id_arg)
    save_root_dir_arg = args.get("save_root_dir")
    save_root_dir = (
        Path(str(save_root_dir_arg)).expanduser().resolve()
        if save_root_dir_arg
        else _default_analysis_save_root()
    )
    _renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))
    analysis = _analyze_rdc_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        top_n=96,
        save_json=True,
        save_root_dir=save_root_dir,
        open_report=False,
    )
    state = _collect_event_from_analysis(analysis, event_id=event_id)
    if state.get("hotspot") is None and state.get("trace") is None:
        raise ValueError(
            f"event {event_id} not found in hotspot/pipeline_trace window; increase analyzer coverage if needed"
        )
    return {
        "rdc_path": str(rdc_path),
        "event_state": state,
        "analysis_dir": analysis.get("analysis_dir"),
    }


def _compare_events(args: Dict[str, Any]) -> Dict[str, Any]:
    rdc_path_arg = args.get("rdc_path")
    if not rdc_path_arg:
        raise ValueError("rdc_path is required")
    if args.get("event_a") is None or args.get("event_b") is None:
        raise ValueError("event_a and event_b are required")

    rdc_path = Path(str(rdc_path_arg)).expanduser().resolve()
    event_a = int(args.get("event_a"))
    event_b = int(args.get("event_b"))
    save_root_dir_arg = args.get("save_root_dir")
    save_root_dir = (
        Path(str(save_root_dir_arg)).expanduser().resolve()
        if save_root_dir_arg
        else _default_analysis_save_root()
    )
    _renderdoccmd, qrenderdoc = _resolve_renderdoc_paths(args.get("renderdoc_dir"))
    analysis = _analyze_rdc_with_qrenderdoc(
        qrenderdoc=qrenderdoc,
        rdc_path=rdc_path,
        top_n=128,
        save_json=True,
        save_root_dir=save_root_dir,
        open_report=False,
    )
    a = _collect_event_from_analysis(analysis, event_id=event_a)
    b = _collect_event_from_analysis(analysis, event_id=event_b)

    ta = a.get("trace") or {}
    tb = b.get("trace") or {}
    a_vs = (ta.get("vs") or {}).get("id")
    b_vs = (tb.get("vs") or {}).get("id")
    a_ps = (ta.get("ps") or {}).get("id")
    b_ps = (tb.get("ps") or {}).get("id")

    a_inputs = sorted(set((x.get("resourceId") for x in (ta.get("psSampledResources") or []) if x.get("resourceId"))))
    b_inputs = sorted(set((x.get("resourceId") for x in (tb.get("psSampledResources") or []) if x.get("resourceId"))))
    a_outputs = sorted(set((x.get("resourceId") for x in (ta.get("outputTargets") or []) if x.get("resourceId"))))
    b_outputs = sorted(set((x.get("resourceId") for x in (tb.get("outputTargets") or []) if x.get("resourceId"))))

    ah = a.get("hotspot") or {}
    bh = b.get("hotspot") or {}
    a_gpu = float(ah.get("gpuDuration_us", 0.0) or 0.0)
    b_gpu = float(bh.get("gpuDuration_us", 0.0) or 0.0)

    return {
        "rdc_path": str(rdc_path),
        "event_a": a,
        "event_b": b,
        "diff": {
            "same_vs_shader": a_vs == b_vs and bool(a_vs),
            "same_ps_shader": a_ps == b_ps and bool(a_ps),
            "vs_shader_a": a_vs,
            "vs_shader_b": b_vs,
            "ps_shader_a": a_ps,
            "ps_shader_b": b_ps,
            "inputs_only_in_a": [x for x in a_inputs if x not in b_inputs],
            "inputs_only_in_b": [x for x in b_inputs if x not in a_inputs],
            "outputs_only_in_a": [x for x in a_outputs if x not in b_outputs],
            "outputs_only_in_b": [x for x in b_outputs if x not in a_outputs],
            "gpuDuration_us_a": a_gpu,
            "gpuDuration_us_b": b_gpu,
            "gpuDuration_delta_us": round(a_gpu - b_gpu, 3),
        },
        "analysis_dir": analysis.get("analysis_dir"),
    }


def _build_tool_handlers() -> Dict[str, Any]:
    handlers: Dict[str, Any] = {}
    if capture_entry_tools is not None:
        try:
            handlers.update(capture_entry_tools.build_handlers(capture_game_fn=_capture_game))
        except Exception:
            handlers["capture_game"] = _capture_game
    else:
        handlers["capture_game"] = _capture_game
    if analysis_entry_tools is not None:
        try:
            handlers.update(
                analysis_entry_tools.build_handlers(
                    analyze_rdc_with_qrenderdoc=_analyze_rdc_with_qrenderdoc,
                    focus_rdc_event_with_qrenderdoc=_focus_rdc_event_with_qrenderdoc,
                    analyze_event_with_qrenderdoc=_analyze_event_with_qrenderdoc,
                    default_analysis_save_root=_default_analysis_save_root,
                    resolve_renderdoc_paths=_resolve_renderdoc_paths,
                    build_focus_event_context=_build_focus_event_context,
                    write_focus_context_files=_write_focus_context_files,
                )
            )
        except Exception:
            handlers.update(
                {
                    "analyze_rdc": _analyze_rdc,
                    "focus_rdc_event": _focus_rdc_event,
                    "analyze_event": _analyze_event,
                    "get_event_state": _get_event_state,
                    "compare_events": _compare_events,
                }
            )
    else:
        handlers.update(
            {
                "analyze_rdc": _analyze_rdc,
                "focus_rdc_event": _focus_rdc_event,
                "analyze_event": _analyze_event,
                "get_event_state": _get_event_state,
                "compare_events": _compare_events,
            }
        )
    if _COMPAT_TOOLS is not None:
        try:
            handlers.update(
                _COMPAT_TOOLS.build_handlers(
                    analyze_rdc=_analyze_rdc,
                    get_event_state=_get_event_state,
                    compare_events=_compare_events,
                    resolve_renderdoc_paths=_resolve_renderdoc_paths,
                )
            )
        except Exception:
            pass
    return handlers


def main() -> None:
    if server_runtime is None:
        raise RuntimeError("server_runtime module unavailable")
    server_runtime.run_stdio_server(
        server_name=SERVER_NAME,
        server_version=SERVER_VERSION,
        tool_definitions_fn=_tool_definitions,
        build_handlers_fn=_build_tool_handlers,
        external_call_fn=(
            (lambda name, arguments: _EXTERNAL_PROXY.call_tool(name, arguments))
            if _EXTERNAL_PROXY is not None
            else None
        ),
    )


if __name__ == "__main__":
    main()
