# Architecture Overview

High-level architecture of the dual-mode Meshtastic link: gateway (internet bridge) and asset (edge SDK).

## System Diagram

```mermaid
flowchart TB
    subgraph Internet
        AC[Atlas Command API]
    end

    subgraph Gateway["Gateway Mode"]
        GW[Gateway Router]
        GW -->|HTTP| AC
    end

    subgraph Mesh["Meshtastic Mesh"]
        subgraph CommandChannel["Command Channel (encrypted)"]
            A1[Asset 1]
            A2[Asset 2]
            A3[Asset 3]
        end
    end

    GW <-->|Radio / Serial| CommandChannel
    A1 <-->|Overhear| A2
    A2 <-->|Overhear| A3
    A1 <-->|Overhear| A3
```

## Gateway Discovery Flow

```mermaid
sequenceDiagram
    participant Asset
    participant Public as Public Channel
    participant Gateway
    participant DM as PKC Direct Message
    participant CC as Command Channel

    Asset->>Public: 1. Broadcast search
    Gateway->>Public: 2. Respond with presence
    Asset->>DM: 3. Initiate handshake
    Gateway->>DM: 4. Send challenge
    Asset->>DM: 5. Send response
    Gateway->>DM: 6. Send channel credentials
    Asset->>CC: 7. Join Command Channel
    Asset->>CC: 8. All subsequent traffic
```

## Data Flow (Subscriptions & Overhearing)

```mermaid
flowchart LR
    subgraph Asset["Asset"]
        WS[world_state.json]
        User[User Code]
        Link[Link Package]
        Link --> WS
        User -->|read| WS
        User -->|subscribe| Link
    end

    subgraph Gateway
        GW2[Gateway]
        Lease[Lease Registry]
        GW2 --> Lease
    end

    subgraph Mesh2["Command Channel"]
        A[Asset A]
        B[Asset B]
    end

    Link -->|subscription request| GW2
    GW2 -->|diff push| A
    A -->|overhear| B
    B -->|ingest| Link
```

## Module Map

```
src/atlas_meshtastic_link/
â”‚
â”œâ”€â”€ __init__.py              # Public API: run(), __version__
â”œâ”€â”€ _link.py                 # Entry point: config â†’ radio â†’ mode runner
â”‚
â”œâ”€â”€ config/                  # LAYER: Configuration (no deps on other layers)
â”‚   â”œâ”€â”€ schema.py            #   LinkConfig dataclasses + load_config()
â”‚   â””â”€â”€ modes/               #   Radio mode profiles (JSON)
â”‚       â””â”€â”€ general.json
â”‚
â”œâ”€â”€ transport/               # LAYER: Raw radio I/O (only layer touching serial)
â”‚   â”œâ”€â”€ interface.py         #   RadioInterface Protocol
â”‚   â”œâ”€â”€ discovery.py         #   USB auto-discovery via pyserial VID/PID
â”‚   â”œâ”€â”€ serial_radio.py      #   SerialRadioAdapter (wraps meshtastic SerialInterface)
â”‚   â”œâ”€â”€ chunking.py          #   Binary 16-byte header chunk/parse protocol
â”‚   â””â”€â”€ reassembly.py        #   MessageReassembler: buckets, TTL, gap detection
â”‚
â”œâ”€â”€ protocol/                # LAYER: Wire format + reliability (no radio specifics)
â”‚   â”œâ”€â”€ envelope.py          #   MessageEnvelope: msgpack + zstd encode/decode
â”‚   â”œâ”€â”€ reliability.py       #   ReliabilityStrategy Protocol + windowed impl
â”‚   â”œâ”€â”€ dedup.py             #   RequestDeduper
â”‚   â””â”€â”€ spool.py             #   PersistentSpool: disk-backed durable queue
â”‚
â”œâ”€â”€ state/                   # LAYER: Shared state (used by both gateway & asset)
â”‚   â”œâ”€â”€ world_state.py       #   WorldStateStore: in-memory dict + atomic JSON flush
â”‚   â”œâ”€â”€ subscriptions.py     #   LeaseRegistry: TTL-based subscription tracking
â”‚   â””â”€â”€ overhearing.py       #   OverhearingFilter: passive ingest routing
â”‚
â”œâ”€â”€ gateway/                 # LAYER: Gateway mode (depends on protocol/, state/)
â”‚   â”œâ”€â”€ router.py            #   GatewayRouter: receive â†’ dispatch â†’ reply
â”‚   â”œâ”€â”€ http_bridge.py       #   AtlasHttpBridge: async HTTP to Atlas Command API
â”‚   â”œâ”€â”€ lease_registry.py    #   Per-asset subscription lease management
â”‚   â””â”€â”€ operations/          #   Pluggable async operation handlers
â”‚       â””â”€â”€ __init__.py
â”‚
â””â”€â”€ asset/                   # LAYER: Asset mode (depends on protocol/, state/)
    â”œâ”€â”€ runner.py            #   AssetRunner: main asset event loop
    â”œâ”€â”€ edge_client.py       #   EdgeClient: typed API for user code
    â”œâ”€â”€ provisioning.py      #   ProvisioningHandshake: gateway discovery state machine
    â””â”€â”€ sync.py              #   AssetSync: ingest diffs â†’ update WorldState
```

## Layer Dependencies

```
config/  â†â”€â”€ (no deps)
transport/ â†â”€â”€ config/
protocol/ â†â”€â”€ (standalone, knows MessageEnvelope)
state/ â†â”€â”€ (standalone)
gateway/ â†â”€â”€ protocol/, state/, transport/
asset/ â†â”€â”€ protocol/, state/, transport/
_link.py â†â”€â”€ all layers
```

## Key Concepts

- **Gateway:** Internet-connected bridge. Talks to Atlas Command over HTTP and to assets over the mesh.
- **Asset:** Edge node. Maintains local `world_state.json`, subscribes to entities, and can overhear traffic addressed to others.
- **Command Channel:** Shared encrypted channel. All post-provisioning traffic flows here.
- **Discovery:** Happens on public channel + PKC DM; credentials never broadcast. See [GATEWAY_DISCOVERY.md](GATEWAY_DISCOVERY.md).

