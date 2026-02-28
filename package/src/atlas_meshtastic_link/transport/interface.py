"""RadioInterface Protocol for radio backends."""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RadioInterface(Protocol):
    """Structural interface for radio backends."""

    async def send(self, data: bytes, destination: str | int | None = None) -> None:
        """Send raw bytes over the radio."""
        ...

    async def receive(self) -> tuple[bytes, str]:
        """Wait for and return (payload, sender_id)."""
        ...

    async def close(self) -> None:
        """Release underlying resources."""
        ...

    async def get_channel_url(self) -> str | None:
        """Return the current mesh channel URL if available."""
        ...

    async def set_channel_url(self, channel_url: str) -> None:
        """Apply mesh channel credentials from a Meshtastic URL."""
        ...

    async def get_node_id(self) -> str | None:
        """Return the node user ID (for example !abcdef12) if available."""
        ...
