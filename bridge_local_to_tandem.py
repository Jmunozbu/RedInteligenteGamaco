#!/usr/bin/env python3
# bridge_local_to_tandem.py
# - Suscribe a MQTT local (frame trifásico único con blocks/regs)
# - Decodifica con mapping_registers.json (labels U_L1, I_L1, ..., THDi_L1, idem L2)
# - Construye dos mensajes (L1 y L2) con payload plano {U,I,FREQ,PF,P,S,Q,THDi}
# - Publica por WSS al MQTT Gateway de Tandem con el envoltorio {"device-id", "data":{...}}

import os, json, time, socket
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
import paho.mqtt.client as mqtt
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

load_dotenv()

# ---------- MQTT (LOCAL - SUB) ----------
SUB_HOST     = os.getenv("MQTT_HOST", "localhost")
SUB_PORT     = int(os.getenv("MQTT_PORT", "1883"))
SUB_TOPIC    = os.getenv("SUB_MQTT_TOPIC", "sensors/adw220/raw")   # único tópico trifásico
SUB_USER     = os.getenv("SUB_MQTT_USER", "")
SUB_PASS     = os.getenv("SUB_MQTT_PASS", "")
SUB_QOS      = int(os.getenv("SUB_MQTT_QOS", "0"))
SUB_CLIENTID = os.getenv("SUB_MQTT_CLIENT_ID", f"bridge_sub_{socket.gethostname()}")

# ---------- TANDEM (PUB via WSS/TLS) ----------
GW_HOST      = os.getenv("TANDEM_GW_HOST")  # ej: gwxxxx-gw1-....connect.autodesktandem.com
GW_PORT      = int(os.getenv("TANDEM_GW_PORT", "443"))
GW_USER      = os.getenv("TANDEM_GW_USER", "")
GW_PASS      = os.getenv("TANDEM_GW_PASS", "")
GW_TOPIC     = os.getenv("TANDEM_GW_TOPIC", "tandem/gateway")

PUB_CLIENTID = os.getenv("PUB_MQTT_CLIENT_ID", f"bridge_pub_{socket.gethostname()}")
TLS_INSECURE = os.getenv("TLS_INSECURE", "0") == "1"  # para pruebas (relaja verificación)

# ---------- Nombres de Connections (en Tandem) ----------
CONN_L1      = os.getenv("CONN_L1", "TomaL1")
CONN_L2      = os.getenv("CONN_L2", "TomaL2")

# ---------- Mapping ----------
MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
DEFAULT_WORD_ORDER = "ABCD"  # byte/word order para float32

# ---------- Carga mapping ----------
mapping = {}
meta_by_addr = {}
meta_default_word_order = DEFAULT_WORD_ORDER

if MAPPING_PATH.exists():
    try:
        mapping = json.loads(MAPPING_PATH.read_text(encoding="utf-8"))
        reg_list = mapping.get("registers", [])
        meta_by_addr = { int(r["addr_dec"]): r for r in reg_list }
        meta_default_word_order = mapping.get("meta", {}).get("byte_order", DEFAULT_WORD_ORDER).upper()
    except Exception as e:
        print(f"[WARN] No se pudo leer mapping '{MAPPING_PATH}': {e}")
else:
    print(f"[WARN] No existe mapping '{MAPPING_PATH}'. No se podrá decodificar.")

# ---------- Helpers de decodificación ----------
def _norm_dtype(s: str) -> str:
    return (s or "").strip().lower().replace("_t","")

def make_decoder(registers, word_order="ABCD"):
    wo = (word_order or "ABCD").upper()
    if   wo == "ABCD":  byteorder, wordorder = Endian.BIG,    Endian.BIG
    elif wo == "CDAB":  byteorder, wordorder = Endian.BIG,    Endian.LITTLE
    elif wo == "BADC":  byteorder, wordorder = Endian.LITTLE, Endian.BIG
    elif wo == "DCBA":  byteorder, wordorder = Endian.LITTLE, Endian.LITTLE
    else:               byteorder, wordorder = Endian.BIG,    Endian.BIG
    return BinaryPayloadDecoder.fromRegisters(registers, byteorder=byteorder, wordorder=wordorder)

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
    else:  # fallback
        v = float(make_decoder(words, word_order).decode_32bit_float())
    return float(v) * float(scale) + float(offset)

def _decode_from_block(word_order, start_addr, regs, out_labeled):
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
            val = decode_value(wordsN, DEFAULT_WORD_ORDER if word_order == "BIG_ENDIAN" else word_order, dtype, scale, offset)
        except Exception:
            continue
        label = meta.get("label", f"R{addr_dec}")
        out_labeled[label] = val

def frame_to_labeled(frame: dict) -> dict:
    """Devuelve dict con todas las métricas etiquetadas según mapping (U_L1, I_L1, ..., THDi_L2, FREQ, etc.)."""
    labeled = {}
    if not meta_by_addr:
        return labeled

    word_order = (frame.get("word_order") or meta_default_word_order).upper()

    if frame.get("mode") == "blocks":
        for b in frame.get("blocks", []):
            start_addr = int(b.get("start_addr", 0))
            regs = b.get("regs", [])
            count = int(b.get("count", len(regs)))
            if not isinstance(regs, list) or len(regs) != count:
                continue
            _decode_from_block(word_order, start_addr, regs, labeled)
    else:
        start_addr = int(frame.get("start_addr", 0))
        regs  = frame.get("regs", [])
        count = int(frame.get("count", len(regs)))
        if not isinstance(regs, list) or len(regs) != count:
            return {}
        _decode_from_block(word_order, start_addr, regs, labeled)

    return labeled

# ---------- Preparación payloads por línea ----------
# Map de labels → claves planas que espera Tandem (por conexión)
FIELDS_L1 = {
    "U_L1": "U",
    "I_L1": "I",
    "P_L1": "P",
    "Q_L1": "Q",
    "S_L1": "S",
    "PF_L1": "PF",
    "THDi_L1": "THDi",
    "FREQ": "FREQ",   # común
}
FIELDS_L2 = {
    "U_L2": "U",
    "I_L2": "I",
    "P_L2": "P",
    "Q_L2": "Q",
    "S_L2": "S",
    "PF_L2": "PF",
    "THDi_L2": "THDi",
    "FREQ": "FREQ",   # común
}

def build_phase_payload(labeled: dict, fieldmap: dict) -> dict:
    out = {}
    for k_src, k_dst in fieldmap.items():
        if k_src in labeled:
            out[k_dst] = labeled[k_src]
    return out

# ---------- Timestamp helpers ----------
def _now_epoch_ms():
    return int(time.time() * 1000)

def _to_epoch_ms(ts_val):
    # acepta epoch ms/seg o ISO; devuelve epoch ms
    if ts_val is None:
        return _now_epoch_ms()
    if isinstance(ts_val, (int, float)):
        return int(ts_val * 1000) if ts_val < 10_000_000_000 else int(ts_val)
    try:
        dt = datetime.fromisoformat(str(ts_val).replace("Z","+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return _now_epoch_ms()

# ---------- MQTT clients ----------
sub = mqtt.Client(client_id=SUB_CLIENTID, clean_session=True, protocol=mqtt.MQTTv311)
if SUB_USER:
    sub.username_pw_set(SUB_USER, SUB_PASS)

pub = mqtt.Client(client_id=PUB_CLIENTID, transport="websockets")
if GW_USER:
    pub.username_pw_set(GW_USER, GW_PASS)
# TLS
if TLS_INSECURE:
    pub.tls_set(cert_reqs=0)
else:
    pub.tls_set()

def _publish_envelope(conn_name: str, payload: dict, ts):
    if not payload:
        return
    envelope = {
        "device-id": conn_name,     # ← Connection en Tandem (ej. TomaL1 / TomaL2)
        "data": {
            "name": conn_name,      # opcional, mantenlo igual por claridad
            "payload": payload,     # {U,I,FREQ,PF,P,S,Q,THDi}
            "ts": _to_epoch_ms(ts)  # epoch ms
        }
    }
    pub.publish(GW_TOPIC, json.dumps(envelope))
    # print(f"[PUB] {conn_name} -> {payload}")

def on_connect_sub(client, userdata, flags, rc):
    print(f"[SUB] Connected rc={rc} -> {SUB_HOST}:{SUB_PORT}  topic='{SUB_TOPIC}'")
    client.subscribe(SUB_TOPIC, qos=SUB_QOS)

def on_message_sub(client, userdata, msg):
    try:
        data = json.loads(msg.payload.decode("utf-8"))
    except Exception:
        return

    # Caso A: Si ya viene en sobre {'device-id', 'data':{name,payload,ts}} lo dejamos pasar (útil para pruebas)
    if isinstance(data, dict) and "device-id" in data and isinstance(data.get("data"), dict) and "payload" in data["data"]:
        # Validación mínima: publica tal cual
        pub.publish(GW_TOPIC, json.dumps(data))
        return

    # Caso B: frame trifásico crudo (como el que enviaste)
    if isinstance(data, dict) and ("blocks" in data or "regs" in data):
        ts_src = data.get("ts")
        labeled = frame_to_labeled(data)
        if not labeled:
            return

        # Construimos payloads por línea
        payload_L1 = build_phase_payload(labeled, FIELDS_L1)
        payload_L2 = build_phase_payload(labeled, FIELDS_L2)

        # Publicamos cada conexión solo si hay datos
        _publish_envelope(CONN_L1, payload_L1, ts_src)
        _publish_envelope(CONN_L2, payload_L2, ts_src)
        return

    # Caso C: si te llega un wide ya rotulado (poco probable aquí)
    if isinstance(data, dict) and "device" in data:
        dev = str(data.get("device","")).strip()
        ts_src = data.get("ts")
        # Asume que "device" vendrá como "TomaL1"/"TomaL2" si decides usar este camino:
        payload = {k: v for k, v in data.items() if k not in ("ts","device")}
        _publish_envelope(dev, payload, ts_src)
        return

    # Otros esquemas: ignorar
    return

def main():
    # conecta publisher (Tandem Gateway)
    pub.connect(GW_HOST, GW_PORT, keepalive=30)

    # conecta subscriber (local)
    sub.on_connect = on_connect_sub
    sub.on_message = on_message_sub
    sub.connect(SUB_HOST, SUB_PORT, keepalive=30)

    pub.loop_start()
    try:
        sub.loop_forever()
    finally:
        pub.loop_stop()

if __name__ == "__main__":
    main()
