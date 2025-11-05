#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inferencia NILM en tiempo real desde el tópico crudo de rtu_to_mqtt_raw.py
- Se suscribe al mismo tópico de entrada (PUB_MQTT_TOPIC / MQTT_TOPIC)
- Decodifica bloques usando meta/mapping_registers.json y WORD_ORDER
- Ventana de consenso + umbral de confianza
- Publica payload minimalista en <OUT_TOPIC_PREFIX>/<fase> (por defecto <IN>/pred/<fase>)
"""

import os, json, struct
from datetime import datetime, timezone
from pathlib import Path
from collections import deque, Counter

import joblib
import numpy as np
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# ==== Modelo / features ====
MODEL_PATH = os.getenv("MODEL_PATH", "nilm_model_xgb.joblib")
bundle  = joblib.load(MODEL_PATH)
model   = bundle["model"]
scaler  = bundle["scaler"]
encoder = bundle["encoder"]
FEATURES = bundle["features"]
CLASSES  = list(encoder.classes_)
print(f"[INIT] Modelo: {MODEL_PATH}; Clases: {CLASSES}; FEATURES: {FEATURES}")

# ==== MQTT ====
MQTT_HOST   = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT   = int(os.getenv("MQTT_PORT", "1883"))
IN_TOPIC    = os.getenv("PUB_MQTT_TOPIC", os.getenv("MQTT_TOPIC", "sensors/adw220/dev01/raw"))
MQTT_USER   = os.getenv("INFER_MQTT_USER", "")
MQTT_PASS   = os.getenv("INFER_MQTT_PASS", "")
MQTT_QOS    = int(os.getenv("INFER_MQTT_QOS", "0"))
CLIENT_ID   = os.getenv("INFER_MQTT_CLIENT_ID", "nilm-infer-from-raw")
OUT_TOPIC_PREFIX = os.getenv("OUT_TOPIC_PREFIX", f"{IN_TOPIC}/pred")

# ==== Debounce / confianza ====
WINDOW_SIZE     = int(os.getenv("WINDOW_SIZE", "3"))          # ventana (p.ej. 3)
CONSENSUS_FRAC  = float(os.getenv("CONSENSUS_FRAC", "1.0"))   # 1.0 = unanimidad
MIN_CONF        = float(os.getenv("MIN_CONF", "0.70"))         # confianza mínima promedio
PRINT_DEBUG     = os.getenv("PRINT_DEBUG", "0") == "1"

# ==== Mapping / Modbus ====
MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
WORD_ORDER   = os.getenv("WORD_ORDER", "ABCD").upper()
FUNC         = int(os.getenv("FUNC", "4"))

mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
REGS = []
for r in mapping.get("registers", []):
    fc = str(r.get("fc", "0x04")).lower()
    if FUNC == 4 and fc != "0x04": continue
    if FUNC == 3 and fc != "0x03": continue
    REGS.append({
        "addr": int(r["addr_dec"]),
        "words": int(r.get("words", 2)),
        "name": r.get("name") or r.get("key") or r.get("label") or f"REG{r['addr_dec']}"
    })
print(f"[INIT] Mapping cargado: {len(REGS)} regs; WORD_ORDER={WORD_ORDER}")

# ==== Decodificadores ====
def now_iso(): return datetime.now(timezone.utc).isoformat()
def u16_to_bytes(word): return struct.pack(">H", word)
def words_to_float32(w_hi, w_lo, order="ABCD"):
    hi = u16_to_bytes(w_hi); lo = u16_to_bytes(w_lo)
    A,B = hi[0],hi[1]; C,D = lo[0],lo[1]
    if order == "ABCD":   b = bytes([A,B,C,D])
    elif order == "BADC": b = bytes([B,A,D,C])
    elif order == "CDAB": b = bytes([C,D,A,B])
    elif order == "DCBA": b = bytes([D,C,B,A])
    else:                 b = bytes([A,B,C,D])
    return struct.unpack(">f", b)[0]
def decode_value(mem, addr, words):
    if words == 1: return float(mem.get(addr, 0))
    hi = mem.get(addr, 0); lo = mem.get(addr+1, 0)
    try: return float(words_to_float32(hi, lo, WORD_ORDER))
    except: return float("nan")
def regs_blocks_to_memory(blocks):
    mem = {}
    for b in blocks:
        start = int(b["start_addr"]); regs = list(b["regs"])
        for i,w in enumerate(regs): mem[start+i] = int(w)
    return mem

# ==== Helpers de mapping / features por fase ====
def find_named_addr(name):
    for r in REGS:
        if r["name"] == name: return r["addr"], r["words"]
    return None, None
def find_phase_addr(base_key, phase):
    candidates = [
        f"{base_key}_{phase}", f"{base_key}{phase}", f"{base_key}-{phase}", f"{base_key} {phase}",
        f"{base_key}_L{phase[-1]}", f"{base_key}_L{phase[-1]}".upper(), f"{base_key}_L{phase[-1]}".lower(),
    ]
    for r in REGS:
        nm = r["name"]
        if nm in candidates: return r["addr"], r["words"]
        if base_key.upper() == "THDi" and ("THDI" in nm.upper()) and (phase in nm.upper() or f"L{phase[-1]}" in nm.upper()):
            return r["addr"], r["words"]
    return None, None
def build_feature_vector_for_phase(mem, phase):
    vec, missing = [], []
    for key in FEATURES:
        addr, words = find_phase_addr(key, phase)
        if addr is None: addr, words = find_named_addr(key)
        if addr is None: missing.append(key); vec.append(float("nan")); continue
        vec.append(float(decode_value(mem, addr, words)))
    return np.array(vec, dtype=float), missing

def infer_once(mem, phase, base_ts):
    x_raw, missing = build_feature_vector_for_phase(mem, phase)
    if missing: raise ValueError(f"Faltan features para {phase}: {missing}")
    Xs = scaler.transform([x_raw])
    proba = model.predict_proba(Xs)[0]
    idx = int(np.argmax(proba))
    cls = encoder.inverse_transform([idx])[0]
    conf = float(proba[idx])
    return {"ts": base_ts or now_iso(), "phase": phase, "class": cls, "confidence": conf}

# ==== Debounce state ====
windows = {"L1": deque(maxlen=WINDOW_SIZE), "L2": deque(maxlen=WINDOW_SIZE)}
last_published = {"L1": None, "L2": None}

def maybe_publish(client, phase, result):
    pred, conf, ts = result["class"], result["confidence"], result["ts"]
    windows[phase].append((pred, conf))
    preds = [p for p,_ in windows[phase]]
    top, cnt = Counter(preds).most_common(1)[0]
    frac = cnt / len(preds)
    mean_conf = np.mean([c for p,c in windows[phase] if p == top])
    if PRINT_DEBUG:
        print(f"[DBG] {phase} win={list(windows[phase])} -> top={top} frac={frac:.2f} mean_conf={mean_conf:.2f}")
    if frac >= CONSENSUS_FRAC and mean_conf >= MIN_CONF and last_published[phase] != top:
        out_topic = f"{OUT_TOPIC_PREFIX}/{phase}"
        payload = {"ts": ts, "phase": phase, "class": top, "confidence": float(mean_conf)}
        client.publish(out_topic, json.dumps(payload, ensure_ascii=False), qos=MQTT_QOS, retain=False)
        last_published[phase] = top
        print(f"[PUB] {out_topic} -> {top} (conf={mean_conf:.2f})")

# ==== MQTT callbacks ====
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print(f"[MQTT] Conectado a {MQTT_HOST}:{MQTT_PORT}")
        client.subscribe([(IN_TOPIC, MQTT_QOS)])
        print(f"[MQTT] Subscrito a: {IN_TOPIC}")
        print(f"[CFG] OUT_TOPIC_PREFIX={OUT_TOPIC_PREFIX} WINDOW_SIZE={WINDOW_SIZE} CONSENSUS_FRAC={CONSENSUS_FRAC} MIN_CONF={MIN_CONF}")
    else:
        print(f"[MQTT] Error de conexión: rc={rc}")

def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8", errors="ignore"))
    except Exception:
        return
    if payload.get("mode") != "blocks" or "blocks" not in payload:
        return
    mem = regs_blocks_to_memory(payload["blocks"])
    base_ts = payload.get("ts")
    for phase in ("L1", "L2"):
        try:
            result = infer_once(mem, phase, base_ts)
        except ValueError:
            continue
        maybe_publish(client, phase, result)

def on_disconnect(client, userdata, rc):
    print(f"[MQTT] Desconectado (rc={rc})")

# ==== Lanzar cliente ====
mqttc = mqtt.Client(client_id=CLIENT_ID, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
if MQTT_USER: mqttc.username_pw_set(MQTT_USER, MQTT_PASS)
mqttc.on_connect = on_connect
mqttc.on_message = on_message
mqttc.on_disconnect = on_disconnect
print(f"[MQTT] Conectando a {MQTT_HOST}:{MQTT_PORT} ...")
mqttc.connect(MQTT_HOST, MQTT_PORT, keepalive=60)

try:
    mqttc.loop_forever()
except KeyboardInterrupt:
    print("\n[SALIDA] Ctrl+C — cerrando …")
    mqttc.disconnect()
