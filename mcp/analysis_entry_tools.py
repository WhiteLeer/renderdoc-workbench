#!/usr/bin/env python3
from pathlib import Path
from typing import Any, Dict, Optional


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


def build_handlers(
    *,
    analyze_rdc_with_qrenderdoc,
    focus_rdc_event_with_qrenderdoc,
    analyze_event_with_qrenderdoc,
    default_analysis_save_root,
    resolve_renderdoc_paths,
    build_focus_event_context,
    write_focus_context_files,
) -> Dict[str, Any]:
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
            else default_analysis_save_root()
        )
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        return analyze_rdc_with_qrenderdoc(
            qrenderdoc=qrenderdoc,
            rdc_path=rdc_path,
            top_n=top_n,
            save_json=save_json,
            save_root_dir=save_root_dir,
            open_report=open_report,
        )

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
            else default_analysis_save_root()
        )
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))

        selected_event_id: Optional[int] = int(event_id_arg) if event_id_arg is not None else None
        auto_pick: Dict[str, Any] = {}
        context_analysis: Optional[Dict[str, Any]] = None
        if selected_event_id is None:
            analysis = analyze_rdc_with_qrenderdoc(
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
                raise ValueError(f"hotspot_rank {hotspot_rank} out of range, available hotspot count: {len(rows)}")
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

        focused = focus_rdc_event_with_qrenderdoc(
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
                context_analysis = analyze_rdc_with_qrenderdoc(
                    qrenderdoc=qrenderdoc,
                    rdc_path=rdc_path,
                    top_n=context_top_n,
                    save_json=True,
                    save_root_dir=save_root_dir,
                    open_report=False,
                )
            focus_ctx = build_focus_event_context(context_analysis, selected_event_id)
            analysis_dir = context_analysis.get("analysis_dir")
            if analysis_dir:
                files = write_focus_context_files(Path(str(analysis_dir)), focus_ctx, rdc_path=rdc_path)
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
            else default_analysis_save_root()
        )
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        return analyze_event_with_qrenderdoc(
            qrenderdoc=qrenderdoc,
            rdc_path=rdc_path,
            event_id=event_id,
            save_root_dir=save_root_dir,
            export_images=export_images,
        )

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
            else default_analysis_save_root()
        )
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        analysis = analyze_rdc_with_qrenderdoc(
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
        return {"rdc_path": str(rdc_path), "event_state": state, "analysis_dir": analysis.get("analysis_dir")}

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
            else default_analysis_save_root()
        )
        _renderdoccmd, qrenderdoc = resolve_renderdoc_paths(args.get("renderdoc_dir"))
        analysis = analyze_rdc_with_qrenderdoc(
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

    return {
        "analyze_rdc": _analyze_rdc,
        "focus_rdc_event": _focus_rdc_event,
        "analyze_event": _analyze_event,
        "get_event_state": _get_event_state,
        "compare_events": _compare_events,
    }

