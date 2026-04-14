#!/usr/bin/env python3
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from queue import Queue, Empty
from typing import Any, Dict, List, Optional


class ExternalMCPProxy:
    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._responses: "Queue[Dict[str, Any]]" = Queue()
        self._id = 1000
        self._lock = threading.Lock()
        self._tool_names: set[str] = set()
        self._enabled = True
        self._initialized = False

    def _candidate_python(self) -> Optional[Path]:
        candidates = [
            Path(r"C:\Users\wepie\Desktop\_ext_renderdoc_trial\.venv313\Scripts\python.exe"),
            Path(r"C:\Users\wepie\Desktop\_ext_linking_src\.venv313\Scripts\python.exe"),
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def _candidate_module_path(self) -> Optional[str]:
        candidates = [
            Path(r"C:\Users\wepie\Desktop\_ext_renderdoc_trial\.venv313\Lib\site-packages\renderdoc_mcp\lib"),
            Path(r"C:\Users\wepie\Desktop\RenderDoc-mcp"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return None

    def _reader_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    self._responses.put(obj)
                except Exception:
                    continue
        except Exception:
            pass

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _request(self, method: str, params: Dict[str, Any], timeout_sec: float = 30.0) -> Dict[str, Any]:
        with self._lock:
            self._ensure_started()
            if not self._proc or not self._proc.stdin:
                raise RuntimeError("external MCP process not available")
            req_id = self._next_id()
            req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
            self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            try:
                msg = self._responses.get(timeout=0.2)
            except Empty:
                continue
            if int(msg.get("id", -1)) != req_id:
                continue
            if "error" in msg:
                raise RuntimeError(str(msg.get("error")))
            return msg.get("result", {})
        raise TimeoutError(f"external MCP request timeout: {method}")

    def _ensure_started(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        if not self._enabled:
            return

        py = self._candidate_python()
        if py is None:
            self._enabled = False
            return

        env = os.environ.copy()
        if "RENDERDOC_MODULE_PATH" not in env:
            module_path = self._candidate_module_path()
            if module_path:
                env["RENDERDOC_MODULE_PATH"] = module_path

        self._proc = subprocess.Popen(
            [str(py), "-m", "renderdoc_mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            cwd=str(Path.home()),
            env=env,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._initialized = False

    def list_tools(self) -> List[Dict[str, Any]]:
        if not self._enabled:
            return []
        try:
            if not self._initialized:
                self._request(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "renderdoc-mcp-proxy", "version": "0.1.0"},
                    },
                    timeout_sec=15.0,
                )
                self._initialized = True
            result = self._request("tools/list", {}, timeout_sec=20.0)
            tools = result.get("tools", []) or []
            self._tool_names = set(str(t.get("name")) for t in tools if t.get("name"))
            return tools
        except Exception:
            return []

    def has_tool(self, name: str) -> bool:
        return name in self._tool_names

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not self._enabled:
            raise RuntimeError("external tool proxy is disabled")
        if not self._initialized or not self._tool_names:
            self.list_tools()
        result = self._request("tools/call", {"name": name, "arguments": arguments}, timeout_sec=120.0)
        content = result.get("content", []) or []
        if not content:
            return {}
        text = str(content[0].get("text", ""))
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
            return {"result": parsed}
        except Exception:
            return {"text": text}
