#!/usr/bin/env python3
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


_SESSION: Dict[str, Any] = {"rdc_path": None}


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "open_capture",
            "description": "Open a RenderDoc capture (.rdc) file for session-based analysis.",
            "inputSchema": {
                "type": "object",
                "properties": {"filepath": {"type": "string", "description": "Absolute path to .rdc file."}},
                "required": ["filepath"],
                "additionalProperties": False,
            },
        },
        {
            "name": "close_capture",
            "description": "Close current opened capture in MCP session context.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_capture_info",
            "description": "Get capture info of current opened .rdc.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_frame_overview",
            "description": "Get frame overview of current opened .rdc.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_pass_timing",
            "description": "Get pass/draw-call timing overview of current opened .rdc.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "granularity": {"type": "string", "enum": ["pass", "draw_call"], "default": "pass"},
                    "top_n": {"type": "integer", "default": 20},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "get_draw_call_state",
            "description": "Get one-shot draw-call state summary.",
            "inputSchema": {
                "type": "object",
                "properties": {"event_id": {"type": "integer"}},
                "required": ["event_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "diff_draw_calls",
            "description": "Diff two draw calls and return state differences.",
            "inputSchema": {
                "type": "object",
                "properties": {"eid1": {"type": "integer"}, "eid2": {"type": "integer"}},
                "required": ["eid1", "eid2"],
                "additionalProperties": False,
            },
        },
        {
            "name": "save_texture",
            "description": "Save one texture resource to file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "resource_id": {"type": "string"},
                    "output_path": {"type": "string"},
                    "event_id": {"type": "integer"},
                    "mip": {"type": "integer", "default": 0},
                    "file_type": {"type": "string", "enum": ["png", "jpg", "bmp", "tga", "hdr", "exr", "dds"], "default": "png"},
                    "renderdoc_dir": {"type": "string"},
                },
                "required": ["resource_id", "output_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "save_render_target",
            "description": "Save current color target (and optional depth) at event.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "output_path": {"type": "string"},
                    "save_depth": {"type": "boolean", "default": False},
                    "renderdoc_dir": {"type": "string"},
                },
                "required": ["event_id", "output_path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "export_draw_textures",
            "description": "Export pixel-shader textures of one draw-call.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "output_dir": {"type": "string"},
                    "skip_small": {"type": "boolean", "default": True},
                    "renderdoc_dir": {"type": "string"},
                },
                "required": ["event_id", "output_dir"],
                "additionalProperties": False,
            },
        },
        {
            "name": "export_mesh",
            "description": "Export post-VS mesh as OBJ for one draw-call.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event_id": {"type": "integer"},
                    "output_path": {"type": "string"},
                    "renderdoc_dir": {"type": "string"},
                },
                "required": ["event_id", "output_path"],
                "additionalProperties": False,
            },
        },
    ]


def _require_open_capture() -> Path:
    p = _SESSION.get("rdc_path")
    if not p:
        raise ValueError("No capture file is open. Use open_capture first.")
    return Path(str(p))


def _extract_diffs(a: Any, b: Any, prefix: str = "") -> List[Dict[str, Any]]:
    if a == b:
        return []
    if isinstance(a, dict) and isinstance(b, dict):
        out: List[Dict[str, Any]] = []
        keys = sorted(set(a.keys()) | set(b.keys()))
        for k in keys:
            key_prefix = f"{prefix}.{k}" if prefix else str(k)
            out.extend(_extract_diffs(a.get(k), b.get(k), key_prefix))
        return out
    return [{"field": prefix or "value", "eid1": a, "eid2": b}]


def _qrenderdoc_python_exec(
    qrenderdoc: Path,
    action: str,
    payload: Dict[str, Any],
    timeout_sec: float = 75.0,
) -> Dict[str, Any]:
    run_dir = Path(tempfile.mkdtemp(prefix="renderdoc_mcp_compat_"))
    script_path = run_dir / "exec.py"
    result_path = run_dir / "result.json"
    log_path = run_dir / "exec.log"
    cfg = {
        "action": action,
        "payload": payload,
        "result_path": str(result_path),
        "log_path": str(log_path),
    }
    script = f"""import json
import os
import struct
import traceback

cfg = {json.dumps(cfg, ensure_ascii=True)}
action = cfg["action"]
payload = cfg["payload"]
result_path = cfg["result_path"]
log_path = cfg["log_path"]

def _log(msg):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\\n")
    except Exception:
        pass

def _resolve_resource(controller, rid_str):
    for t in controller.GetTextures():
        if str(t.resourceId) == rid_str:
            return ("texture", t.resourceId, t)
    for b in controller.GetBuffers():
        if str(b.resourceId) == rid_str:
            return ("buffer", b.resourceId, b)
    return (None, None, None)

def _save_tex(controller, resource_id, out_path, file_type, mip):
    import renderdoc as rd
    fmap = {{
        "png": rd.FileType.PNG, "jpg": rd.FileType.JPG, "bmp": rd.FileType.BMP,
        "tga": rd.FileType.TGA, "hdr": rd.FileType.HDR, "exr": rd.FileType.EXR, "dds": rd.FileType.DDS
    }}
    texsave = rd.TextureSave()
    texsave.resourceId = resource_id
    texsave.destType = fmap.get(file_type, rd.FileType.PNG)
    texsave.mip = int(mip)
    texsave.slice.sliceIndex = 0
    texsave.alpha = rd.AlphaMapping.Preserve
    parent = os.path.dirname(out_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    controller.SaveTexture(texsave, out_path)

result = {{"ok": False, "error": None, "data": {{}}}}
try:
    import renderdoc as rd
    cap = rd.OpenCaptureFile()
    r = cap.OpenFile(payload["rdc_path"], "", None)
    if r != rd.ResultCode.Succeeded:
        raise RuntimeError("OpenFile failed: %s" % str(r))
    if not cap.LocalReplaySupport():
        raise RuntimeError("Capture cannot be replayed on this machine")
    r, controller = cap.OpenCapture(rd.ReplayOptions(), None)
    if r != rd.ResultCode.Succeeded:
        raise RuntimeError("OpenCapture failed: %s" % str(r))

    if action in ("save_texture", "save_render_target", "export_draw_textures", "export_mesh", "get_draw_call_state"):
        eid = payload.get("event_id")
        if eid is not None:
            controller.SetFrameEvent(int(eid), True)

    def _build_action_map(actions, out_map):
        for a in actions:
            out_map[int(a.eventId)] = a
            if len(a.children) > 0:
                _build_action_map(a.children, out_map)

    if action == "get_draw_call_state":
        event_id = int(payload["event_id"])
        action_map = {{}}
        _build_action_map(controller.GetRootActions(), action_map)
        a = action_map.get(event_id)
        if a is None:
            raise RuntimeError("event not found: %d" % event_id)
        state = controller.GetPipelineState()
        out = {{
            "event_id": event_id,
            "action_name": a.GetName(controller.GetStructuredFile()),
            "vertex_count": int(getattr(a, "numIndices", 0)),
            "instance_count": int(getattr(a, "numInstances", 0)),
        }}
        try:
            out["topology"] = str(state.GetPrimitiveTopology())
        except Exception:
            out["topology"] = None
        try:
            ds = state.GetDepthState()
            out["depth"] = {{"test": bool(ds.depthEnable), "write": bool(ds.depthWrites), "func": str(ds.depthFunction)}}
        except Exception:
            out["depth"] = None
        try:
            ss = state.GetStencilState()
            out["stencil"] = {{"enabled": bool(ss.stencilEnable)}}
        except Exception:
            out["stencil"] = None
        try:
            rs = state.GetRasterizer()
            out["rasterizer"] = {{"cull": str(rs.cullMode), "fill": str(rs.fillMode), "front_ccw": bool(rs.frontCCW)}}
        except Exception:
            out["rasterizer"] = None
        try:
            cb = state.GetColorBlend()
            if cb.blends:
                b = cb.blends[0]
                out["blend"] = {{
                    "enabled": bool(b.enabled),
                    "color_src": str(b.colorBlend.source),
                    "color_dst": str(b.colorBlend.destination),
                    "color_op": str(b.colorBlend.operation),
                    "alpha_src": str(b.alphaBlend.source),
                    "alpha_dst": str(b.alphaBlend.destination),
                    "alpha_op": str(b.alphaBlend.operation),
                }}
            else:
                out["blend"] = None
        except Exception:
            out["blend"] = None
        try:
            outputs = []
            for o in state.GetOutputTargets():
                if int(o.resource) != 0:
                    outputs.append({{"resourceId": str(o.resource)}})
            out["render_targets"] = outputs
        except Exception:
            out["render_targets"] = []
        try:
            ps = state.GetShaderReflection(rd.ShaderStage.Pixel)
            textures = []
            if ps is not None:
                ro_by_index = {{}}
                for b in state.GetReadOnlyResources(rd.ShaderStage.Pixel):
                    ro_by_index.setdefault(int(b.access.index), []).append(b)
                for i, ro in enumerate(ps.readOnlyResources):
                    for b in ro_by_index.get(i, []):
                        textures.append({{"slot": i, "name": ro.name, "resourceId": str(b.descriptor.resource)}})
            out["textures"] = textures
        except Exception:
            out["textures"] = []
        shaders = {{}}
        try:
            vs = state.GetShaderReflection(rd.ShaderStage.Vertex)
            if vs is not None:
                shaders["vs"] = {{"id": str(vs.resourceId), "entry": state.GetShaderEntryPoint(rd.ShaderStage.Vertex)}}
        except Exception:
            pass
        try:
            ps = state.GetShaderReflection(rd.ShaderStage.Pixel)
            if ps is not None:
                shaders["ps"] = {{"id": str(ps.resourceId), "entry": state.GetShaderEntryPoint(rd.ShaderStage.Pixel)}}
        except Exception:
            pass
        out["shaders"] = shaders
        result["data"] = out

    elif action == "save_texture":
        kind, rid, desc = _resolve_resource(controller, str(payload["resource_id"]))
        if rid is None or kind != "texture":
            raise RuntimeError("resource_id not found or not texture")
        out_path = payload["output_path"]
        _save_tex(controller, rid, out_path, payload.get("file_type", "png"), payload.get("mip", 0))
        result["data"] = {{"status": "saved", "resource_id": str(payload["resource_id"]), "output_path": out_path}}

    elif action == "save_render_target":
        state = controller.GetPipelineState()
        outputs = state.GetOutputTargets()
        color = None
        for o in outputs:
            if int(o.resource) != 0:
                color = o
                break
        if color is None:
            raise RuntimeError("No color render target bound")
        output_path = payload["output_path"]
        if os.path.isdir(output_path):
            color_path = os.path.join(output_path, "rt_color_eid%d.png" % int(payload["event_id"]))
        else:
            color_path = output_path
        _save_tex(controller, color.resource, color_path, "png", 0)
        saved = [{{"type": "color", "resource_id": str(color.resource), "output_path": color_path}}]
        if bool(payload.get("save_depth", False)):
            depth = state.GetDepthTarget()
            if int(depth.resource) != 0:
                if os.path.isdir(output_path):
                    depth_path = os.path.join(output_path, "rt_depth_eid%d.png" % int(payload["event_id"]))
                else:
                    base, ext = os.path.splitext(color_path)
                    depth_path = base + "_depth" + ext
                _save_tex(controller, depth.resource, depth_path, "png", 0)
                saved.append({{"type": "depth", "resource_id": str(depth.resource), "output_path": depth_path}})
        result["data"] = {{"saved": saved, "saved_count": len(saved)}}

    elif action == "export_draw_textures":
        out_dir = payload["output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        skip_small = bool(payload.get("skip_small", True))
        state = controller.GetPipelineState()
        ps = state.GetShaderReflection(rd.ShaderStage.Pixel)
        if ps is None:
            raise RuntimeError("No pixel shader bound at this event")
        ro_by_index = {{}}
        for b in state.GetReadOnlyResources(rd.ShaderStage.Pixel):
            ro_by_index.setdefault(int(b.access.index), []).append(b)
        exported = []
        skipped = []
        for i, ro_refl in enumerate(ps.readOnlyResources):
            for b in ro_by_index.get(i, []):
                rid = b.descriptor.resource
                rid_str = str(rid)
                tex_desc = None
                for t in controller.GetTextures():
                    if str(t.resourceId) == rid_str:
                        tex_desc = t
                        break
                if tex_desc is None:
                    continue
                if skip_small and tex_desc.width <= 4 and tex_desc.height <= 4:
                    skipped.append({{"name": ro_refl.name, "resource_id": rid_str}})
                    continue
                fname = ("%s_%dx%d.png" % (ro_refl.name, tex_desc.width, tex_desc.height)).replace("/", "_").replace("\\\\", "_")
                out_path = os.path.join(out_dir, fname)
                _save_tex(controller, rid, out_path, "png", 0)
                exported.append({{"name": ro_refl.name, "resource_id": rid_str, "output_path": out_path}})
        result["data"] = {{"exported": exported, "exported_count": len(exported), "skipped": skipped, "skipped_count": len(skipped)}}

    elif action == "export_mesh":
        event_id = int(payload["event_id"])
        output_path = payload["output_path"]
        postvs = controller.GetPostVSData(0, 0, rd.MeshDataStage.VSOut)
        if postvs.vertexResourceId == rd.ResourceId.Null():
            raise RuntimeError("No post-VS data available")
        data = controller.GetBufferData(postvs.vertexResourceId, postvs.vertexByteOffset, postvs.numIndices * postvs.vertexByteStride)
        floats_per_vertex = int(postvs.vertexByteStride // 4)
        if floats_per_vertex <= 0:
            raise RuntimeError("Invalid vertex stride")
        positions = []
        num_verts = int(postvs.numIndices)
        for i in range(num_verts):
            off = i * int(postvs.vertexByteStride)
            if off + int(postvs.vertexByteStride) > len(data):
                break
            vals = struct.unpack_from("%df" % floats_per_vertex, data, off)
            x = vals[0] if len(vals) > 0 else 0.0
            y = vals[1] if len(vals) > 1 else 0.0
            z = vals[2] if len(vals) > 2 else 0.0
            positions.append((x, y, z))
        lines = ["# Exported from RenderDoc MCP compat - event %d" % event_id]
        for p in positions:
            lines.append("v %.6f %.6f %.6f" % (p[0], p[1], p[2]))
        tri_count = 0
        for i in range(0, len(positions) - 2, 3):
            i1, i2, i3 = i + 1, i + 2, i + 3
            lines.append("f %d %d %d" % (i1, i2, i3))
            tri_count += 1
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\\n".join(lines) + "\\n")
        result["data"] = {{"event_id": event_id, "output_path": output_path, "vertices": len(positions), "triangles": tri_count}}

    else:
        raise RuntimeError("Unknown action: %s" % action)

    controller.Shutdown()
    cap.Shutdown()
    result["ok"] = True
except Exception:
    result["error"] = traceback.format_exc()
try:
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
except Exception:
    pass
"""
    script_path.write_text(script, encoding="utf-8")
    proc = subprocess.Popen(
        [str(qrenderdoc), "--python", str(script_path)],
        cwd=str(qrenderdoc.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.time() + max(float(timeout_sec), 10.0)
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
    stdout = (proc.stdout.read() if proc.stdout else "").strip()
    stderr = (proc.stderr.read() if proc.stderr else "").strip()
    if not result_path.exists():
        raise RuntimeError(f"qrenderdoc python did not produce result for {action}; stdout={stdout} stderr={stderr}")
    payload_out = json.loads(result_path.read_text(encoding="utf-8"))
    payload_out["stdout"] = stdout
    payload_out["stderr"] = stderr
    payload_out["log_path"] = str(log_path)
    return payload_out


def build_handlers(
    *,
    analyze_rdc: Callable[[Dict[str, Any]], Dict[str, Any]],
    get_event_state: Callable[[Dict[str, Any]], Dict[str, Any]],
    compare_events: Callable[[Dict[str, Any]], Dict[str, Any]],
    resolve_renderdoc_paths: Callable[[Optional[str]], Any],
) -> Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]:
    def _open_capture(args: Dict[str, Any]) -> Dict[str, Any]:
        p = Path(str(args.get("filepath", ""))).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"File not found: {p}")
        if p.suffix.lower() != ".rdc":
            raise ValueError(f"Not an .rdc file: {p}")
        _SESSION["rdc_path"] = str(p)
        return {"status": "opened", "filepath": str(p)}

    def _close_capture(_args: Dict[str, Any]) -> Dict[str, Any]:
        prev = _SESSION.get("rdc_path")
        _SESSION["rdc_path"] = None
        return {"status": "closed", "filepath": prev}

    def _get_capture_info(_args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        analysis = analyze_rdc({"rdc_path": str(rdc_path), "top_n": 12, "save_json": False, "open_report": False})
        summary = analysis.get("summary", {}) if isinstance(analysis, dict) else {}
        return {
            "filepath": str(rdc_path),
            "api": summary.get("api"),
            "total_actions": summary.get("eventsTotal"),
            "draw_calls": summary.get("drawCalls"),
            "textures": summary.get("resourcesTotal"),
            "current_event": None,
        }

    def _get_frame_overview(_args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        analysis = analyze_rdc({"rdc_path": str(rdc_path), "top_n": 24, "save_json": False, "open_report": False})
        summary = analysis.get("summary", {}) if isinstance(analysis, dict) else {}
        pipeline_trace = analysis.get("pipeline_trace", []) if isinstance(analysis, dict) else []
        rts: Dict[str, Dict[str, Any]] = {}
        for row in pipeline_trace:
            for rt in row.get("outputTargets", []) or []:
                rid = str(rt.get("resourceId", ""))
                if not rid:
                    continue
                if rid not in rts:
                    rts[rid] = {
                        "resource_id": rid,
                        "draw_count": 0,
                        "size": f'{rt.get("w", 0)}x{rt.get("h", 0)}',
                        "format": rt.get("format", ""),
                    }
                rts[rid]["draw_count"] += 1
        render_targets = sorted(rts.values(), key=lambda x: int(x.get("draw_count", 0)), reverse=True)
        return {
            "filepath": str(rdc_path),
            "api": summary.get("api"),
            "total_actions": summary.get("eventsTotal"),
            "draw_calls": summary.get("drawCalls"),
            "dispatches": summary.get("dispatchCalls"),
            "render_targets": render_targets[:20],
        }

    def _get_pass_timing(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        granularity = str(args.get("granularity", "pass")).lower()
        top_n = max(int(args.get("top_n", 20)), 1)
        analysis = analyze_rdc({"rdc_path": str(rdc_path), "top_n": max(top_n, 40), "save_json": False, "open_report": False})
        hotspots = analysis.get("hotspots", {}).get("topByGpuDuration", []) if isinstance(analysis, dict) else []
        if granularity == "draw_call":
            rows = []
            for row in hotspots[:top_n]:
                rows.append(
                    {
                        "event_id": row.get("eventId"),
                        "name": row.get("name"),
                        "estimated_cost": row.get("gpuDuration_us"),
                        "timing_unit": "us",
                    }
                )
            return {"granularity": "draw_call", "entries": rows, "total_draw_calls": len(hotspots)}

        pipeline_trace = analysis.get("pipeline_trace", []) if isinstance(analysis, dict) else []
        pass_cost: Dict[str, Dict[str, Any]] = {}
        hotspot_map = {int(x.get("eventId", -1)): float(x.get("gpuDuration_us", 0.0) or 0.0) for x in hotspots}
        for row in pipeline_trace:
            outputs = row.get("outputTargets", []) or []
            if not outputs:
                pass_key = "no_target"
            else:
                pass_key = "|".join(sorted(str(x.get("resourceId", "")) for x in outputs if x.get("resourceId")))
            if pass_key not in pass_cost:
                pass_cost[pass_key] = {"pass_key": pass_key, "draw_count": 0, "estimated_cost": 0.0, "render_targets": outputs}
            pass_cost[pass_key]["draw_count"] += 1
            eid = int(row.get("eventId", -1))
            pass_cost[pass_key]["estimated_cost"] += hotspot_map.get(eid, 0.0)
        passes = sorted(pass_cost.values(), key=lambda x: float(x.get("estimated_cost", 0.0)), reverse=True)
        return {"granularity": "pass", "passes": passes[:top_n], "total_passes": len(passes), "timing_unit": "us"}

    def _get_draw_call_state(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        event_id = int(args.get("event_id"))
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        try:
            out = _qrenderdoc_python_exec(
                qrenderdoc=qrenderdoc,
                action="get_draw_call_state",
                payload={"rdc_path": str(rdc_path), "event_id": event_id},
            )
            if out.get("ok"):
                return out.get("data", {})
        except Exception:
            pass

        # Stable fallback: pull from analyze_rdc pipeline window.
        analysis = analyze_rdc({"rdc_path": str(rdc_path), "top_n": 160, "save_json": False, "open_report": False})
        hotspot = {}
        trace = {}
        for row in (analysis.get("hotspots", {}).get("topByGpuDuration", []) or []):
            try:
                if int(row.get("eventId", -1)) == event_id:
                    hotspot = row
                    break
            except Exception:
                continue
        for row in (analysis.get("pipeline_trace", []) or []):
            try:
                if int(row.get("eventId", -1)) == event_id:
                    trace = row
                    break
            except Exception:
                continue
        if not hotspot and not trace:
            raise ValueError(f"event_id {event_id} not found")
        return {
            "event_id": event_id,
            "action_name": hotspot.get("name") or trace.get("name"),
            "estimated_gpu_us": hotspot.get("gpuDuration_us"),
            "topology": trace.get("topology"),
            "blend": trace.get("blend"),
            "depth": trace.get("depth"),
            "stencil": trace.get("stencil"),
            "rasterizer": trace.get("rasterizer"),
            "textures": trace.get("psSampledResources", []),
            "render_targets": trace.get("outputTargets", []),
            "shaders": {"vs": trace.get("vs"), "ps": trace.get("ps")},
        }

    def _diff_draw_calls(args: Dict[str, Any]) -> Dict[str, Any]:
        eid1 = int(args.get("eid1"))
        eid2 = int(args.get("eid2"))
        t1 = _get_draw_call_state({"event_id": eid1, "renderdoc_dir": args.get("renderdoc_dir")})
        t2 = _get_draw_call_state({"event_id": eid2, "renderdoc_dir": args.get("renderdoc_dir")})
        diffs = _extract_diffs(t1, t2)
        return {
            "eid1": eid1,
            "eid2": eid2,
            "differences": diffs,
            "identical": len(diffs) == 0,
            "summary": f"发现 {len(diffs)} 处差异" if diffs else "两个 draw call 的 pipeline state 完全相同",
        }

    def _save_texture(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        out = _qrenderdoc_python_exec(
            qrenderdoc=qrenderdoc,
            action="save_texture",
            payload={
                "rdc_path": str(rdc_path),
                "resource_id": str(args.get("resource_id")),
                "output_path": str(Path(str(args.get("output_path"))).expanduser().resolve()),
                "event_id": args.get("event_id"),
                "mip": int(args.get("mip", 0)),
                "file_type": str(args.get("file_type", "png")).lower(),
            },
        )
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "save_texture failed")
        return out.get("data", {})

    def _save_render_target(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        out = _qrenderdoc_python_exec(
            qrenderdoc=qrenderdoc,
            action="save_render_target",
            payload={
                "rdc_path": str(rdc_path),
                "event_id": int(args.get("event_id")),
                "output_path": str(Path(str(args.get("output_path"))).expanduser().resolve()),
                "save_depth": bool(args.get("save_depth", False)),
            },
        )
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "save_render_target failed")
        return out.get("data", {})

    def _export_draw_textures(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        out = _qrenderdoc_python_exec(
            qrenderdoc=qrenderdoc,
            action="export_draw_textures",
            payload={
                "rdc_path": str(rdc_path),
                "event_id": int(args.get("event_id")),
                "output_dir": str(Path(str(args.get("output_dir"))).expanduser().resolve()),
                "skip_small": bool(args.get("skip_small", True)),
            },
        )
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "export_draw_textures failed")
        return out.get("data", {})

    def _export_mesh(args: Dict[str, Any]) -> Dict[str, Any]:
        rdc_path = _require_open_capture()
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        out = _qrenderdoc_python_exec(
            qrenderdoc=qrenderdoc,
            action="export_mesh",
            payload={
                "rdc_path": str(rdc_path),
                "event_id": int(args.get("event_id")),
                "output_path": str(Path(str(args.get("output_path"))).expanduser().resolve()),
            },
        )
        if not out.get("ok"):
            raise RuntimeError(out.get("error") or "export_mesh failed")
        return out.get("data", {})

    return {
        "open_capture": _open_capture,
        "close_capture": _close_capture,
        "get_capture_info": _get_capture_info,
        "get_frame_overview": _get_frame_overview,
        "get_pass_timing": _get_pass_timing,
        "get_draw_call_state": _get_draw_call_state,
        "diff_draw_calls": _diff_draw_calls,
        "save_texture": _save_texture,
        "save_render_target": _save_render_target,
        "export_draw_textures": _export_draw_textures,
        "export_mesh": _export_mesh,
    }
