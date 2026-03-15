"""Small local web UI for asset-mode smoke testing."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

try:
    from scripts._webui_common import (
        LOG_FORMAT,
        InMemoryLogBufferHandler,
        LinkProcessController,
        autostart_serial_only,
        build_asset_config,
        default_config_path,
        install_log_capture,
        load_mode_config,
        setup_script_logging,
        validate_same_origin,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution path
    from _webui_common import (  # type: ignore[no-redef]
        LOG_FORMAT,
        InMemoryLogBufferHandler,
        LinkProcessController,
        autostart_serial_only,
        build_asset_config,
        default_config_path,
        install_log_capture,
        load_mode_config,
        setup_script_logging,
        validate_same_origin,
    )

LOGGER_NAME = "atlas_meshtastic_link.webui.asset"
log = logging.getLogger(LOGGER_NAME)

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ATLAS Asset Test Console</title>
  <style>
    :root {
      --bg: #f3f5fb;
      --panel: #ffffff;
      --ink: #1f2430;
      --subtle: #687083;
      --line: #d8ddeb;
      --accent: #1c3f72;
      --accent-soft: #d9e8ff;
      --warn: #7a2e0c;
      --warn-soft: #ffefd8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Gill Sans", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(70rem 42rem at -10% -15%, #dbe6f8 0%, transparent 55%),
        radial-gradient(55rem 28rem at 120% 0%, #f7e9d7 0%, transparent 55%),
        var(--bg);
      min-height: 100vh;
      padding: 1.2rem;
    }
    .layout {
      max-width: 1100px;
      margin: 0 auto;
      display: grid;
      gap: 1rem;
      grid-template-columns: repeat(12, minmax(0, 1fr));
    }
    .hero, .panel {
      grid-column: span 12;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 1rem;
      box-shadow: 0 12px 25px rgba(16, 24, 40, 0.07);
    }
    .hero h1 {
      margin: 0 0 .25rem 0;
      font-family: Georgia, "Times New Roman", serif;
      font-size: clamp(1.35rem, 2.8vw, 1.9rem);
      letter-spacing: .01em;
    }
    .hero p {
      margin: 0;
      color: var(--subtle);
    }
    .warning {
      margin-top: .7rem;
      padding: .6rem .75rem;
      border-radius: 10px;
      border: 1px solid #f3d4a5;
      background: var(--warn-soft);
      color: var(--warn);
      font-size: .9rem;
    }
    .grid {
      display: grid;
      gap: .75rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .field {
      display: grid;
      gap: .35rem;
    }
    .field label {
      font-size: .84rem;
      color: var(--subtle);
    }
    input, select, textarea {
      width: 100%;
      border-radius: 9px;
      border: 1px solid var(--line);
      padding: .55rem .65rem;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    textarea {
      min-height: 260px;
      font-family: "Consolas", "Lucida Console", monospace;
      font-size: .83rem;
      white-space: pre;
    }
    .inline {
      display: flex;
      align-items: center;
      gap: .55rem;
      min-height: 38px;
      font-size: .92rem;
    }
    .inline input[type="checkbox"] {
      width: 16px;
      height: 16px;
      margin: 0;
    }
    .actions {
      display: flex;
      gap: .6rem;
      margin-top: .2rem;
      flex-wrap: wrap;
    }
    button {
      border: 1px solid transparent;
      border-radius: 10px;
      padding: .55rem .8rem;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      transition: transform .14s ease, box-shadow .14s ease;
    }
    button:hover {
      transform: translateY(-1px);
      box-shadow: 0 8px 16px rgba(0, 0, 0, 0.09);
    }
    .btn-start { background: var(--accent); color: #fff; }
    .btn-stop { background: #fff; border-color: #c4342b; color: #c4342b; }
    .btn-neutral { background: #fff; border-color: var(--line); color: var(--ink); }
    .status {
      display: grid;
      gap: .4rem;
      font-size: .95rem;
      margin-top: .45rem;
    }
    .pill {
      display: inline-block;
      border-radius: 999px;
      background: var(--accent-soft);
      color: #123a70;
      font-size: .78rem;
      font-weight: 700;
      padding: .15rem .55rem;
      margin-bottom: .35rem;
    }
    .muted { color: var(--subtle); }
    .mono {
      font-family: "Consolas", "Lucida Console", monospace;
      font-size: .83rem;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fafb;
      padding: .65rem;
      white-space: pre-wrap;
      max-height: 290px;
      overflow: auto;
    }
    .toast {
      border: 1px solid #f1ccb7;
      background: #fff2ea;
      color: #8e3210;
      border-radius: 10px;
      min-height: 38px;
      padding: .55rem .7rem;
      font-size: .9rem;
    }
    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: .5rem;
      margin-bottom: .55rem;
    }
    .file-meta { font-size: .82rem; color: var(--subtle); }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="layout">
    <section class="hero">
      <span class="pill">Asset mode</span>
      <h1>ATLAS Asset Test Console</h1>
      <p>Local harness for asset-mode startup diagnostics and user-facing smoke tests.</p>
      <div class="warning">Asset runtime functionality is currently diagnostics-focused while core asset operations are still being implemented.</div>
    </section>

    <section class="panel">
      <form id="asset-form">
        <div class="grid">
          <div class="field">
            <label for="radio_mode">Radio mode</label>
            <select id="radio_mode" name="radio_mode">
              <option value="serial" selected>Real serial</option>
            </select>
          </div>
          <div class="field">
            <label for="radio_port">Serial port (optional)</label>
            <input id="radio_port" name="radio_port" placeholder="COM7 or /dev/ttyUSB0" />
          </div>
          <div class="field">
            <label for="entity_id">Entity ID (optional)</label>
            <input id="entity_id" name="entity_id" placeholder="asset-node-01" />
          </div>
          <div class="field">
            <label for="intent_path">Asset intent path</label>
            <input id="intent_path" name="intent_path" value="./asset_intent.json" />
          </div>
          <div class="field">
            <label for="world_state_path">World state path</label>
            <input id="world_state_path" name="world_state_path" value="./world_state.json" />
          </div>
          <div class="field">
            <label for="spool_path">Spool path (optional)</label>
            <input id="spool_path" name="spool_path" placeholder="./spool" />
          </div>
          <div class="field">
            <label for="log_level">Log level</label>
            <select id="log_level" name="log_level">
              <option value="DEBUG">DEBUG</option>
              <option value="INFO" selected>INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
            </select>
          </div>
          <div class="field">
            <label>Auto-discover serial device</label>
            <div class="inline">
              <input id="auto_discover" name="auto_discover" type="checkbox" checked />
              <span>Enabled</span>
            </div>
          </div>
          <div class="field">
            <label>Auto provision</label>
            <div class="inline">
              <input id="auto_provision" name="auto_provision" type="checkbox" checked />
              <span>Enabled</span>
            </div>
          </div>
        </div>
        <div class="actions">
          <button type="button" class="btn-start" id="start-btn">Start asset</button>
          <button type="button" class="btn-stop" id="stop-btn">Stop asset</button>
        </div>
      </form>
      <div class="toast" id="action-message"></div>
      <div class="status">
        <div><strong>State:</strong> <span id="status-state">unknown</span></div>
        <div><strong>Uptime:</strong> <span id="status-uptime" class="muted">n/a</span></div>
        <div><strong>Connected:</strong> <span id="status-channel-connected" class="muted">unknown</span></div>
        <div><strong>Channel:</strong> <span id="status-channel-url" class="muted">unknown</span></div>
        <div><strong>Last error:</strong> <span id="status-error" class="muted">none</span></div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>Asset Intent JSON (Editable)</h3>
        <div class="actions">
          <button type="button" class="btn-neutral" id="intent-reload-btn">Reload</button>
          <button type="button" class="btn-start" id="intent-save-btn">Save</button>
        </div>
      </div>
      <div class="file-meta" id="intent-path">Path: unknown</div>
      <textarea id="intent-editor" spellcheck="false"></textarea>
      <div class="toast" id="intent-message"></div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h3>World State JSON (Read Only)</h3>
        <div class="actions">
          <button type="button" class="btn-neutral" id="world-refresh-btn">Refresh</button>
        </div>
      </div>
      <div class="file-meta" id="world-path">Path: unknown</div>
      <div id="world-view" class="mono">No world state loaded yet.</div>
    </section>

    <section class="panel">
      <h3>Effective config</h3>
      <div id="config-view" class="mono">No config submitted yet.</div>
    </section>

    <section class="panel">
      <h3>Recent logs</h3>
      <div id="logs-view" class="mono">Waiting for logs...</div>
    </section>
  </main>

  <script>
    const form = document.getElementById("asset-form");
    const actionMessage = document.getElementById("action-message");
    const statusState = document.getElementById("status-state");
    const statusUptime = document.getElementById("status-uptime");
    const statusChannelConnected = document.getElementById("status-channel-connected");
    const statusChannelUrl = document.getElementById("status-channel-url");
    const statusError = document.getElementById("status-error");
    const configView = document.getElementById("config-view");
    const logsView = document.getElementById("logs-view");
    const intentEditor = document.getElementById("intent-editor");
    const intentMessage = document.getElementById("intent-message");
    const intentPath = document.getElementById("intent-path");
    const worldPath = document.getElementById("world-path");
    const worldView = document.getElementById("world-view");

    async function postWithForm(path) {
      const payload = new FormData(form);
      const response = await fetch(path, { method: "POST", body: payload });
      const body = await response.json();
      if (!response.ok) {
        throw new Error(body.message || "Request failed");
      }
      actionMessage.textContent = body.message || "Done.";
      await refreshAll();
    }

    async function refreshStatus() {
      const response = await fetch("/status");
      const data = await response.json();
      statusState.textContent = data.state;
      statusUptime.textContent = data.uptime_seconds === null ? "n/a" : `${data.uptime_seconds}s`;
      if (data.channel_connected === true) {
        statusChannelConnected.textContent = "yes";
      } else if (data.channel_connected === false) {
        statusChannelConnected.textContent = "no";
      } else {
        statusChannelConnected.textContent = "unknown";
      }
      statusChannelUrl.textContent = data.channel_url || "unknown";
      statusError.textContent = data.last_error || "none";
    }

    async function refreshLogs() {
      const response = await fetch("/logs?limit=250");
      const data = await response.json();
      const lines = data.lines || [];
      logsView.textContent = lines.length ? lines.join("\\n") : "No logs yet.";
    }

    async function refreshConfig() {
      const response = await fetch("/config/effective");
      const data = await response.json();
      configView.textContent = data.config ? JSON.stringify(data.config, null, 2) : "No config submitted yet.";
    }

    async function loadIntent() {
      const response = await fetch("/files/asset-intent");
      const data = await response.json();
      intentPath.textContent = `Path: ${data.path || "unknown"}`;
      if (!response.ok || data.ok === false) {
        intentMessage.textContent = data.message || "Failed to load asset intent.";
        if (typeof data.raw === "string") {
          intentEditor.value = data.raw;
        }
        return;
      }
      intentMessage.textContent = "Asset intent loaded.";
      intentEditor.value = data.content ? JSON.stringify(data.content, null, 2) : (data.raw || "{}");
    }

    async function saveIntent() {
      const response = await fetch("/files/asset-intent", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw: intentEditor.value }),
      });
      const data = await response.json();
      intentPath.textContent = `Path: ${data.path || "unknown"}`;
      if (!response.ok || data.ok === false) {
        intentMessage.textContent = data.message || "Failed to save asset intent.";
        return;
      }
      intentEditor.value = data.content ? JSON.stringify(data.content, null, 2) : intentEditor.value;
      intentMessage.textContent = data.message || "Saved.";
    }

    async function loadWorldState() {
      const response = await fetch("/files/world-state");
      const data = await response.json();
      worldPath.textContent = `Path: ${data.path || "unknown"}`;
      if (!response.ok || data.ok === false) {
        worldView.textContent = data.message || "Failed to load world state.";
        if (typeof data.raw === "string" && data.raw.length) {
          worldView.textContent = `${worldView.textContent}\\n\\n${data.raw}`;
        }
        return;
      }
      worldView.textContent = data.content ? JSON.stringify(data.content, null, 2) : (data.raw || "{}");
    }

    async function refreshAll() {
      try {
        await Promise.all([refreshStatus(), refreshLogs(), refreshConfig(), loadWorldState()]);
      } catch (error) {
        actionMessage.textContent = error.message;
      }
    }

    document.getElementById("start-btn").addEventListener("click", async () => {
      try { await postWithForm("/start"); } catch (error) { actionMessage.textContent = error.message; }
    });
    document.getElementById("stop-btn").addEventListener("click", async () => {
      try {
        const response = await fetch("/stop", { method: "POST" });
        const body = await response.json();
        if (!response.ok) throw new Error(body.message || "Stop failed");
        actionMessage.textContent = body.message || "Stopped.";
        await refreshAll();
      } catch (error) { actionMessage.textContent = error.message; }
    });
    document.getElementById("intent-reload-btn").addEventListener("click", async () => { await loadIntent(); });
    document.getElementById("intent-save-btn").addEventListener("click", async () => { await saveIntent(); });
    document.getElementById("world-refresh-btn").addEventListener("click", async () => { await loadWorldState(); });

    loadIntent();
    refreshAll();
    setInterval(refreshAll, 1500);
  </script>
</body>
</html>
"""


def _current_asset_paths(app: FastAPI) -> tuple[Path, Path]:
    effective = app.state.controller.effective_config()
    if isinstance(effective, dict):
        asset_cfg = effective.get("asset", {})
        intent_path = str(asset_cfg.get("intent_path") or "./asset_intent.json")
        world_state_path = str(asset_cfg.get("world_state_path") or "./world_state.json")
        return Path(intent_path), Path(world_state_path)
    startup_cfg = app.state.startup_config
    return Path(startup_cfg.asset.intent_path), Path(startup_cfg.asset.world_state_path)


def _read_json_file(path: Path) -> tuple[bool, dict[str, Any]]:
    if not path.exists():
        return False, {"ok": False, "path": str(path), "message": "File not found.", "raw": None, "content": None}
    raw = path.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return False, {
            "ok": False,
            "path": str(path),
            "message": f"Invalid JSON: {exc}",
            "raw": raw,
            "content": None,
        }
    if not isinstance(parsed, dict):
        return False, {
            "ok": False,
            "path": str(path),
            "message": "JSON root must be an object.",
            "raw": raw,
            "content": None,
        }
    return True, {"ok": True, "path": str(path), "message": None, "raw": raw, "content": parsed}


def _write_json_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def create_asset_app(*, auto_start: bool = True, config_path: str | Path | None = None) -> FastAPI:
    """Build FastAPI app for local asset UI."""
    setup_script_logging()
    handler = InMemoryLogBufferHandler(max_lines=1000)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    install_log_capture(handler, ["atlas_meshtastic_link", LOGGER_NAME])
    controller = LinkProcessController(mode="asset", logger=log)
    config_file = Path(config_path) if config_path is not None else default_config_path(__file__, "asset_webui.json")
    try:
        startup_config = load_mode_config(config_file, "asset")
        log.info("[WEBUI] loaded asset config from %s", config_file)
    except Exception as exc:
        log.warning("[WEBUI] failed to load asset config %s: %s", config_file, exc)
        startup_config = build_asset_config(
            {
                "radio_mode": "serial",
                "auto_discover": "on",
                "auto_provision": "on",
                "intent_path": "./asset_intent.json",
                "world_state_path": "./world_state.json",
                "log_level": "INFO",
            }
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        if auto_start:
            await autostart_serial_only(
                controller=controller,
                config=startup_config,
                logger=log,
                mode_name="asset",
            )
            await asyncio.sleep(0)
        yield
        controller.stop()

    app = FastAPI(title="ATLAS Asset Test Console", version="0.1.0", lifespan=lifespan)
    app.state.controller = controller
    app.state.log_handler = handler
    app.state.startup_config = startup_config
    app.state.config_path = str(config_file)

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(INDEX_HTML)

    @app.get("/favicon.ico")
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.post("/start")
    async def start_link(request: Request) -> JSONResponse:
        try:
            validate_same_origin(request)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=403)
        form = dict(await request.form())
        try:
            config = build_asset_config(form)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=400)

        ok, message = app.state.controller.start(config)
        if not ok:
            return JSONResponse({"ok": False, "message": message}, status_code=409)
        return JSONResponse({"ok": True, "message": message})

    @app.post("/stop")
    async def stop_link(request: Request) -> JSONResponse:
        try:
            validate_same_origin(request)
        except ValueError as exc:
            return JSONResponse({"ok": False, "message": str(exc)}, status_code=403)
        ok, message = app.state.controller.stop()
        if not ok:
            return JSONResponse({"ok": False, "message": message}, status_code=409)
        return JSONResponse({"ok": True, "message": message})

    @app.get("/status")
    async def status() -> dict[str, Any]:
        return app.state.controller.status_snapshot()

    @app.get("/logs")
    async def logs(limit: int = 200) -> dict[str, list[str]]:
        safe_limit = max(1, min(limit, 1000))
        return {"lines": app.state.log_handler.lines(safe_limit)}

    @app.get("/config/effective")
    async def effective_config() -> dict[str, Any]:
        config = app.state.controller.effective_config()
        if config is None:
            config = asdict(app.state.startup_config)
        return {"config": config, "config_path": app.state.config_path}

    @app.get("/files/asset-intent")
    async def get_asset_intent() -> JSONResponse:
        intent_path, _world_state_path = _current_asset_paths(app)
        ok, payload = _read_json_file(intent_path)
        status = 200 if ok else 404
        return JSONResponse(payload, status_code=status)

    @app.put("/files/asset-intent")
    async def put_asset_intent(request: Request) -> JSONResponse:
        body = await request.json()
        raw = body.get("raw") if isinstance(body, dict) else None
        if not isinstance(raw, str):
            return JSONResponse({"ok": False, "message": "Request body must include string field 'raw'."}, status_code=400)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return JSONResponse({"ok": False, "message": f"Invalid JSON: {exc}"}, status_code=400)
        if not isinstance(parsed, dict):
            return JSONResponse({"ok": False, "message": "JSON root must be an object."}, status_code=400)

        intent_path, _world_state_path = _current_asset_paths(app)
        _write_json_file(intent_path, parsed)
        return JSONResponse(
            {
                "ok": True,
                "message": "Asset intent saved.",
                "path": str(intent_path),
                "content": parsed,
                "raw": json.dumps(parsed, indent=2, sort_keys=True),
            }
        )

    @app.get("/files/world-state")
    async def get_world_state() -> JSONResponse:
        _intent_path, world_state_path = _current_asset_paths(app)
        ok, payload = _read_json_file(world_state_path)
        status = 200 if ok else 404
        return JSONResponse(payload, status_code=status)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local ATLAS asset web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface")
    parser.add_argument("--port", type=int, default=8841, help="HTTP port")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload")
    parser.add_argument(
        "--config",
        default=str(default_config_path(__file__, "asset_webui.json")),
        help="Asset web UI JSON config file",
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_asset_app(config_path=args.config),
        host=args.host,
        port=args.port,
        reload=args.reload,
        access_log=False,
    )


if __name__ == "__main__":
    main()
