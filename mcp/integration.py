#!/usr/bin/env python3
import importlib.util
from pathlib import Path
from typing import Any, Dict, List, Optional


def _load_module_by_filename(module_name: str, filename: str):
    try:
        mod = __import__(module_name)
        return mod
    except Exception:
        pass
    try:
        p = Path(__file__).resolve().parent / filename
        if not p.exists():
            return None
        spec = importlib.util.spec_from_file_location(module_name, str(p))
        if not spec or not spec.loader:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def load_compat_module():
    return _load_module_by_filename("compat_tools", "compat_tools.py")


def load_external_proxy_module():
    return _load_module_by_filename("external_tools_proxy", "external_tools_proxy.py")


def create_external_proxy(external_proxy_module) -> Any:
    if external_proxy_module is None:
        return None
    try:
        return external_proxy_module.ExternalMCPProxy()
    except Exception:
        return None


def merge_tool_definitions(
    base_tools: List[Dict[str, Any]],
    compat_module: Any,
    external_proxy: Any,
) -> List[Dict[str, Any]]:
    tools = list(base_tools)
    if compat_module is not None:
        try:
            tools.extend(compat_module.get_tool_definitions())
        except Exception:
            pass
    if external_proxy is not None:
        try:
            existing = set(str(t.get("name")) for t in tools if t.get("name"))
            ext_tools = external_proxy.list_tools()
            for t in ext_tools:
                n = str(t.get("name", ""))
                if n and n not in existing:
                    tools.append(t)
        except Exception:
            pass
    return tools

