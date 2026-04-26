import os
import json
import base64
import requests
import cv2
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from fastmcp import Client
import asyncio
import threading
import time
from datetime import datetime

load_dotenv("/home/arduino/.env")

OLLAMA_URL      = "http://localhost:11434/api/chat"
QWEN_MODEL      = "qwen2.5:1.5b"
MOONDREAM_MODEL = "moondream"
MCP_URL         = "http://localhost:8000/mcp"

app = Flask(__name__)
ollama_lock    = threading.Lock()
alert_priority = threading.Event()  # cuando está activo, cámara cede paso

# ── System prompt Qwen2.5 ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a safety monitoring system for an elderly person living alone.
You receive a JSON alert and must call the appropriate tools.

Input fields:
- alert_type: 'temperature' | 'inactivity' | 'fall'
- severity: 1 (low) | 2 (medium) | 3 (high)
- sensor_data: dict with sensor values, may be empty

Available tools and their EXACT required arguments:

1. send_whatsapp_alert(alert_type, message, severity)
   - alert_type: 'fall' | 'inactivity' | 'temperature'
   - message: string in Spanish describing the situation
   - severity: 'low' | 'medium' | 'high'

2. activate_local_alarm(mode)
   - mode: 'alert' | 'emergency' | 'off'

Rules:
- ALWAYS call send_whatsapp_alert for every alert
- If severity=3 OR alert_type='fall': also call activate_local_alarm(mode='emergency')
- If severity=2 AND alert_type='temperature': also call activate_local_alarm(mode='alert')
- Never mix arguments between tools"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "send_whatsapp_alert",
            "description": "Send a WhatsApp alert to the family",
            "parameters": {
                "type": "object",
                "properties": {
                    "alert_type": {"type": "string", "enum": ["fall", "inactivity", "temperature"]},
                    "message":    {"type": "string"},
                    "severity":   {"type": "string", "enum": ["low", "medium", "high"]}
                },
                "required": ["alert_type", "message", "severity"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "activate_local_alarm",
            "description": "Activate LEDs and buzzer on the device",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": ["alert", "emergency", "off"]}
                },
                "required": ["mode"]
            }
        }
    }
]

# ── MCP client ────────────────────────────────────────────────────────────────

async def execute_tool_mcp(name: str, args: dict) -> str:
    async with Client(MCP_URL) as client:
        result = await client.call_tool(name, args)
        return result.data if hasattr(result, "data") else str(result)


def execute_tool(name: str, args: dict) -> str:
    return asyncio.run(execute_tool_mcp(name, args))

# ── Qwen2.5 ───────────────────────────────────────────────────────────────────

def severity_to_str(severity: int) -> str:
    return {1: "low", 2: "medium", 3: "high"}.get(severity, "high")


def call_qwen(alert: dict) -> list:
    user_message = json.dumps({
        "alert_type":     alert.get("alert_type"),
        "severity":       alert.get("severity"),
        "severity_label": severity_to_str(alert.get("severity", 1)),
        "sensor_data":    alert.get("sensor_data", {})
    })
    with ollama_lock:
        response = requests.post(OLLAMA_URL, json={
            "model":    QWEN_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_message}
            ],
            "tools":  TOOLS,
            "stream": False
        }, timeout=1500)
    return response.json().get("message", {}).get("tool_calls", [])


REQUIRED_ARGS = {
    "send_whatsapp_alert": {"alert_type", "message", "severity"},
    "activate_local_alarm": {"mode"},
}


def validate(tool_calls: list) -> bool:
    for tc in tool_calls:
        fn   = tc.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments", {})
        if name not in REQUIRED_ARGS:
            return False
        if not isinstance(args, dict):
            return False
        if not REQUIRED_ARGS[name].issubset(args.keys()):
            return False
    return True


def handle_alert(alert: dict) -> dict:
    alert_priority.set()  # señal: cámara cede paso
    print(f"\n[ALERT] {alert}")
    try:
        for attempt in range(2):
            tool_calls = call_qwen(alert)
            if tool_calls and validate(tool_calls):
                results = []
                for tc in tool_calls:
                    fn     = tc["function"]
                    name   = fn["name"]
                    args   = fn["arguments"]
                    print(f"  → {name}({args})")
                    result = execute_tool(name, args)
                    print(f"  ← {result}")
                    results.append({"tool": name, "result": result})
                return {"status": "ok", "tools_executed": results}
            print(f"  [WARNING] Intento {attempt + 1} fallido: {tool_calls}")

        # Fallback
        print("  [FALLBACK] Enviando WhatsApp de emergencia")
        result = execute_tool("send_whatsapp_alert", {
            "alert_type": alert.get("alert_type", "unknown"),
            "message":    "Alerta detectada. Revisar dispositivo urgentemente.",
            "severity":   "high"
        })
        return {"status": "fallback", "result": result}
    finally:
        alert_priority.clear()  # cámara puede volver

# ── Moondream2 ────────────────────────────────────────────────────────────────

MOONDREAM_PROMPT = (
    "Look at this image carefully. "
    "Is there a person visible? If yes, are they standing, sitting, or lying on the floor? "
    "Answer only with one of: 'standing', 'sitting', 'fallen', 'no person'."
)


def capture_frame() -> str | None:
    cap = cv2.VideoCapture("/dev/video0")
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    _, buffer = cv2.imencode(".jpg", frame)
    return base64.b64encode(buffer).decode("utf-8")


def call_moondream(image_b64: str) -> str:
    with ollama_lock:
        response = requests.post(OLLAMA_URL, json={
            "model":    MOONDREAM_MODEL,
            "messages": [{
                "role":    "user",
                "content": MOONDREAM_PROMPT,
                "images":  [image_b64]
            }],
            "stream": False
        }, timeout=120)
    return response.json().get("message", {}).get("content", "").strip().lower()


def handle_camera() -> dict:
    if alert_priority.is_set():
        print("\n[CAMERA] Alerta en curso, saltando ciclo")
        return {"status": "skipped", "reason": "alert in progress"}

    print("\n[CAMERA] Capturando frame...")
    image_b64 = capture_frame()
    if not image_b64:
        return {"status": "error", "reason": "no se pudo capturar frame"}

    if alert_priority.is_set():
        print("\n[CAMERA] Alerta detectada antes de inferencia, saltando")
        return {"status": "skipped", "reason": "alert in progress"}

    description = call_moondream(image_b64)
    print(f"  [MOONDREAM] {description}")

    if "fallen" in description:
        alert = {
            "alert_type":  "fall",
            "severity":    3,
            "sensor_data": {"description": description}
        }
        return handle_alert(alert)

    return {"status": "ok", "description": description, "action": "none"}

# ── Endpoints HTTP ────────────────────────────────────────────────────────────

@app.route("/alert", methods=["POST"])
def alert_endpoint():
    alert = request.get_json()
    if not alert or "alert_type" not in alert or "severity" not in alert:
        return jsonify({"error": "JSON inválido. Requiere alert_type y severity"}), 400
    mapping = {"bathroom_timeout": "inactivity"}
    alert["alert_type"] = mapping.get(alert["alert_type"], alert["alert_type"])
    result = handle_alert(alert)
    return jsonify(result)


@app.route("/camera", methods=["POST"])
def camera_endpoint():
    result = handle_camera()
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def camera_loop():
    while True:
        try:
            handle_camera()
        except Exception as e:
            print(f"[ERROR] Cámara: {e}")
        time.sleep(30)


if __name__ == "__main__":
    print("[GUARDIAN] ai_loop arrancado en http://0.0.0.0:5000")
    # t = threading.Thread(target=camera_loop, daemon=True)
    # t.start()
    app.run(host="0.0.0.0", port=5000)
