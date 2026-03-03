from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class FakeRadio:
    node_id: str
    channel_url: str
    peers: dict[str, "FakeRadio"] = field(default_factory=dict)
    inbox: asyncio.Queue[tuple[bytes, str]] = field(default_factory=asyncio.Queue)

    async def send(self, data: bytes, destination: str | int | None = None) -> None:
        if destination in (None, "^all"):
            for peer_id, peer in self.peers.items():
                if peer_id == self.node_id:
                    continue
                await peer.inbox.put((data, self.node_id))
            return

        dest = str(destination)
        if dest in self.peers:
            await self.peers[dest].inbox.put((data, self.node_id))

    async def receive(self) -> tuple[bytes, str]:
        return await self.inbox.get()

    async def close(self) -> None:
        return None

    async def get_channel_url(self) -> str | None:
        return self.channel_url

    async def set_channel_url(self, channel_url: str) -> None:
        self.channel_url = channel_url

    async def get_node_id(self) -> str | None:
        return self.node_id
