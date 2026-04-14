#!/usr/bin/env python3
import json
import sys
from typing import Any, Callable, Dict


def _send_response(req_id: Any, result: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def _send_error(req_id: Any, code: int, message: str) -> None:
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}) + "\n"
    )
    sys.stdout.flush()


def _text_result(data: Dict[str, Any], is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def run_stdio_server(
    *,
    server_name: str,
    server_version: str,
    tool_definitions_fn: Callable[[], Any],
    build_handlers_fn: Callable[[], Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]],
    external_call_fn: Callable[[str, Dict[str, Any]], Dict[str, Any]] | None = None,
) -> None:
    tool_handlers = build_handlers_fn()
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
        wants_response = req_id is not None

        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": server_name, "version": server_version},
                }
                if wants_response:
                    _send_response(req_id, result)
            elif method == "tools/list":
                if wants_response:
                    _send_response(req_id, {"tools": tool_definitions_fn()})
            elif method == "tools/call":
                name = str(params.get("name"))
                arguments = params.get("arguments", {})
                handler = tool_handlers.get(name)
                if handler is not None:
                    data = handler(arguments if isinstance(arguments, dict) else {})
                elif external_call_fn is not None:
                    data = external_call_fn(name, arguments if isinstance(arguments, dict) else {})
                else:
                    raise ValueError(f"Unknown tool: {name}")
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

