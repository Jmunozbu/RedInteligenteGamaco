#!/usr/bin/env python3
# backend/infer_from_phase.py
# Inferencia NILM por fase (L1/L2) desde HiveMQ → publica a /pred/L1 y /pred/L2

import os, json, time
from datetime import datetime, timezone
from collections import deque, Counter

import numpy as np
import joblib
import paho.mqtt.client as mqtt

# ---------------- Config (hardcoded con override por ENV) ----------------
MQTT_HOST   = os.getenv("MQTT_HOST", "8c8fef4b1fe449129461c0141702e0cf.s1.eu.hivemq.cloud")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "8883"))  # TLS
MQTT_USER   = os.getenv("MQTT_USER", "BL441BW")
MQTT_PASS   = os.getenv("MQTT_PASS", "5Z6y4LR@$a")
CLIENT_ID   = os.getenv("MQTT_CLIENT_ID", "bl441bw_nilm_infer_01")

TOPIC_L1_IN = os.getenv("TOPIC_L1_IN", "sensors/adw220/dev01/L1")
TOPIC_L2_IN = os.getenv("TOPIC_L2_IN", "sensors/adw220/dev01/L2")
OUT_PREFIX  = os.getenv("OUT_PREFIX",  "sensors/adw220/dev01/pred")

MODEL_PATH  = os.getenv("MODEL_PATH", "nilm_model_xgb.joblib")

WINDOW_SIZE    = int(os.getenv("WINDOW_SIZE", "3"))     # votos
CONSENSUS_FRAC = float(os.getenv("CONSENSUS_FRAC", "1.0"))
MIN_CONF       = float(os.getenv("MIN_CONF", "0.70"))

# ---------------- Carga modelo ----------------
bundle  = joblib.load(MODEL_PATH)
model   = bundle["model"]
scaler  = bundle["scaler"]
encoder = bundle["encoder"]
FEATURES= bundle["features"]   # e.g. ["FREQ","I","P","PF","Q","S","THDi","U"]

CLASSES = list(encoder.classes_)
print(f"[INIT] Modelo: {MODEL_PATH}  Clases: {CLASSES}  FEATURES: {FEATURES}")

# ---------------- Helpers ----------------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def build_feature_vector(payload: dict) -> np.ndarray:
    # Mapea por nombre EXACTO de FEATURES en el JSON entrante
    vec = []
    for k in FEATURES:
        v = payload.get(k)
        vec.append(float(v) if v is not None else np.nan)
    return np.array(vec, dtype=float)

def infer_one(js: dict, phase: str):
    x_raw = build_feature_vector(js)
    if np.isnan(x_raw).any():
        # Si falta algo, evitamos publicar; ligero y silencioso
        return None
    Xs = scaler.transform([x_raw])
    proba = model.predict_proba(Xs)[0]
    idx   = int(np.argmax(proba))
    cls   = encoder.inverse_transform([idx])[0]
    conf  = float(proba[idx])
    ts    = js.get("ts") or now_iso()
    return {"ts": ts, "phase": phase, "class": cls, "confidence": conf}

# Estado de ventanas por fase (para consenso)
wins = {"L1": deque(maxlen=WINDOW_SIZE), "L2": deque(maxlen=WINDOW_SIZE)}
last_pub = {"L1": None, "L2": None}

def maybe_publish(client, phase, res):
    if not res: return
    wins[phase].append((res["class"], res["confidence"]))
    labels = [c for c,_ in wins[phase]]
    top, cnt = Counter(labels).most_common(1)[0]
    frac = cnt / len(labels)
    mean_conf = float(np.mean([c for cl,c in wins[phase] if cl == top]))
    if frac >= CONSENSUS_FRAC and mean_conf >= MIN_CONF and last_pub[phase] != top:
        out_topic = f"{OUT_PREFIX}/{phase}"
        out = {"ts": res["ts"], "phase": phase, "class": top, "confidence": mean_conf}
        client.publish(out_topic, json.dumps(out), qos=0, retain=False)
        last_pub[phase] = top
        print(f"[PUB] {out_topic} -> {top} ({mean_conf:.2f})")

# ---------------- MQTT ----------------
def on_connect(c, u, f, rc):
    if rc == 0:
        print(f"[MQTT] Conectado a {MQTT_HOST}:{MQTT_PORT}")
        c.subscribe([(TOPIC_L1_IN, 0), (TOPIC_L2_IN, 0)])
    else:
        print(f"[MQTT] rc={rc}")

def on_message(c, u, msg):
    try:
        js = json.loads(msg.payload.decode("utf-8", "ignore"))
    except Exception:
        return
    phase = "L1" if msg.topic.endswith("/L1") else ("L2" if msg.topic.endswith("/L2") else None)
    if phase is None: return
    res = infer_one(js, phase)
    maybe_publish(c, phase, res)

def run():
    c = mqtt.Client(client_id=CLIENT_ID, clean_session=True, protocol=mqtt.MQTTv311)
    c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.tls_set()  # TLS por defecto (certs del sistema)
    c.on_connect = on_connect
    c.on_message = on_message

    backoff = 2
    while True:
        try:
            c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            c.loop_forever()
        except Exception as e:
            print(f"[MQTT] desconectado: {e}. Reintentando en {backoff}s…")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)

if __name__ == "__main__":
    run()
