#!/usr/bin/env python3
# adw_to_hivemq.py
# Lee Modbus (R10 como RTU-over-TCP) y publica a HiveMQ Cloud (TLS) en:
# - Tópico raw (frame por bloques) y/o
# - Tópicos por línea (L1/L2) en payload plano {U,I,P,Q,S,PF,THDi,FREQ}

import os, json, time, socket
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from pymodbus.client.tcp import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

import paho.mqtt.client as mqtt

load_dotenv()

# -------------------- Modbus / R10 --------------------
R10_IP       = os.getenv("R10_IP", "192.168.3.100")
R10_PORT     = int(os.getenv("R10_PORT", "502"))
SLAVE_ID     = int(os.getenv("SLAVE_ID", "2"))
FUNC         = int(os.getenv("FUNC", "4"))              # 4=Input, 3=Holding
WORD_ORDER   = os.getenv("WORD_ORDER", "ABCD").upper()  # ABCD/CDAB/BADC/DCBA
POLL_SEC     = float(os.getenv("POLL_SEC", "1"))

# -------------------- MQTT (HiveMQ Cloud u otro) --------------------
MQTT_HOST    = os.getenv("PUB_MQTT_HOST", "YOUR_CLUSTER.s1.eu.hivemq.cloud")
MQTT_PORT    = int(os.getenv("PUB_MQTT_PORT", "8883"))  # TLS típico HiveMQ: 8883
MQTT_USER    = os.getenv("PUB_MQTT_USER", "")
MQTT_PASS    = os.getenv("PUB_MQTT_PASS", "")
MQTT_QOS     = int(os.getenv("PUB_MQTT_QOS", "0"))
CLIENT_ID    = os.getenv("PUB_MQTT_CLIENT_ID", f"adw_rtu_bridge_{socket.gethostname()}")
TLS_ENABLE   = os.getenv("PUB_MQTT_TLS", "1") == "1"
TLS_INSECURE = os.getenv("PUB_MQTT_TLS_INSECURE", "0") == "1"
TLS_CA       = os.getenv("PUB_MQTT_TLS_CA", "")  # opcional: ruta a CA específica

# -------------------- Tópicos y modos de publicación --------------------
# Raw trifásico (bloques/regs)
TOPIC_RAW    = os.getenv("TOPIC_RAW", "sensors/adw220/dev01/raw")
PUBLISH_RAW  = os.getenv("PUBLISH_RAW", "1") == "1"

# Tópicos por línea (payload plano)
TOPIC_L1     = os.getenv("TOPIC_L1", "sensors/adw220/dev01/L1")
TOPIC_L2     = os.getenv("TOPIC_L2", "sensors/adw220/dev01/L2")
PUBLISH_PHASE= os.getenv("PUBLISH_PHASE", "1") == "1"

# -------------------- Mapping / lectura por bloques --------------------
MAPPING_PATH     = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
MAX_GAP_TO_MERGE = int(os.getenv("MAX_GAP_TO_MERGE", "0"))
MAX_PER_REQ      = int(os.getenv("MAX_PER_REQ", "110"))  # ≤125 recomendado

# -------------------- Decoder helpers --------------------
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
    else:
        v = float(make_decoder(words, word_order).decode_32bit_float())
    return float(v) * float(scale) + float(offset)

# -------------------- Mapping load y organización --------------------
mapping = {}
meta_by_addr = {}
meta_default_word_order = "ABCD"  # fallback

def load_mapping(path: Path):
    global mapping, meta_by_addr, meta_default_word_order
    if not path.exists():
        raise SystemExit(f"No existe mapping '{path}'.")
    mapping = json.loads(path.read_text(encoding="utf-8"))
    regs = []
    reg_list = mapping.get("registers", [])
    meta_by_addr = { int(r["addr_dec"]): r for r in reg_list }
    meta_default_word_order = mapping.get("meta", {}).get("byte_order", "ABCD").upper()
    for r in reg_list:
        fc = str(r.get("fc","0x04")).lower()
        if FUNC == 4 and fc != "0x04": 
            continue
        if FUNC == 3 and fc != "0x03":
            continue
        regs.append( (int(r["addr_dec"]), int(r.get("words",2))) )
    regs.sort(key=lambda x: x[0])
    if not regs:
        raise SystemExit("No hay registros compatibles con FUNC en el mapping.")
    return regs

def build_min_blocks(regs):
    if not regs: return []
    blocks = []
    cur_start = regs[0][0]
    cur_end   = regs[0][0] + regs[0][1] - 1
    for addr, words in regs[1:]:
        need_start = addr
        need_end   = addr + words - 1
        if need_start <= cur_end + MAX_GAP_TO_MERGE + 1:
            if need_end > cur_end:
                cur_end = need_end
        else:
            blocks.append((cur_start, cur_end - cur_start + 1))
            cur_start, cur_end = need_start, need_end
    blocks.append((cur_start, cur_end - cur_start + 1))
    return blocks

def read_chunked(mb: ModbusTcpClient, start_addr: int, total_count: int, slave_id: int, func: int):
    regs_concat = []
    remaining = total_count
    curr_addr = start_addr
    while remaining > 0:
        this_count = min(remaining, MAX_PER_REQ)
        if func == 4:
            rr = mb.read_input_registers(address=curr_addr, count=this_count, slave=slave_id)
        else:
            rr = mb.read_holding_registers(address=curr_addr, count=this_count, slave=slave_id)
        if rr.isError():
            regs_concat.extend([0]*this_count)
        else:
            regs = list(getattr(rr, "registers", []))
            if len(regs) != this_count:
                regs = regs + [0]*max(0, this_count - len(regs))
            regs_concat.extend(regs)
        curr_addr += this_count
        remaining -= this_count
    return regs_concat

# -------------------- Decodificación por bloques → labels --------------------
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
            # Si el frame trae "word_order" especial, respétalo, si no el del mapping
            wo = WORD_ORDER or meta_default_word_order
            val = decode_value(wordsN, wo, dtype, scale, offset)
        except Exception:
            continue
        label = meta.get("label", f"R{addr_dec}")
        out_labeled[label] = val

def blocks_frame_to_labeled(frame_word_order: str, blocks):
    labeled = {}
    for b in blocks:
        start_addr = int(b["start_addr"])
        regs = b["regs"]
        _decode_from_block(frame_word_order, start_addr, regs, labeled)
    return labeled

# -------------------- Payloads por línea --------------------
FIELDS_L1 = {
    "U_L1": "U",
    "I_L1": "I",
    "P_L1": "P",
    "Q_L1": "Q",
    "S_L1": "S",
    "PF_L1": "PF",
    "THDi_L1": "THDi",
    "FREQ": "FREQ",
}
FIELDS_L2 = {
    "U_L2": "U",
    "I_L2": "I",
    "P_L2": "P",
    "Q_L2": "Q",
    "S_L2": "S",
    "PF_L2": "PF",
    "THDi_L2": "THDi",
    "FREQ": "FREQ",
}

def build_phase_payload(labeled: dict, fmap: dict) -> dict:
    out = {}
    for k_src, k_dst in fmap.items():
        if k_src in labeled:
            out[k_dst] = labeled[k_src]
    # añade ts ISO para visualización humana
    out["ts"] = datetime.now(timezone.utc).isoformat()
    return out

# -------------------- MQTT connect (TLS opcional) --------------------
def mqtt_connect():
    c = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv311, clean_session=True)
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASS)
    if TLS_ENABLE:
        if TLS_CA and Path(TLS_CA).exists():
            c.tls_set(ca_certs=TLS_CA)
        else:
            c.tls_set()  # CA del sistema
        if TLS_INSECURE:
            c.tls_insecure_set(True)
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return c

# -------------------- Main loop --------------------
def main():
    # Cargar mapping y armar bloques mínimos
    needed_regs = load_mapping(MAPPING_PATH)      # [(addr,words),...]
    blocks = build_min_blocks(needed_regs)        # [(start,count),...]

    # Conexión Modbus
    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=1.5)
    assert mb.connect(), f"No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}"

    # Conexión MQTT (HiveMQ)
    mq = mqtt_connect()
    mq.loop_start()

    try:
        while True:
            # 1) Leer todos los bloques en una pasada
            block_payloads = []
            for (start, count) in blocks:
                regs_concat = read_chunked(mb, start, count, SLAVE_ID, FUNC)
                block_payloads.append({
                    "start_addr": start,
                    "count": len(regs_concat),
                    "regs": regs_concat,
                    "func": FUNC
                })

            # 2) Publicar RAW (opcional)
            if PUBLISH_RAW:
                payload_raw = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "device": "adw220",
                    "slave": SLAVE_ID,
                    "word_order": WORD_ORDER,
                    "mode": "blocks",
                    "blocks": block_payloads
                }
                mq.publish(TOPIC_RAW, json.dumps(payload_raw, ensure_ascii=False), qos=MQTT_QOS, retain=False)

            # 3) Decodificar y publicar por línea (opcional)
            if PUBLISH_PHASE:
                labeled = blocks_frame_to_labeled(WORD_ORDER, block_payloads)
                if labeled:
                    pL1 = build_phase_payload(labeled, FIELDS_L1)
                    pL2 = build_phase_payload(labeled, FIELDS_L2)
                    if len(pL1) > 1:
                        mq.publish(TOPIC_L1, json.dumps(pL1, ensure_ascii=False), qos=MQTT_QOS, retain=False)
                    if len(pL2) > 1:
                        mq.publish(TOPIC_L2, json.dumps(pL2, ensure_ascii=False), qos=MQTT_QOS, retain=False)

            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            mq.loop_stop(); mq.disconnect()
        except Exception:
            pass
        mb.close()

if __name__ == "__main__":
    main()
