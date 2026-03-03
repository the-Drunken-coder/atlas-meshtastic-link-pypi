# World State (Local Cache)

## Purpose

The link package maintains a local cache of the known world: the asset's own telemetry, neighbor assets, known tracks, tasks, and geofeatures. User code (like a drone's flight controller) interacts with this local cache rather than blocking on radio transmissions. This decouples high-speed robotics loops from the low-speed, high-latency Meshtastic network.

## File-Based State Exposure

The current world state is exposed to user code via a local `world_state.json` file. Since Atlas Command's data (Entities, Tasks, Objects, Tracks) is already heavily JSON-oriented, `world_state.json` mirrors these structures. User code can read this file at whatever rate it needs to make decisions.

### Structure

The file uses a flat structure to store all known state regardless of how it was received (overheard from neighbors vs subscribed from the gateway). 

- `entities`: A dictionary mapping entity IDs to wrapper records (`kind`, `id`, `data`, `source`, `source_node`, `received_at`, `version`, ...). `data` contains Atlas entity payloads (Assets, Tracks, Geofeatures, etc.).
- `tasks`: A dictionary mapping task IDs to wrapper records with the same envelope shape; task payloads are stored under `data`.
- `objects`: A dictionary mapping object IDs to wrapper records with the same envelope shape; object payloads are stored under `data`.
- `index`: Additional metadata about what data the Gateway considers active or synced, including entity ID references.

## Interaction Flow

1. **Boot & Provision:** The asset boots, provisions with the Gateway, and joins the encrypted Command Channel.
2. **Configuration:** The package reads config and begins broadcasting the asset's telemetry on the configured interval.
3. **Passive Overhearing:** The package hears a neighbor asset broadcasting. It adds the neighbor to the world state with a TTL expiration timer.
4. **Dynamic Request:** The user logic decides it needs specific data. It tells the link package to subscribe to a resource.
5. **Gateway Fetch:** The link package sends a subscription request to the Gateway. The Gateway resolves the data and sends it back.
6. **State Update:** The link package writes the received data into the world state. User code reads and acts on it.
