"""Top-level run() entry point - wires config, radio, transport, and mode runner."""
from __future__ import annotations

import asyncio
import inspect
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Callable

from atlas_meshtastic_link.config.schema import LinkConfig, load_config

log = logging.getLogger(__name__)


def run(
    config: str | Path | LinkConfig,
    *,
    on_ready: Callable[[], None] | None = None,
    stop_event: asyncio.Event | None = None,
    status_hook: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Blocking entry point for the Atlas Meshtastic Link.

    Args:
        config: Path to a JSON config file, or a LinkConfig instance.
        on_ready: Optional callback invoked once the link is running.
        stop_event: Optional asyncio.Event to signal shutdown (useful for tests).
    """
    cfg = load_config(config)
    logging.basicConfig(
        level=getattr(logging, cfg.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )
    log.info("[LINK] Starting atlas_meshtastic_link in %s mode", cfg.mode)

    asyncio.run(_async_main(cfg, on_ready=on_ready, stop_event=stop_event, status_hook=status_hook))


async def _async_main(
    cfg: LinkConfig,
    *,
    on_ready: Callable[[], None] | None = None,
    stop_event: asyncio.Event | None = None,
    status_hook: Callable[[dict[str, Any]], None] | None = None,
) -> None:
    """Async main loop - builds components and runs the appropriate mode."""
    if stop_event is None:
        stop_event = asyncio.Event()
        if sys.platform != "win32":
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, stop_event.set)

    radio = _build_radio(cfg)
    log.info("[LINK] Radio built: %s", type(radio).__name__)
    try:
        if cfg.mode == "gateway":
            await _run_gateway(cfg, radio, stop_event, on_ready, status_hook)
        elif cfg.mode == "asset":
            await _run_asset(cfg, radio, stop_event, on_ready, status_hook)
        else:
            raise ValueError(f"Unknown mode: {cfg.mode!r}. Expected 'gateway' or 'asset'.")
    finally:
        close_fn = getattr(radio, "close", None)
        if close_fn is not None:
            maybe_awaitable = close_fn()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable


def _build_radio(cfg: LinkConfig):  # noqa: ANN202
    """Construct the appropriate RadioInterface based on config."""
    from atlas_meshtastic_link.transport.serial_radio import SerialRadioAdapter

    port = cfg.radio.port
    if port is not None:
        return SerialRadioAdapter(
            port,
            segment_size=cfg.transport.segment_size,
            reliability_method=cfg.transport.reliability_method,
            spool_path=cfg.transport.spool_path,
        )

    if cfg.radio.auto_discover:
        from atlas_meshtastic_link.transport.discovery import discover_usb_ports

        discovered = discover_usb_ports()
        if not discovered:
            raise RuntimeError("No radio port configured and auto-discovery found nothing.")

        busy_ports: list[str] = []
        for candidate in discovered:
            try:
                log.info("[LINK] Trying auto-discovered port: %s", candidate.device)
                return SerialRadioAdapter(
                    candidate.device,
                    segment_size=cfg.transport.segment_size,
                    reliability_method=cfg.transport.reliability_method,
                    spool_path=cfg.transport.spool_path,
                )
            except RuntimeError as exc:
                if "already in use" not in str(exc):
                    raise
                busy_ports.append(candidate.device)
                log.warning("[LINK] Auto-discovered port %s is busy; trying next candidate", candidate.device)

        raise RuntimeError(
            "No available auto-discovered radio ports; all discovered ports are in use: "
            + ", ".join(busy_ports)
        )

    raise RuntimeError("No radio port configured; set radio.port or enable radio.auto_discover.")


async def _run_gateway(  # noqa: ANN001
    cfg: LinkConfig,
    radio,
    stop_event: asyncio.Event,
    on_ready: Callable[[], None] | None,
    status_hook: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Start gateway mode (HTTP bridge + discovery router + business runtime)."""
    from atlas_meshtastic_link.gateway.http_bridge import AtlasHttpBridge
    from atlas_meshtastic_link.gateway.interaction_log import InteractionLog
    from atlas_meshtastic_link.gateway.operations.runtime import GatewayOperationsRuntime
    from atlas_meshtastic_link.gateway.router import GatewayRouter

    interaction_log = InteractionLog(cfg.gateway.interaction_log_path)
    interaction_log.open()

    bridge = AtlasHttpBridge(
        base_url=cfg.gateway.api_base_url,
        token=cfg.gateway.api_token,
    )
    await bridge.start()
    runtime = GatewayOperationsRuntime(
        radio=radio,
        bridge=bridge,
        config=cfg.gateway,
        stop_event=stop_event,
        status_hook=(lambda payload: _emit_status(status_hook, **payload)),
        interaction_log=interaction_log,
    )

    def on_assets_changed(assets: list[str]) -> None:
        _emit_status(status_hook, connected_assets=assets)

    router = GatewayRouter(
        radio=radio,
        gateway_id=cfg.gateway.gateway_id,
        challenge_code=cfg.gateway.challenge_code,
        expected_response_code=cfg.gateway.expected_response_code,
        command_channel_url=cfg.gateway.command_channel_url,
        asset_lease_timeout_seconds=cfg.gateway.asset_lease_timeout_seconds,
        stop_event=stop_event,
        on_assets_changed=on_assets_changed,
        on_business_message=runtime.on_radio_message,
        interaction_log=interaction_log,
    )
    router_task = asyncio.create_task(router.run(), name="atlas_gateway_router")
    runtime_task = asyncio.create_task(runtime.run(), name="atlas_gateway_runtime")
    stop_task = asyncio.create_task(stop_event.wait(), name="atlas_gateway_stop_wait")
    usage_task = asyncio.create_task(
        _log_channel_usage_periodically("gateway", radio, stop_event),
        name="atlas_gateway_channel_usage",
    )
    gateway_channel = await _read_channel_url(radio)
    _emit_status(
        status_hook,
        channel_connected=gateway_channel is not None,
        channel_url=gateway_channel,
        connected_assets=[],
    )

    log.info("[LINK] Gateway mode - HTTP bridge ready")
    try:
        if on_ready:
            on_ready()
        done, _ = await asyncio.wait(
            {router_task, runtime_task, stop_task, usage_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if router_task in done:
            exc = router_task.exception()
            if exc is not None:
                raise exc
        if runtime_task in done:
            exc = runtime_task.exception()
            if exc is not None:
                raise exc
        if usage_task in done:
            exc = usage_task.exception()
            if exc is not None:
                raise exc
    finally:
        stop_event.set()
        stop_task.cancel()
        usage_task.cancel()
        runtime_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await asyncio.gather(usage_task, return_exceptions=True)
        await asyncio.gather(runtime_task, return_exceptions=True)
        if not router_task.done():
            try:
                await asyncio.wait_for(router_task, timeout=2.0)
            except asyncio.TimeoutError:
                router_task.cancel()
                await asyncio.gather(router_task, return_exceptions=True)
        interaction_log.close()
        await bridge.stop()


async def _run_asset(  # noqa: ANN001
    cfg: LinkConfig,
    radio,
    stop_event: asyncio.Event,
    on_ready: Callable[[], None] | None,
    status_hook: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Start asset mode (discovery/provisioning + business loop)."""
    from atlas_meshtastic_link.asset.runner import AssetRunner
    from atlas_meshtastic_link.asset.provisioning import ProvisioningHandshake

    log.info("[LINK] Asset mode starting (auto_provision=%s)", cfg.asset.auto_provision)
    if on_ready:
        on_ready()
    usage_task = asyncio.create_task(
        _log_channel_usage_periodically("asset", radio, stop_event),
        name="atlas_asset_channel_usage",
    )

    current_channel = await _read_channel_url(radio)
    _emit_status(
        status_hook,
        channel_connected=current_channel is not None,
        channel_url=current_channel,
    )

    if cfg.asset.auto_provision:
        if current_channel is None:
            _emit_status(status_hook, channel_connected=False)
        while not stop_event.is_set():
            handshake = ProvisioningHandshake(
                radio=radio,
                asset_id=cfg.asset.entity_id,
                expected_challenge_code=cfg.asset.expected_challenge_code,
                response_code=cfg.asset.response_code,
                timeout_seconds=cfg.asset.provision_timeout_seconds,
                discovery_interval_seconds=cfg.asset.discovery_interval_seconds,
                stop_event=stop_event,
            )
            provisioned = await handshake.run()
            if provisioned:
                connected_channel = await _read_channel_url(radio)
                log.info("[LINK] Asset provisioning complete; command channel joined (%s)", connected_channel or "unknown")
                _emit_status(
                    status_hook,
                    channel_connected=True,
                    channel_url=connected_channel,
                )
                break

            if stop_event.is_set():
                break

            retry_delay = max(1.0, cfg.asset.discovery_interval_seconds)
            log.warning("[LINK] Asset provisioning timed out; retrying in %.1fs", retry_delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=retry_delay)
            except asyncio.TimeoutError:
                pass

    asset_runner = AssetRunner(
        radio=radio,
        config=cfg.asset,
        stop_event=stop_event,
        status_hook=status_hook,
    )
    runner_task = asyncio.create_task(asset_runner.run(), name="atlas_asset_runner")
    stop_task = asyncio.create_task(stop_event.wait(), name="atlas_asset_stop_wait")
    try:
        done, _ = await asyncio.wait({runner_task, stop_task, usage_task}, return_when=asyncio.FIRST_COMPLETED)
        if runner_task in done:
            exc = runner_task.exception()
            if exc is not None:
                raise exc
    finally:
        stop_event.set()
        stop_task.cancel()
        runner_task.cancel()
        usage_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await asyncio.gather(runner_task, return_exceptions=True)
        await asyncio.gather(usage_task, return_exceptions=True)


async def _read_channel_url(radio) -> str | None:  # noqa: ANN001
    get_channel = getattr(radio, "get_channel_url", None)
    if not callable(get_channel):
        return None
    try:
        channel = await get_channel()
    except Exception:
        return None
    if not channel:
        return None
    return str(channel)


async def _read_channel_usage_summary(radio) -> str | None:  # noqa: ANN001
    get_usage = getattr(radio, "get_channel_usage_summary", None)
    if not callable(get_usage):
        return None
    try:
        usage = await get_usage()
    except Exception:
        return None
    if not usage:
        return None
    return str(usage)


async def _log_channel_usage_periodically(  # noqa: ANN001
    mode_name: str,
    radio,
    stop_event: asyncio.Event,
    interval_seconds: float = 5.0,
) -> None:
    while not stop_event.is_set():
        summary = await _read_channel_usage_summary(radio)
        if summary is None:
            channel_url = await _read_channel_url(radio)
            summary = channel_url or "none"
        log.info("[LINK] %s radio channel usage: %s", mode_name.capitalize(), summary)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue


def _emit_status(status_hook: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    if status_hook is None:
        return
    try:
        status_hook(payload)
    except Exception:
        log.debug("[LINK] status_hook raised", exc_info=True)
