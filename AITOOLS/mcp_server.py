import os
import json
import socket
import struct
import msgpack
from datetime import datetime
from fastmcp import FastMCP
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv("/home/arduino/.env")

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM = os.environ["TWILIO_FROM"]
TWILIO_TO = os.environ["TWILIO_TO"]
ARDUINO_SOCKET = os.environ["ARDUINO_SOCKET"]

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
mcp = FastMCP("guardian-mcp")

@mcp.tool()
def send_whatsapp_alert(alert_type: str, message: str, severity: str) -> str:
    """
    Send a WhatsApp alert to the family.
    alert_type: 'fall' | 'inactivity' | 'temperature'
    severity: 'low' | 'medium' | 'high'
    """
    if severity == "high":
        header = "ALERTA URGENTE"
    elif severity == "medium":
        header = "AVISO"
    else:
        header = "NOTIFICACIÓN"

    type_labels = {
        "fall":        "Caída detectada",
        "inactivity":  "Inactividad prolongada",
        "temperature": "Temperatura anómala",
    }

    body = (
        f"{header} — SISTEMA GUARDIAN\n"
        f"Tipo: {type_labels.get(alert_type, alert_type)}\n"
        f"Detalle: {message}\n"
        f"Hora: {datetime.now().strftime('%H:%M:%S')}"
    )

    msg = twilio_client.messages.create(from_=TWILIO_FROM, to=TWILIO_TO, body=body)
    return f"WhatsApp enviado. SID: {msg.sid}"

@mcp.tool()
def activate_local_alarm(mode: str) -> str:
    """
    Activate LEDs and buzzer on the device.
    mode: 'alert' (yellow LEDs + short beep) | 'emergency' (red LEDs + continuous beep) | 'off'
    """
    if mode == "alert":
        command = {"cmd": "alarm", "color": [255, 165, 0], "buzzer": 1, "duration": 3}
    elif mode == "emergency":
        command = {"cmd": "alarm", "color": [255, 0, 0], "buzzer": 2, "duration": 10}
    else:
        command = {"cmd": "alarm", "color": [0, 0, 0], "buzzer": 0, "duration": 0}

    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(ARDUINO_SOCKET)
        payload = msgpack.packb(command)
        sock.sendall(struct.pack(">I", len(payload)) + payload)
        sock.close()
        return f"Alarma activada en modo: {mode}"
    except Exception as e:
        return f"Error activando alarma: {e}"

if __name__ == "__main__":
	mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)

