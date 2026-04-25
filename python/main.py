import time
import threading
import requests
import json
from arduino.app_utils import App, Bridge

LLM_URL = "http://172.18.0.1:11434/api/chat"
MODEL = "qwen2.5:1.5b"

def send_alert(payload, label):
    """Envía la alerta al LLM en un hilo aparte."""
    def run():
        print(f"🤖 [{label}] Enviando alerta: {payload}")
        try:
            response = requests.post(
                LLM_URL,
                json={
                    "model": MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": json.dumps(payload)
                        }
                    ],
                    "stream": False
                },
                timeout=12000
            )
            data = response.json()
            content = data.get("message", {}).get("content", "")
            print(f"🤖 [{label}] Respuesta: {content}")
        except Exception as e:
            print(f"❌ [{label}] Error: {e}")

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

# --- Handlers ---

def on_temp_alert(temp, severity):
    print(f"⚠️  Temperatura fuera de rango: {temp}ºC (severidad {severity})")
    payload = {
        "alert_type": "temperature",
        "severity": severity,
        "sensor_data": {
            "temperature": temp
        }
    }
    send_alert(payload, "TEMP_ALERT")

def on_temp_normal(temp):
    print(f"✅ Temperatura normal: {temp}ºC")

def on_movement(dist):
    print(f"🚶 Movimiento detectado a {dist} mm")

def on_bathroom_timeout():
    print("🚨 Persona lleva demasiado tiempo en el baño!")
    payload = {
        "alert_type": "bathroom_timeout",
        "severity": 3,
        "sensor_data": {}
    }
    send_alert(payload, "BATHROOM_TIMEOUT")

# --- Registro ---

Bridge.provide("temp_alert", on_temp_alert)
Bridge.provide("temp_normal", on_temp_normal)
Bridge.provide("movement_detected", on_movement)
Bridge.provide("bathroom_timeout", on_bathroom_timeout)

def loop():
    time.sleep(1)

App.run(user_loop=loop)