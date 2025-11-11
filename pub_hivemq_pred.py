#!/usr/bin/env python3
# pub_hivemq_pred.py
# Publica mensajes simulados de predicción NILM en HiveMQ Cloud (TLS)
# Requiere: pip install paho-mqtt

import time
import json
import random
from paho.mqtt import client as mqtt

# ---------------- CONFIG ----------------
BROKER     = "8c8fef4b1fe449129461c0141702e0cf.s1.eu.hivemq.cloud"
PORT       = 8883                # TLS
USERNAME   = "BL441BW"
PASSWORD   = "5Z6y4LR@$a"
CLIENT_ID  = "simulator_pred_001"

TOPIC_L1   = "sensors/adw220/dev01/pred/L1"
TOPIC_L2   = "sensors/adw220/dev01/pred/L2"

PUBLISH_INTERVAL = 4.0           # segundos
QOS = 1
RETAIN = False

# Clases posibles
CLASSES = ["NoLoad", "PC", "Motor", "Cargador", "Iluminación"]
# ---------------------------------------

def on_connect(client, userdata, flags, rc):
    print("Conectado OK" if rc == 0 else f"Fallo al conectar rc={rc}")

def on_disconnect(client, userdata, rc):
    print(f"Desconectado rc={rc}")

def simulate_prediction(phase: str):
    """Genera un payload tipo NILM para una fase"""
    cls = random.choice(CLASSES)
    conf = round(random.uniform(0.4, 0.98), 2)
    payload = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "phase": phase,
        "class": cls,
        "confidence": conf
    }
    return payload

def main():
    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)
    client.username_pw_set(USERNAME, PASSWORD)
    client.tls_set()
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    print(f"Conectando a HiveMQ Cloud {BROKER}:{PORT} ...")
    client.connect(BROKER, PORT, keepalive=60)
    client.loop_start()

    try:
        while True:
            payload_L1 = simulate_prediction("L1")
            payload_L2 = simulate_prediction("L2")

            msg_L1 = json.dumps(payload_L1)
            msg_L2 = json.dumps(payload_L2)

            print(f"[PUB] {TOPIC_L1} -> {msg_L1}")
            client.publish(TOPIC_L1, msg_L1, qos=QOS, retain=RETAIN)

            print(f"[PUB] {TOPIC_L2} -> {msg_L2}")
            client.publish(TOPIC_L2, msg_L2, qos=QOS, retain=RETAIN)

            time.sleep(PUBLISH_INTERVAL)
    except KeyboardInterrupt:
        print("\nInterrumpido por usuario. Cerrando conexión ...")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    main()
