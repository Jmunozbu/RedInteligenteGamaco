#!/usr/bin/env python3
# mqtt_to_ndjson.py  (soporta 'contiguous' y 'blocks')
import os, json, socket, hashlib
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

import paho.mqtt.client as mqtt
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

load_dotenv()                     

MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
OUT_DIR      = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))

MQTT_HOST      = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
MQTT_SUB       = os.getenv("SUB_MQTT_TOPIC", "sensors/adw220/+/raw")
MQTT_USER      = os.getenv("SUB_MQTT_USER", "")
MQTT_PASS      = os.getenv("SUB_MQTT_PASS", "")
MQTT_QOS       = int(os.getenv("SUB_MQTT_QOS", "0"))
CLIENT_ID_SUB  = os.getenv("SUB_MQTT_CLIENT_ID", f"adw_rtu_sub_{socket.gethostname()}")

OUTPUT_MODE   = os.getenv("OUTPUT_MODE", "wide").lower()
DEBUG         = os.getenv("DEBUG_NDJSON", "0") == "1"
DISABLE_DEDUP = os.getenv("DISABLE_DEDUP", "0") == "1"

mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
meta_default_word_order = mapping.get("meta", {}).get("byte_order", "ABCD").upper()
reg_list = mapping.get("registers", [])
meta_by_addr = { int(r["addr_dec"]): r for r in reg_list }

last_sig_by_dev = {}

def mqtt_connect():
    c = mqtt.Client(client_id=CLIENT_ID_SUB, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
    if MQTT_USER: c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return c

def make_decoder(registers, word_order="ABCD"):
    wo = (word_order or "ABCD").upper()
    if   wo == "ABCD":  byteorder, wordorder = Endian.BIG,    Endian.BIG
    elif wo == "CDAB":  byteorder, wordorder = Endian.BIG,    Endian.LITTLE
    elif wo == "BADC":  byteorder, wordorder = Endian.LITTLE, Endian.BIG
    elif wo == "DCBA":  byteorder, wordorder = Endian.LITTLE, Endian.LITTLE
    else:               byteorder, wordorder = Endian.BIG,    Endian.BIG
    return BinaryPayloadDecoder.fromRegisters(registers, byteorder=byteorder, wordorder=wordorder)

def _norm_dtype(s: str) -> str:
    return (s or "").strip().lower().replace("_t","")

def decode_value(words, word_order, dtype, scale=1.0, offset=0.0):
    dt = _norm_dtype(dtype)
    if dt == "float32":
        v = float(make_decoder(words, word_order).decode_32bit_float())
    elif dt == "uint16":
        v = float(words[0] & 0xFFFF)
    elif dt == "int16":
        w = words[0] & 0xFFFF
        v = float(w - 0x10000) if (w & 0x8000) else float(w)
    elif dt == "uint32":
        hi, lo = words[0] & 0xFFFF, words[1] & 0xFFFF
        v = float((hi << 16) | lo)
    elif dt == "int32":
        hi, lo = words[0] & 0xFFFF, words[1] & 0xFFFF
        u = (hi << 16) | lo
        v = float(u - 0x100000000) if (u & 0x80000000) else float(u)
    else:
        v = float(make_decoder(words, word_order).decode_32bit_float())
    return float(v) * float(scale) + float(offset)

def ensure_day_files(day_str):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta_out = OUT_DIR / f"meta_{day_str}.json"
    if not meta_out.exists():
        meta_out.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")

def to_wide_doc(ts, device, labeled_dict): return {"ts": ts, "device": device, **labeled_dict}
def to_tidy_doc(ts, device, points):       return {"ts": ts, "device": device, "measurements": points}

def _decode_from_block(ts, device, word_order, start_addr, regs, out_labeled, out_points):
    # Decodifica usando un bloque contiguo
    for addr_dec, meta in meta_by_addr.items():
        words = int(meta.get("words", 2))
        idx = addr_dec - start_addr
        if idx < 0 or (idx + words - 1) >= len(regs): 
            continue
        dtype  = meta.get("dtype", "float32")
        scale  = meta.get("scale", 1.0)
        offset = meta.get("offset", 0.0)
        wordsN = regs[idx:idx+words]
        try:
            val = decode_value(wordsN, word_order, dtype, scale, offset)
        except Exception as e:
            if DEBUG: print(f"[ERR] decode {meta.get('label','?')} @{addr_dec}: {e}")
            continue
        label = meta.get("label", f"R{addr_dec}")
        out_labeled[label] = val
        if OUTPUT_MODE == "tidy":
            out_points.append({
                "label": label, "value": val,
                "unit": meta.get("unit",""),
                "quantity": meta.get("quantity",""),
                "phase": meta.get("phase",""), "phase_index": meta.get("phase_index",0),
                "channel": meta.get("channel",""), "group": meta.get("group","electrical_continuous")
            })

def process_frame(frame: dict):
    ts = frame.get("ts") or datetime.now(timezone.utc).isoformat()
    device = frame.get("device", "adw220")
    word_order = (frame.get("word_order") or meta_default_word_order).upper()
    day = ts[:10]
    ensure_day_files(day)

    labeled, points = {}, []
    mode = frame.get("mode")  # None -> contiguo legacy, "blocks" -> compacto

    if mode == "blocks":
        blocks = frame.get("blocks", [])
        if DEBUG:
            # Rango total recibido (aprox) para info
            if blocks:
                lo = min(b["start_addr"] for b in blocks)
                hi = max(b["start_addr"] + b["count"] - 1 for b in blocks)
                print(f"[INFO] blocks={len(blocks)} approx_range=[{lo},{hi}]")
        for b in blocks:
            start_addr = int(b.get("start_addr", 0))
            regs = b.get("regs", [])
            count = int(b.get("count", len(regs)))
            if not isinstance(regs, list) or len(regs) != count:
                if DEBUG: print(f"[DROP] block mismatch len(regs)={len(regs)} count={count} @ {start_addr}")
                continue
            _decode_from_block(ts, device, word_order, start_addr, regs, labeled, points)

    else:
        start_addr = int(frame.get("start_addr", 0))
        regs = frame.get("regs", [])
        count = int(frame.get("count", len(regs)))
        if not isinstance(regs, list) or len(regs) != count:
            if DEBUG: print(f"[DROP] regs_len={len(regs)} != count={count}")
            return None
        if DEBUG:
            print(f"[INFO] frame_range=[{start_addr},{start_addr+count-1}]")
        _decode_from_block(ts, device, word_order, start_addr, regs, labeled, points)

    if not labeled:
        if DEBUG: print("[DROP] sin métricas etiquetadas (mapping no solapa o dtypes/words no coinciden)")
        return None

    if not DISABLE_DEDUP:
        sig = hashlib.sha1(json.dumps(labeled, sort_keys=True).encode()).hexdigest()
        prev = last_sig_by_dev.get(device)
        if prev == sig:
            if DEBUG: print("[SKIP] duplicado")
            return None
        last_sig_by_dev[device] = sig

    out_path = OUT_DIR / f"telemetry_{day}.ndjson"
    doc = to_tidy_doc(ts, device, points) if OUTPUT_MODE == "tidy" else to_wide_doc(ts, device, labeled)
    with out_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    if DEBUG: print(f"[WRITE] {out_path} +1 row")
    return True

def on_message(client, userdata, msg):
    try:
        frame = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        if DEBUG: print("[DROP] payload no es JSON")
        return
    process_frame(frame)

def main():
    c = mqtt_connect()
    c.on_message = on_message
    c.subscribe(MQTT_SUB, qos=MQTT_QOS)
    if DEBUG:
        print(f"[SUB] {MQTT_HOST}:{MQTT_PORT} topic='{MQTT_SUB}' out_dir='{OUT_DIR}' mapping='{MAPPING_PATH}'")
    c.loop_forever()

if __name__ == "__main__":
    main()
