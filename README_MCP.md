# RenderDoc MCP (local)

这个目录包含一个可用的 MCP `stdio` 服务：

- 服务脚本：`mcp/renderdoc_mcp_server.py`
- 启动脚本：`start_renderdoc_mcp.bat`
- 工具名：`capture_game`

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

## 提示

- 自动 F12 依赖窗口前台切换，反作弊/内核保护游戏可能仍失败。
- MuMu 常见可抓，是因为目标通常是模拟器渲染进程而非受保护游戏本体。
