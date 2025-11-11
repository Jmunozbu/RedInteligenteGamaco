#!/usr/bin/env python3
# pub_hivemq.py
# Script simple hardcodeado para publicar a HiveMQ (o cualquier broker MQTT)
# Requiere: pip install paho-mqtt

import time
import json
import sys
from paho.mqtt import client as mqtt

# -------- CONFIG HARD-CODED (modifica aquí si hace falta) --------
BROKER = "8c8fef4b1fe449129461c0141702e0cf.s1.eu.hivemq.cloud"   # broker HiveMQ público (sin auth). Cambia si usas otro.
PORT   = 8883                  # 8883 para TLS
TOPIC  = "sensors/adw220/dev01/L1"
CLIENT_ID = "pc_publisher_juand_001"   # cambia si hace falta
USERNAME = "BL441BW"  # "tu_usuario"  # si tu broker requiere auth, pon aquí
PASSWORD = "5Z6y4LR@$a"  # "tu_pass"
QOS = 1
RETAIN = False
PUBLISH_INTERVAL = 5.0  # segundos (0.0 hace un solo publish y sale)
# ----------------------------------------------------------------

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Conectado al broker MQTT OK.")
    else:
        print(f"Fallo al conectar, codigo rc={rc}")

def on_disconnect(client, userdata, rc):
    print("Desconectado (rc=%s)" % rc)

def on_publish(client, userdata, mid):
    print(f"Mensaje publicado (mid={mid})")

def build_payload(counter):
    # Ejemplo JSON hardcodeado con datos tipo ADW220
    payload = {
        "device": "ADW220-dev01",
        "ts": int(time.time()),
        "phase": "L1",
        "FREQ": 60.0,
        "I": round(0.5 + 0.1 * (counter % 5), 4),
        "P": round(10.0 + 0.5 * (counter % 10), 3),
        "PF": 0.95,
        "THDi": round(2.0 + 0.2 * (counter % 4), 2)
    }
    return json.dumps(payload)

def main(single_shot=False):
    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True)

    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    client.tls_set()

    try:
        client.connect(BROKER, PORT, keepalive=60)
    except Exception as e:
        print("Error conectando al broker:", e)
        sys.exit(1)

    client.loop_start()
    try:
        counter = 0
        if single_shot:
            payload = build_payload(counter)
            print("Publicando (single):", payload)
            (rc, mid) = client.publish(TOPIC, payload, qos=QOS, retain=RETAIN)
            # esperar confirmación si QOS>0
            time.sleep(0.5)
        else:
            print(f"Publicando cada {PUBLISH_INTERVAL}s a {BROKER}:{PORT} -> topic '{TOPIC}'. Ctrl+C para parar.")
            while True:
                payload = build_payload(counter)
                print("Publicando:", payload)
                client.publish(TOPIC, payload, qos=QOS, retain=RETAIN)
                counter += 1
                time.sleep(PUBLISH_INTERVAL)
    except KeyboardInterrupt:
        print("\nInterrupción por usuario, cerrando...")
    finally:
        client.loop_stop()
        client.disconnect()

if __name__ == "__main__":
    # Si llamas: python pub_hivemq.py once -> publica una vez y sale
    if len(sys.argv) > 1 and sys.argv[1].lower() in ("once", "one", "single"):
        main(single_shot=True)
    else:
        main(single_shot=False)
