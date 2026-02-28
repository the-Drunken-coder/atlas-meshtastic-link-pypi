"""Small local web UI for gateway-mode smoke testing."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

try:
    from scripts._webui_common import (
        InMemoryLogBufferHandler,
        LinkProcessController,
        LOG_FORMAT,
        autostart_serial_only,
        build_gateway_config,
        default_config_path,
        install_log_capture,
        load_mode_config,
        setup_script_logging,
        validate_same_origin,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution path
    from _webui_common import (  # type: ignore[no-redef]
        InMemoryLogBufferHandler,
        LinkProcessController,
        LOG_FORMAT,
        autostart_serial_only,
        build_gateway_config,
        default_config_path,
        install_log_capture,
        load_mode_config,
        setup_script_logging,
        validate_same_origin,
    )

LOGGER_NAME = "atlas_meshtastic_link.webui.gateway"
log = logging.getLogger(LOGGER_NAME)

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>ATLAS Gateway Test Console</title>
  <style>
    :root {
      --bg: #f7f3ec;
      --panel: #ffffff;
      --ink: #1b1f24;
      --subtle: #616977;
      --line: #d5dbe5;
      --accent: #005f73;
      --accent-soft: #d8eff4;
      --warn: #9a3412;
      --warn-soft: #ffedd5;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Gill Sans", sans-serif;
      color: var(--ink);
      background:
        radial-gradient(70rem 40rem at 10% -10%, #d7e7ef 0%, transparent 60%),
        radial-gradient(70rem 40rem at 120% 10%, #fae6c9 0%, transparent 55%),
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
      box-shadow: 0 12px 26px rgba(16, 24, 40, 0.06);
    }
    .hero h1 {
      margin: 0 0 .25rem 0;
      font-family: Georgia, "Times New Roman", serif;
      letter-spacing: 0.02em;
      font-size: clamp(1.35rem, 2.8vw, 1.9rem);
    }
    .hero p {
      margin: 0;
      color: var(--subtle);
    }
    .grid {
      display: grid;
      gap: .75rem;
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
    .field { display: grid; gap: .35rem; }
    .field label { font-size: .84rem; color: var(--subtle); }
    input, select {
      width: 100%;
      border-radius: 9px;
      border: 1px solid var(--line);
      padding: .55rem .65rem;
      background: #fff;
      color: var(--ink);
      font: inherit;
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
      color: #0d3b48;
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
      border: 1px solid #f4c7b9;
      background: var(--warn-soft);
      color: var(--warn);
      border-radius: 10px;
      min-height: 38px;
      padding: .55rem .7rem;
      font-size: .9rem;
    }
    .intent-card {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: .65rem;
      margin-bottom: .6rem;
      background: #fafbfd;
    }
    .intent-head {
      font-size: .88rem;
      margin-bottom: .45rem;
      color: var(--subtle);
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="layout">
    <section class="hero">
      <span class="pill">Gateway mode</span>
      <h1>ATLAS Gateway Test Console</h1>
      <p>Local smoke-test harness for start/stop, config validation, and runtime logs.</p>
    </section>

    <section class="panel">
      <form id="gateway-form">
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
            <label for="gateway_api_base_url">Gateway API base URL</label>
            <input id="gateway_api_base_url" name="gateway_api_base_url" value="https://atlascommandapi.org" />
          </div>
          <div class="field">
            <label for="gateway_api_token">Gateway API token (optional)</label>
            <input id="gateway_api_token" name="gateway_api_token" />
          </div>
          <div class="field">
            <label for="asset_lease_timeout_seconds">Asset lease timeout (seconds)</label>
            <input id="asset_lease_timeout_seconds" name="asset_lease_timeout_seconds" value="45" />
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
        </div>
        <div class="actions">
          <button type="button" class="btn-start" id="start-btn">Start gateway</button>
          <button type="button" class="btn-stop" id="stop-btn">Stop gateway</button>
        </div>
      </form>
      <div class="toast" id="action-message"></div>
      <div class="status">
        <div><strong>State:</strong> <span id="status-state">unknown</span></div>
        <div><strong>Uptime:</strong> <span id="status-uptime" class="muted">n/a</span></div>
        <div><strong>Channel:</strong> <span id="status-channel-url" class="muted">unknown</span></div>
        <div><strong>Connected assets:</strong> <span id="status-connected-assets" class="muted">none</span></div>
        <div><strong>Last error:</strong> <span id="status-error" class="muted">none</span></div>
      </div>
    </section>

    <section class="panel">
      <div class="actions" style="margin-top:0; margin-bottom:.45rem;">
        <button type="button" class="btn-neutral" id="asset-intents-refresh-btn">Refresh asset intents</button>
      </div>
      <h3>Connected Assets: Latest Asset Intent</h3>
      <div id="asset-intents-view" class="mono">No connected asset intents yet.</div>
    </section>

    <section class="panel">
      <h3>Sync health</h3>
      <div id="sync-health-view" class="mono">No sync health data yet.</div>
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
    const form = document.getElementById("gateway-form");
    const actionMessage = document.getElementById("action-message");
    const statusState = document.getElementById("status-state");
    const statusUptime = document.getElementById("status-uptime");
    const statusChannelUrl = document.getElementById("status-channel-url");
    const statusConnectedAssets = document.getElementById("status-connected-assets");
    const statusError = document.getElementById("status-error");
    const configView = document.getElementById("config-view");
    const logsView = document.getElementById("logs-view");
    const assetIntentsView = document.getElementById("asset-intents-view");
    const syncHealthView = document.getElementById("sync-health-view");

    async function postWithForm(path) {
      const payload = new FormData(form);
      const response = await fetch(path, { method: "POST", body: payload });
      const body = await response.json();
      if (!response.ok) throw new Error(body.message || "Request failed");
      actionMessage.textContent = body.message || "Done.";
      await refreshAll();
    }

    async function refreshStatus() {
      const response = await fetch("/status");
      const data = await response.json();
      statusState.textContent = data.state;
      statusUptime.textContent = data.uptime_seconds === null ? "n/a" : `${data.uptime_seconds}s`;
      statusChannelUrl.textContent = data.channel_url || "unknown";
      const assets = Array.isArray(data.connected_assets) ? data.connected_assets : [];
      statusConnectedAssets.textContent = assets.length ? assets.join(", ") : "none";
      statusError.textContent = data.last_error || "none";
      const syncHealth = data.sync_health_by_asset || {};
      const syncEvent = data.sync_health_event || null;
      const lines = [];
      if (syncEvent) {
        lines.push(`event: ${JSON.stringify(syncEvent)}`);
      }
      const assetIds = Object.keys(syncHealth).sort();
      if (!assetIds.length) {
        lines.push("No sync health data yet.");
      } else {
        for (const assetId of assetIds) {
          const item = syncHealth[assetId] || {};
          lines.push(
            `${assetId} state=${item.state || "unknown"} seq=${item.last_seq ?? "?"} age_ms=${item.discrepancy_age_ms ?? 0} reason=${item.last_reason || "none"}`
          );
        }
      }
      syncHealthView.textContent = lines.join("\\n");
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

    async function refreshAssetIntents() {
      const response = await fetch("/assets/intents");
      const data = await response.json();
      const items = Array.isArray(data.asset_intents) ? data.asset_intents : [];
      if (!items.length) {
        assetIntentsView.textContent = "No connected asset intents yet.";
        return;
      }
      const lines = [];
      for (const item of items) {
        lines.push(`asset_id: ${item.asset_id}`);
        lines.push(`updated_at_epoch: ${item.updated_at ?? "unknown"}`);
        lines.push(JSON.stringify(item.payload || {}, null, 2));
        lines.push("");
      }
      assetIntentsView.textContent = lines.join("\\n");
    }

    async function refreshAll() {
      try {
        await Promise.all([refreshStatus(), refreshLogs(), refreshConfig(), refreshAssetIntents()]);
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
    document.getElementById("asset-intents-refresh-btn").addEventListener("click", async () => { await refreshAssetIntents(); });

    refreshAll();
    setInterval(refreshAll, 1500);
  </script>
</body>
</html>
"""


def create_gateway_app(*, auto_start: bool = True, config_path: str | Path | None = None) -> FastAPI:
    """Build FastAPI app for local gateway UI."""
    setup_script_logging()
    handler = InMemoryLogBufferHandler(max_lines=1000)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    install_log_capture(handler, ["atlas_meshtastic_link", LOGGER_NAME])
    controller = LinkProcessController(mode="gateway", logger=log)
    config_file = Path(config_path) if config_path is not None else default_config_path(__file__, "gateway_webui.json")
    try:
        startup_config = load_mode_config(config_file, "gateway")
        log.info("[WEBUI] loaded gateway config from %s", config_file)
    except Exception as exc:
        log.warning("[WEBUI] failed to load gateway config %s: %s", config_file, exc)
        startup_config = build_gateway_config(
            {
                "radio_mode": "serial",
                "auto_discover": "on",
                "gateway_api_base_url": "https://atlascommandapi.org",
                "asset_lease_timeout_seconds": "45",
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
                mode_name="gateway",
            )
            await asyncio.sleep(0)
        yield
        controller.stop()

    app = FastAPI(title="ATLAS Gateway Test Console", version="0.1.0", lifespan=lifespan)
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
            config = build_gateway_config(form)
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

    @app.get("/assets/intents")
    async def asset_intents() -> dict[str, Any]:
        snapshot = app.state.controller.status_snapshot()
        connected_assets = snapshot.get("connected_assets", [])
        intents_map = snapshot.get("gateway_asset_intents", {})

        result: list[dict[str, Any]] = []
        if isinstance(connected_assets, list) and isinstance(intents_map, dict):
            for asset_id in connected_assets:
                key = str(asset_id)
                item = intents_map.get(key)
                if not isinstance(item, dict):
                    continue
                result.append(
                    {
                        "asset_id": item.get("asset_id") or key,
                        "node_id": key,
                        "updated_at": item.get("updated_at_epoch"),
                        "payload": item.get("payload"),
                    }
                )
        result.sort(key=lambda item: str(item.get("asset_id")))
        return {"ok": True, "connected_assets": connected_assets, "asset_intents": result}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local ATLAS gateway web UI.")
    parser.add_argument("--host", default="127.0.0.1", help="Host interface")
    parser.add_argument("--port", type=int, default=8840, help="HTTP port")
    parser.add_argument("--reload", action="store_true", help="Enable uvicorn reload")
    parser.add_argument(
        "--config",
        default=str(default_config_path(__file__, "gateway_webui.json")),
        help="Gateway web UI JSON config file",
    )
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        create_gateway_app(config_path=args.config),
        host=args.host,
        port=args.port,
        reload=args.reload,
        access_log=False,
    )


if __name__ == "__main__":
    main()
