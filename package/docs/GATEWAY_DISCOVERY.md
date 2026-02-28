# Gateway Discovery and Provisioning

## Overview

Before an asset can participate on the Command Channel, it must find a gateway and complete a provisioning handshake. This is the one exception to the "all traffic on the Command Channel" rule — discovery and credential exchange happen outside the Command Channel because the asset doesn't have the channel credentials yet.

## Phases

### 1. Search (Public Channel)

- The asset broadcasts a search/discovery message on the default public channel.
- Any gateway in range that hears the search responds with its presence.
- This is intentionally on the public channel because the asset has no knowledge of the gateway's Command Channel at this point.

### 2. Challenge and Credentials (Private DM)

- Once the asset identifies a gateway, the handshake switches to direct messages between the asset and the gateway.
- The gateway sends a challenge code to the asset.
- The asset responds with the correct response code.
- If the response is valid, the gateway sends the Command Channel URL to the asset over the same private DM.
- This exchange is JSON payload over the mesh transport, so confidentiality depends on your Meshtastic channel privacy settings and physical radio security.

### 3. Join Command Channel

- The asset applies the received credentials and joins the Command Channel.
- From this point forward, all communication between the asset and gateway happens on the shared Command Channel.
- The asset can now participate in subscriptions, state sync, and opportunistic overhearing like every other asset on the channel.

## Challenge/Response Codes

- Both the gateway and asset must be configured with matching challenge and response codes.
- The challenge code is what the gateway sends to verify the asset is authorized.
- The response code is what the asset sends back to prove it knows the shared secret.
- These are pre-shared values set in configuration on both sides before deployment.
- If the asset sends the wrong response code, the gateway rejects it and does not send credentials.

## Security Model

- **Public channel exposure is minimal:** Only the search/discovery message is broadcast publicly. It reveals that an asset is looking for a gateway but contains no credentials or sensitive data.
- **Credentials are never broadcast:** Command channel URL is only sent in direct message payloads after successful challenge/response.
- **No additional application-layer encryption:** Provisioning payloads are not encrypted by this package beyond whatever protection the underlying radio/channel already provides.
- **Deploy with unique secrets:** Replace default challenge/response values before field use.
