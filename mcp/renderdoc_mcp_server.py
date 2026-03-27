#!/usr/bin/env python3
import ctypes
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


SERVER_NAME = "renderdoc-mcp"
SERVER_VERSION = "0.1.0"
VK_F12 = 0x7B
KEYEVENTF_KEYUP = 0x0002


def _send_response(req_id: Any, result: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def _send_error(req_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}) + "\n"
    )
    sys.stdout.flush()


def _tool_definitions() -> List[Dict[str, Any]]:
    return [
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
                        "description": "Whether to auto-send F12 to trigger capture.",
                        "default": True,
                    },
                    "trigger_delay_sec": {
                        "type": "number",
                        "description": "Delay before auto-triggering F12.",
                        "default": 5,
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
        }
    ]


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
    trigger_delay_sec = float(args.get("trigger_delay_sec", 5))
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

    trigger_note = "auto_trigger disabled"
    if auto_trigger:
        time.sleep(max(trigger_delay_sec, 0))
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
        trigger_note = (
            f"sent F12 after {trigger_delay_sec}s; foreground_found={focused}"
        )

    if wait_for_exit:
        stdout, stderr = proc.communicate(timeout=max(timeout_sec, 1))
    else:
        try:
            stdout, stderr = proc.communicate(timeout=max(timeout_sec, 1))
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""

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
        "capture_template": str(capture_output),
        "latest_capture": str(latest) if latest else None,
        "temp_collected_from": temp_collected_from,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
    }
    return result


def _text_result(data: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def main() -> None:
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            continue

        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})

        # notifications don't require a response
        wants_response = req_id is not None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                }
                if wants_response:
                    _send_response(req_id, result)
            elif method == "tools/list":
                if wants_response:
                    _send_response(req_id, {"tools": _tool_definitions()})
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if name != "capture_game":
                    raise ValueError(f"Unknown tool: {name}")
                data = _capture_game(arguments)
                if wants_response:
                    _send_response(req_id, _text_result(data, is_error=False))
            elif method in ("notifications/initialized", "ping"):
                if wants_response:
                    _send_response(req_id, {})
            elif method in ("resources/list", "prompts/list"):
                if wants_response:
                    _send_response(req_id, {"resources": []} if method == "resources/list" else {"prompts": []})
            else:
                if wants_response:
                    _send_error(req_id, -32601, f"Method not found: {method}")
        except Exception as exc:
            if wants_response:
                if method == "tools/call":
                    _send_response(req_id, _text_result({"error": str(exc)}, is_error=True))
                else:
                    _send_error(req_id, -32000, str(exc))


if __name__ == "__main__":
    main()
