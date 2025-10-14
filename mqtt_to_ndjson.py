#!/usr/bin/env python3
# mqtt_to_ndjson.py
import os, json, socket, hashlib
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

import paho.mqtt.client as mqtt

from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

load_dotenv()

# ---- Entradas/Salidas ----
MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
OUT_DIR      = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))

# ---- MQTT (con autenticación) ----
MQTT_HOST      = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
MQTT_SUB       = os.getenv("MQTT_SUB", "sensors/adw220/+/raw")
MQTT_USER      = os.getenv("MQTT_USER", "")
MQTT_PASS      = os.getenv("MQTT_PASS", "")
MQTT_QOS       = int(os.getenv("MQTT_QOS", "0"))
CLIENT_ID_SUB  = os.getenv("MQTT_CLIENT_ID_SUB", f"adw_rtu_sub_{socket.gethostname()}")

# ---- Salida de formato ----
OUTPUT_MODE   = os.getenv("OUTPUT_MODE", "wide").lower()  # "wide" | "tidy"

# Carga mapping
mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
meta_default_word_order = mapping["meta"].get("byte_order", "ABCD").upper()  # ABCD/CDAB/BADC/DCBA
reg_list = mapping["registers"]
meta_by_addr = { int(r["addr_dec"]): r for r in reg_list }

# Dedup por device
last_sig_by_dev = {}

def mqtt_connect():
    client = mqtt.Client(client_id=CLIENT_ID_SUB, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return client

def make_decoder(registers, word_order="ABCD"):
    wo = word_order.upper()
    if   wo == "ABCD":
        byteorder, wordorder = Endian.BIG, Endian.BIG
    elif wo == "CDAB":
        byteorder, wordorder = Endian.BIG, Endian.LITTLE
    elif wo == "BADC":
        byteorder, wordorder = Endian.LITTLE, Endian.BIG
    elif wo == "DCBA":
        byteorder, wordorder = Endian.LITTLE, Endian.LITTLE
    else:
        byteorder, wordorder = Endian.BIG, Endian.BIG
    return BinaryPayloadDecoder.fromRegisters(registers, byteorder=byteorder, wordorder=wordorder)

def decode_value(words, word_order, dtype, scale=1.0, offset=0.0):
    # words: lista de enteros 0..65535
    if dtype == "float32":
        dec = make_decoder(words, word_order)
        v = float(dec.decode_32bit_float())
    elif dtype == "uint16":
        # 1 word
        v = int(words[0] & 0xFFFF)
    elif dtype == "int16":
        w = words[0] & 0xFFFF
        v = w - 0x10000 if w & 0x8000 else w
    elif dtype == "uint32":
        if len(words) < 2:
            raise ValueError("uint32 requiere 2 words")
        hi, lo = words[0] & 0xFFFF, words[1] & 0xFFFF
        v = (hi << 16) | lo
    elif dtype == "int32":
        if len(words) < 2:
            raise ValueError("int32 requiere 2 words")
        hi, lo = words[0] & 0xFFFF, words[1] & 0xFFFF
        u = (hi << 16) | lo
        v = u - 0x100000000 if u & 0x80000000 else u
    else:
        # fallback: float32
        dec = make_decoder(words, word_order)
        v = float(dec.decode_32bit_float())

    return float(v) * float(scale) + float(offset)

def ensure_day_files(day_str):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta_out = OUT_DIR / f"meta_{day_str}.json"
    if not meta_out.exists():
        meta_out.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

def to_wide_doc(ts, device, labeled_dict):
    return {"ts": ts, "device": device, **labeled_dict}

def to_tidy_doc(ts, device, points):
    return {"ts": ts, "device": device, "measurements": points}

def process_frame(frame: dict):
    """
    frame publisher:
    {
      "ts": "...",
      "slave": 1,
      "func": 4,
      "start_addr": 256,
      "count": 54,
      "word_order": "ABCD",
      "regs": [ ... QUANTITY ints ... ]
    }
    """
    ts = frame.get("ts") or datetime.now(timezone.utc).isoformat()
    device = frame.get("device", "adw220")
    start_addr = int(frame.get("start_addr", 0))
    count = int(frame.get("count", 0))
    regs = frame.get("regs", [])
    word_order = (frame.get("word_order") or meta_default_word_order).upper()

    if not isinstance(regs, list) or len(regs) != count:
        return None  # frame incompleto

    labeled = {}
    points = []

    for addr_dec, meta in meta_by_addr.items():
        idx = addr_dec - start_addr
        words = int(meta.get("words", 2))
        if idx < 0 or (idx + words - 1) >= len(regs):
            continue

        dtype = meta.get("dtype", "float32").lower()
        scale = meta.get("scale", 1.0)
        offset = meta.get("offset", 0.0)

        wordsN = regs[idx:idx+words]
        try:
            val = decode_value(wordsN, word_order, dtype=dtype, scale=scale, offset=offset)
        except Exception:
            continue

        label = meta.get("label", f"R{addr_dec}")
        labeled[label] = val

        if OUTPUT_MODE == "tidy":
            points.append({
                "label": label,
                "value": val,
                "unit": meta.get("unit",""),
                "quantity": meta.get("quantity",""),
                "phase": meta.get("phase",""),
                "phase_index": meta.get("phase_index",0),
                "channel": meta.get("channel",""),
                "group": meta.get("group","electrical_continuous")
            })

    if not labeled:
        return None

    sig = hashlib.sha1(json.dumps(labeled, sort_keys=True).encode()).hexdigest()
    prev = last_sig_by_dev.get(device)
    if prev == sig:
        return None
    last_sig_by_dev[device] = sig

    day = ts[:10]
    ensure_day_files(day)
    out_path = OUT_DIR / f"telemetry_{day}.ndjson"

    doc = to_tidy_doc(ts, device, points) if OUTPUT_MODE == "tidy" else to_wide_doc(ts, device, labeled)

    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False) + "\n")

    return True

# ---- MQTT callbacks ----
def on_message(client, userdata, msg):
    try:
        frame = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return
    process_frame(frame)

def main():
    c = mqtt_connect()
    c.on_message = on_message
    c.subscribe(MQTT_SUB, qos=MQTT_QOS)
    c.loop_forever()

if __name__ == "__main__":
    main()
