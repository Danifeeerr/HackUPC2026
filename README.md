# Minerva — Autonomous Elderly Monitoring System

> HackUPC 2026 · Built on Arduino UNO Q (Qualcomm Dragonwing QRB2210 + STM32U585)

---

## Overview

Minerva is an autonomous monitoring system for elderly people living alone. It continuously observes the environment through sensors and a camera, detects anomalous situations, and acts without any human intervention — alerting the family via WhatsApp and triggering local alarms on the device.

The user (the elderly person) never interacts with the system. It runs entirely on a single Arduino UNO Q device, with no external laptops, hubs, or cloud dependencies in the critical path.

---

## Hardware

The Arduino UNO Q contains two processors:

| Processor | Role |
|---|---|
| **MCU** — STM32U585 (Zephyr RTOS) | Reads sensors via Qwiic/I2C, communicates with MPU via serial bridge |
| **MPU** — Qualcomm Dragonwing QRB2210 (Linux Debian ARM64) | Runs all Python services, Docker, Ollama, and AI models |

**MPU Resources:**
- RAM: 3.6 GB total, ~2.5 GB available
- Storage: 17 GB free on `/home/arduino` (models stored here)
- Camera: `/dev/video0`
- MCU bridge: `/var/run/arduino-router.sock`

**Sensors in use:**
- Thermo (temperature + humidity) — fire/overheating detection
- Distance (ToF) — bathroom inactivity detection
- Camera — fall detection via VLM

---

## Architecture

### Dual Model Design

Two AI models run sequentially, never simultaneously. `OLLAMA_KEEP_ALIVE=0` ensures Ollama releases RAM after each inference.

```
┌─────────────────────────────────────────────────────┐
│                    Arduino UNO Q (MPU)               │
│                                                      │
│  ┌──────────────┐     ┌──────────────┐               │
│  │ Moondream2   │     │ Qwen2.5 1.5B │               │
│  │ VLM ~1.7GB   │     │ LLM ~1.0GB   │               │
│  │ Perception   │     │ Decision     │               │
│  └──────────────┘     └──────────────┘               │
│        ↑                     ↑                       │
│  camera_worker.py       ai_loop.py                   │
│        ↓                     ↓                       │
│  /dev/video0          MCP Server :8000               │
│                              ↓                       │
│                    WhatsApp + Local Alarm             │
└─────────────────────────────────────────────────────┘
```

**RAM budget per model turn:**
```
OS + systemd + Docker + InfluxDB + services = ~948 MB (fixed)

Moondream2 turn:   948 + 1700 = ~2.6 GB  ✅ (active RAM ~1.05 GB)
Qwen2.5 1.5B turn: 948 + 1000 = ~1.9 GB  ✅
```

### Alert Flows

**Sensor alert flow (temperature / bathroom inactivity):**
```
MCU sensor → bridge_listener.py → threshold check
                                        │
                                   POST /alert
                                        │
                                   ai_loop.py
                                        │
                                  Qwen2.5 1.5B
                                        │
                                  tool calls
                                        │
                            ┌───────────┴───────────┐
                     WhatsApp alert           Local alarm
                     (Twilio API)         (LEDs + Buzzer)
```

**Camera fall detection flow:**
```
camera_worker.py (every 30s)
        │
   /dev/video0 → OpenCV frame
        │
   Moondream2 via Ollama
        │
   "fallen" detected?
   ├── NO → wait 30s
   └── YES → POST /alert to ai_loop.py
                    │
              Qwen2.5 1.5B
                    │
              tool calls
                    │
        ┌───────────┴───────────┐
 WhatsApp alert           Local alarm
 (Twilio API)         (LEDs + Buzzer)
```

---

## Services

Four independent processes run on the MPU:

| Process | Port | Responsibility |
|---|---|---|
| `ollama serve` | 11434 | Serves both AI models via HTTP API |
| `mcp_server.py` | 8000 | MCP server — exposes tools (WhatsApp, alarm) |
| `ai_loop.py` | 5000 | Orchestrator — receives alerts, calls Qwen2.5, executes tools |
| `camera_worker.py` | — | Captures frames, runs Moondream2, sends fall alerts |

### AI Models

| Model | Size | Role |
|---|---|---|
| `moondream:latest` | 1.7 GB | VLM — visual scene analysis, fall detection |
| `qwen2.5:1.5b` | 986 MB | LLM — tool calling, alert routing |

### MCP Tools

Qwen2.5 has access to exactly 2 tools (kept minimal for reliability):

| Tool | Description |
|---|---|
| `send_whatsapp_alert(alert_type, message, severity)` | Sends formatted WhatsApp message to family via Twilio |
| `activate_local_alarm(mode)` | Controls LEDs and buzzer on the device |

### Alert Types

| alert_type | Source | Severity | Actions |
|---|---|---|---|
| `temperature` | Thermo sensor | 1-3 | WhatsApp + (alarm if ≥2) |
| `inactivity` | Distance sensor (bathroom) | 1-3 | WhatsApp + (alarm if 3) |
| `fall` | Camera + Moondream2 | 3 | WhatsApp + emergency alarm |

---

## Alert JSON Contract

The payload sent to `ai_loop.py` by any alert source:

```json
{
  "alert_type": "temperature",
  "severity": 3,
  "sensor_data": {
    "temperature": 58.0
  }
}
```

- `alert_type`: `"temperature"` | `"inactivity"` | `"fall"`
- `severity`: `1` (low) | `2` (medium) | `3` (high)
- `sensor_data`: dict with sensor readings, may be empty `{}`

---

## Stack

```
MPU (Linux Debian ARM64)
├── Ollama 0.21.2           — model server (no systemd, manual start)
├── Python 3.13.5 (venv)
│   ├── fastmcp 3.2.4       — MCP server framework
│   ├── flask               — HTTP server for ai_loop.py
│   ├── twilio 9.10.5       — WhatsApp messaging
│   ├── opencv-python-headless — camera capture
│   ├── requests            — HTTP client
│   └── python-dotenv       — environment config
└── Docker
    └── influxdb:2.7-alpine — sensor time-series database
```

---

## Running the System

### Prerequisites

```bash
# Start Ollama (no systemd service — manual start required)
OLLAMA_KEEP_ALIVE=0 OLLAMA_MODELS=/home/arduino/.ollama/models \
  nohup ollama serve > /home/arduino/ollama.log 2>&1 &

# Verify models are available
ollama list
```

### Start all services

```bash
source /home/arduino/venv/bin/activate

# Terminal 1 — MCP server
python3 /home/arduino/mcp_server.py

# Terminal 2 — AI orchestrator
python3 /home/arduino/ai_loop.py

# Terminal 3 — Camera worker
python3 /home/arduino/camera_worker.py
```

### Simulate an alert (for testing)

```bash
# Temperature alert
curl -s -X POST http://localhost:5000/alert \
  -H "Content-Type: application/json" \
  -d '{"alert_type": "temperature", "severity": 3, "sensor_data": {"temperature": 58.0}}'

# Fall alert
curl -s -X POST http://localhost:5000/alert \
  -H "Content-Type: application/json" \
  -d '{"alert_type": "fall", "severity": 3, "sensor_data": {"description": "person lying on floor"}}'
```

### Environment variables (`.env`)

```env
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TWILIO_FROM=whatsapp:+14155238886
TWILIO_TO=whatsapp:+34XXXXXXXXX
ARDUINO_SOCKET=/var/run/arduino-router.sock
```

---

## Current Limitations

### 1. Inference latency (~90s per model)
The QRB2210 is a mobile ARM chip without GPU acceleration. Each model inference takes 60–120 seconds on CPU. This means:
- Alert → WhatsApp: ~90s for sensor alerts
- Fall detected → WhatsApp: ~180s (Moondream + Qwen2.5)

### 2. No concurrent inference
Both models share the same Ollama instance. They cannot run simultaneously due to RAM constraints. Camera analysis and sensor alerts are serialized.

### 3. Ollama has no systemd service
The installation failed to create a systemd unit due to disk space constraints. Ollama must be started manually after each reboot.

### 4. Twilio WhatsApp Sandbox
The system uses the Twilio sandbox, which requires opt-in from each recipient number and has message restrictions. Not suitable for production.

### 5. Camera requires manual process
`camera_worker.py` must be started manually. It is not daemonized.

### 6. Single device, no redundancy
If the UNO Q fails, the system stops. There is no failover.

---

## Scalability — Removing Limitations

### Latency: Replace models with quantized or distilled alternatives
| Current | Replacement | Gain |
|---|---|---|
| Moondream2 1.7GB | Moondream2 Q2 ~800MB | ~40% faster |
| Qwen2.5 1.5B | Phi-3 mini or Gemma 2B with GPU | 5–10x faster with NPU |

The QRB2210 has a Hexagon DSP/NPU that Ollama does not currently use. Future Ollama versions with Qualcomm NPU support would reduce inference to under 10 seconds.

### Concurrent inference: Dedicated hardware per model
Deploy Moondream2 on the MCU side or a dedicated edge accelerator (e.g. Hailo-8), freeing the MPU for Qwen2.5 exclusively.

### Reliability: Systemd services + watchdog
```bash
# Proper installation with enough disk space would create:
/etc/systemd/system/ollama.service
/etc/systemd/system/Minerva-mcp.service
/etc/systemd/system/Minerva-ailoop.service
/etc/systemd/system/Minerva-camera.service
```
All services would auto-restart on failure.

### Notifications: Production Twilio account
Replace sandbox with a registered Twilio WhatsApp Business account and a purchased phone number to enable voice calls and unlimited recipients.

### Multi-device: Distributed fleet
Each UNO Q monitors one room or one resident. A central aggregator (e.g. MQTT broker or cloud dashboard) collects incidents from all devices for caregiver oversight.

### Privacy-preserving cloud backup
Sensor data (not images) could be synced to a HIPAA-compliant cloud store (InfluxDB Cloud, AWS Timestream) for long-term trend analysis and anomaly baseline learning — while keeping all images strictly on-device.

---

## Privacy

- Camera frames never leave the device. Only Moondream2 consumes them locally.
- No person identification — Moondream2 describes scenes, not individuals.
- No audio recording.
- Sensor data is stored locally in InfluxDB on the device.

---

## Repository Structure

```
/home/arduino/
├── .env                  # credentials (not committed)
├── mcp_server.py         # MCP server — tools implementation
├── ai_loop.py            # AI orchestrator — Flask + Qwen2.5
├── camera_worker.py      # Camera loop — Moondream2
├── incidents.log         # local incident log
└── venv/                 # Python virtual environment
```

---

## Team

Built at HackUPC 2026 with Arduino and Qualcomm hardware.
