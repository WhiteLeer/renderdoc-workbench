# RenderDoc MCP (local)

这个目录包含一个可用的 MCP `stdio` 服务：

- 服务脚本：`mcp/renderdoc_mcp_server.py`
- 启动脚本：`start_renderdoc_mcp.bat`
- 工具名：`capture_game`
- 工具名：`analyze_rdc`
- 工具名：`focus_rdc_event`
- 工具名：`analyze_event`
- 工具名：`get_event_state`
- 工具名：`compare_events`
- 工具名：`open_capture`
- 工具名：`close_capture`
- 工具名：`get_capture_info`
- 工具名：`get_frame_overview`
- 工具名：`get_pass_timing`
- 工具名：`get_draw_call_state`
- 工具名：`diff_draw_calls`
- 工具名：`save_texture`
- 工具名：`save_render_target`
- 工具名：`export_draw_textures`
- 工具名：`export_mesh`

另外已接入“外部工具代理层”：当本地不存在某个工具时，会自动转发到外部 `renderdoc-mcp`（Linkingooo/PyPI 工具集）执行。

## 模式

`capture_game` 支持两种模式：

1. `launch`：用 RenderDoc 启动 exe 再抓帧
2. `attach`：注入到已经运行的进程再抓帧

## 关键参数

- `capture_mode`：`launch` 或 `attach`，默认 `launch`
- `game_path`：`launch` 必填
- `target_pid`：`attach` 可填
- `target_process_name`：`attach` 可填（如 `MuMuPlayer.exe`）
- `hook_children`：默认 `true`，适合“启动器拉起子进程”的游戏
- `disallow_fullscreen`：默认 `true`，减少独占全屏导致的抓取失败
- `trigger_delay_sec`：默认 `5`，建议本体游戏设 `8-15`
- `delay_for_debugger_sec`：RenderDoc 注入后额外等待秒数
- `trigger_backend`：`auto` / `qrenderdoc` / `targetcontrol` / `hotkey`
  - `auto`：MuMu 默认走 `qrenderdoc` Python 触发（稳定拿到 `NewCapture`）
  - `qrenderdoc`：显式使用 qrenderdoc Python API 触发并复制 capture
  - `targetcontrol`：直接走低层 TargetControl 触发
  - `hotkey`：窗口置前后发送 F12（需 `allow_focus_hotkey=true`）
- `qrenderdoc_trigger_count`：qrenderdoc 模式触发次数，默认 `8`
- `qrenderdoc_poll_timeout_sec`：qrenderdoc 模式轮询 `NewCapture` 的超时秒数，默认 `45`

## MCP 配置示例

关键点：不要把 `cwd` 设为 `RenderDoc-mcp` 根目录，否则会被目录内 `python36.dll` 干扰。

```json
{
  "mcpServers": {
    "renderdoc-mcp": {
      "command": "python",
      "args": [
        "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\mcp\\renderdoc_mcp_server.py"
      ],
      "cwd": "C:\\Users\\wepie"
    }
  }
}
```

## 示例调用

`launch`（直接拉起本体）：

```json
{
  "capture_mode": "launch",
  "game_path": "D:\\Games\\AAA\\Game.exe",
  "hook_children": true,
  "disallow_fullscreen": true,
  "trigger_delay_sec": 10,
  "open_in_qrenderdoc": true
}
```

`attach`（已运行进程）：

```json
{
  "capture_mode": "attach",
  "target_process_name": "Game.exe",
  "trigger_delay_sec": 8,
  "open_in_qrenderdoc": true
}
```

`analyze_rdc`（分析已有 rdc）：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "top_n": 12,
  "save_json": true,
  "save_root_dir": "C:\\Users\\wepie\\Desktop\\RENDERDOC-MCP-SAVE",
  "open_report": true
}
```

返回内容包含：
- 流程统计：事件数、Draw 数、RenderPass 数、Present 事件
- 热点：按 GPU Duration 排序的 Top DrawCall
- 贴图/资源：纹理使用统计（可用时）与热点事件绑定资源
- 贴图定位优化：`texture_lookup`（resourceId -> 热点 eventId / sampledBy / outputBy）
- 管线追踪：热点事件的 VS/PS、采样输入、输出目标
- 可视化：生成 `report.html` 并可自动打开
- 落盘结构：默认 `桌面\\RENDERDOC-MCP-SAVE\\<RDC文件名>\\`
  - `analysis.full.json`
  - `flow.json`
  - `hotspots.json`
  - `textures.json`
  - `pipeline_trace.json`
  - `resource_map.json`
  - `texture_lookup.json`
  - `algorithms.json`
  - `errors.json`
  - `report.html`

`focus_rdc_event`（打开/切换到 rdc 并跳到事件）：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "event_id": 1246,
  "show_event_browser": true,
  "keep_qrenderdoc_open": true,
  "persist_context": true,
  "context_top_n": 24,
  "save_root_dir": "C:\\Users\\wepie\\Desktop\\RENDERDOC-MCP-SAVE"
}
```

或自动定位热点第 N 名并跳转：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "hotspot_rank": 1,
  "hotspot_top_n": 12,
  "show_event_browser": true,
  "persist_context": true
}
```

当 `persist_context=true` 时，会额外保存：
- `focus_event_<eventId>.json`：该事件的热点排名、管线信息、相关贴图、优化建议
- `focus_event_<eventId>.md`：可读版摘要，便于你后续直接问“为什么热点/怎么优化”

`analyze_event`（针对单个事件自动导出贴图与上下文并给出分析）：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "event_id": 429,
  "export_images": true,
  "save_root_dir": "C:\\Users\\wepie\\Desktop\\RENDERDOC-MCP-SAVE"
}
```

输出目录：`桌面\\RENDERDOC-MCP-SAVE\\<RDC名>\\event_<eventId>\\`
- `event_analysis.json`：完整结构化分析
- `event_analysis.md`：可读总结（原因+优化建议）
- `images\\`：该事件输入/输出资源导出图（PNG，能导出的会自动保存）

说明（稳定性模式）：
- 为避免 qrenderdoc 崩溃弹窗循环，`analyze_event` 当前优先走稳定分析链路（会返回资源映射与优化建议）。
- `images\\` 目录会创建，但贴图导出暂时禁用并在结果里标注 `stability mode`。

`get_event_state`（单事件一键状态总览）：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "event_id": 429
}
```

返回：热点排名、GPU时长、VS/PS、采样输入资源、输出目标、关联资源映射。

`compare_events`（两个事件差异分析）：

```json
{
  "rdc_path": "C:\\Users\\wepie\\Desktop\\RenderDoc-mcp\\captures\\sample.rdc",
  "event_a": 429,
  "event_b": 568
}
```

返回：shader 是否一致、输入/输出资源差异、GPU 时长差值（`gpuDuration_delta_us`）。

## 外部兼容工具（已模块化）

为兼容 `renderdoc-mcp`（PyPI / Linkingooo）常见工作流，新增一组 session 工具：

1. 先 `open_capture({"filepath":"...rdc"})`
2. 再调用 `get_capture_info` / `get_frame_overview` / `get_pass_timing`
3. 事件级调用：`get_draw_call_state` / `diff_draw_calls`
4. 导出类调用：`save_texture` / `save_render_target` / `export_draw_textures` / `export_mesh`
5. 结束时 `close_capture`

说明：
- 这组兼容工具由新模块 `mcp/compat_tools.py` 承载。
- 导出类工具内部走 `qrenderdoc --python`，不依赖你当前 Python 环境直接 import `renderdoc`。
- 外部全量工具代理模块：`mcp/external_tools_proxy.py`。

当前服务结构（重构中，已落地第一阶段）：
- `mcp/renderdoc_mcp_server.py`：本地核心业务工具实现（capture/analyze/focus）
- `mcp/analysis_entry_tools.py`：analyze/focus/event/compare 入口处理器（已拆分）
- `mcp/capture_entry_tools.py`：capture_game 入口处理器（已拆分）
- `mcp/compat_tools.py`：兼容工具层（open_capture/get_pass_timing 等）
- `mcp/external_tools_proxy.py`：外部 MCP 工具代理（自动并入 tools/list）
- `mcp/integration.py`：可选模块加载与工具定义合并
- `mcp/server_runtime.py`：JSON-RPC stdio 协议主循环

## 提示

- 自动 F12 依赖窗口前台切换，反作弊/内核保护游戏可能仍失败。
- MuMu 常见可抓，是因为目标通常是模拟器渲染进程而非受保护游戏本体。
