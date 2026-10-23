# Amazon Continuity Hub — 90-Second Judge Demo Script

A concise, high-impact demonstration script showcasing cross-device context synchronization, voice-driven handoff, and resilient cloud architecture.

---

## 🎙️ The 90-Second Presentation Script

### [00:00 – 00:15] Hook & Problem
* **What to Say**:
  > "Today's smart homes have dozens of connected devices, yet they operate in total silos. If you watch a show in your browser and walk into the living room, your TV and Alexa have zero clue what you were doing. We built **Amazon Continuity Hub**: a real-time WebSocket state mesh that unifies Alexa, Fire TV, browser extensions, and IoT into a single real-time user context."
* **What to Show**:
  > Terminal split or UI showing Firefox extension, Fire TV simulator, and Alexa voice forwarder connected to the hub (`/health` shows 3 active WebSocket connections).

---

### [00:15 – 00:45] The Core Demo: Instant Cross-Device Handoff
* **What to Say**:
  > "Watch this: in Firefox, I'm watching *The Expanse* at minute 12:34 (754 seconds). The extension broadcasts a lightweight state update over WebSocket. Now I say: *'Alexa, continue this on Fire TV.'* Alexa queries our hub, matches the active context, and instantly dispatches an authenticated play command to Fire TV with the exact URL and timestamp."
* **What to Show**:
  > Show Fire TV receiving `{"action": "play", "url": "...", "position_seconds": 754.0}` and changing state to `playing`. Firefox immediately receives the presence broadcast that Fire TV took over.

---

### [00:45 – 01:05] Contextual AI Reasoning & Smart Home
* **What to Say**:
  > "Next, I ask Alexa: *'What was I watching?'* Amazon Bedrock Converse analyzes the unified state and answers immediately: *'Fire TV is playing The Expanse on Prime Video.'* When I say *'Turn off the living room light'*, the IoT light socket flips to `power_off` in under 15 milliseconds."
* **What to Show**:
  > Terminal logs highlighting Bedrock context synthesis and smart light power state changing to `off`.

---

### [01:05 – 01:20] Reliability & Engineering Hardening
* **What to Say**:
  > "Behind the scenes, this hub is battle-tested. We solved the classic reconnect race condition with connection epoch validation, hardened DynamoDB writes with optimistic concurrency retries and last-writer-wins fallback, and exposed standard Prometheus metrics on `/metrics`. If AWS services throttle, Bedrock Converse degrades gracefully within 4.5 seconds to preserve Alexa's strict voice deadlines."
* **What to Show**:
  > `curl http://localhost:8000/metrics` displaying Prometheus counters (`hub_events_routed_total`, `hub_ws_connections`).

---

### [01:20 – 01:30] Deployment & Closing
* **What to Say**:
  > "Everything is infrastructure-as-code on AWS ECS Fargate via Terraform with automated GitHub Actions OIDC CD and automated health check rollback scripts. Amazon Continuity Hub bridges the gap between voice and visual interfaces. Thank you!"

---

## 🛡️ The 3 Joke-Proof Fallbacks

If Wi-Fi, hardware, or AWS credentials misbehave during a live pitch, use these three battle-tested fallbacks in order:

### Fallback 1: Live Mesh Simulator (`make sim`)
* **Trigger**: Real browser extension or physical Fire TV hardware disconnects.
* **Action**: Run the automated end-to-end mesh simulator:
  ```bash
  make sim
  # OR: python scripts/simulate_mesh.py
  ```
* **Why it works**: Simulates all 3 client devices (Firefox, Fire TV, Smart Light) over real WebSocket connections and exercises all 7 real-time handoff steps in under 100ms.

### Fallback 2: Dual-Mode Local Fallback (`USE_AWS=false`)
* **Trigger**: AWS credentials expired, Bedrock quota exceeded, or no internet access.
* **Action**: Toggle `USE_AWS=false` in `.env` or run:
  ```bash
  USE_AWS=false uvicorn app.main:app --port 8000
  ```
* **Why it works**: The hub runs completely self-contained with an in-memory optimistic state store and local rule-based AI reasoning engine. Zero AWS credentials or network connectivity needed.

### Fallback 3: Pre-Recorded Video Walkthrough
* **Trigger**: Complete host or presentation system outage.
* **Action**: Open the pre-recorded video demo (`assets/demo.mp4` or backup cloud link) showing the real-time browser extension and Fire TV sync with synchronized voice commands.
