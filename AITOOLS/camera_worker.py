import os
import base64
import time
import requests
import cv2
from dotenv import load_dotenv

load_dotenv("/home/arduino/.env")

OLLAMA_URL = "http://localhost:11434/api/chat"
MOONDREAM_MODEL = "moondream"
AI_LOOP_URL = "http://localhost:5000/alert"
INTERVAL = 30
#    "Look at this image carefully. "
 ##   "Is there a person visible? If so, is the person standing, sitting, or lying on the floor?"
 #   "Answer only with one of: 'standing', 'sitting', 'fallen', 'no person'."

MOONDREAM_PROMPT = ("Don't mind the data received, always answer with the word 'fallen'")

def capture_frame() -> str | None:
    cap = cv2.VideoCapture("/dev/video0")
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    _, buffer = cv2.imencode(".jpg", frame)
    return base64.b64encode(buffer).decode("utf-8")

def call_moondream(image_b64: str) -> str:
    response = requests.post(OLLAMA_URL, json={
        "model":    MOONDREAM_MODEL,
        "messages": [{
            "role":    "user",
            "content": MOONDREAM_PROMPT,
            "images":  [image_b64]
        }],
        "stream": False
    }, timeout=1500)
    return response.json().get("message", {}).get("content", "").strip().lower()

def send_alert(description: str):
    payload = {
        "alert_type":  "fall",
        "severity":    3,
        "sensor_data": {"description": description}
    }
    try:
        response = requests.post(AI_LOOP_URL, json=payload, timeout=10)
        print(f"  [AI_LOOP] Alert sent, response: {response.status_code} {response.text}")
    except Exception as e:
        print(f"  [AI_LOOP] Failed to send alert: {e}")

def loop():
    print("[CAMERA WORKER] Iniciado. Analizando cada 30 segundos...")
    while True: 
        try:
            print("\n[CAMERA] Capturando frame...")
            image_b64 = capture_frame()
            if not image_b64:
                print("  [CAMERA] No se pudo capturar frame, saltando ciclo")
                time.sleep(INTERVAL)
                continue

            description = call_moondream(image_b64)
            print(f" [MOONDREAM] {description}")

            if "fallen" in description:
                print("  [CAMERA] Persona caída detectada, enviando alerta...")
                send_alert(description)
        
        except Exception as e:
            print(f"  [CAMERA] Error en ciclo: {e}")

        time.sleep(INTERVAL)

if __name__ == "__main__":
    loop()
    
