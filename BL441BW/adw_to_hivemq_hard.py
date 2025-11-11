#!/usr/bin/env python3
# adw_to_hivemq_hard.py
# Lee Modbus (R10 como RTU-over-TCP) con mapping y publica a HiveMQ Cloud (TLS)
# en tópicos por línea: sensors/adw220/dev01/L1 y sensors/adw220/dev01/L2
# Requiere: pip install pymodbus paho-mqtt

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

from paho.mqtt import client as mqtt

# -------------------- Modbus / R10 (HARD-CODED) --------------------
R10_IP        = "192.168.3.1"
R10_PORT      = 502
SLAVE_ID      = 2
FUNC          = 4                    # 4=Input, 3=Holding
WORD_ORDER    = "ABCD"               # ABCD/CDAB/BADC/DCBA
POLL_SEC      = 3.0
TIMEOUT_S     = 1.5

# -------------------- MQTT HiveMQ Cloud (HARD-CODED) --------------------
MQTT_HOST     = "8c8fef4b1fe449129461c0141702e0cf.s1.eu.hivemq.cloud"
MQTT_PORT     = 8883                 # TLS
MQTT_USER     = "BL441BW"
MQTT_PASS     = "5Z6y4LR@$a"
MQTT_QOS      = 1
CLIENT_ID     = "bl441bw_adw_bridge_01"
TOPIC_L1      = "sensors/adw220/dev01/L1"
TOPIC_L2      = "sensors/adw220/dev01/L2"
RETAIN        = False

# -------------------- Mapping / lectura por bloques --------------------
MAPPING_PATH      = Path("meta/mapping_registers.json")
MAX_GAP_TO_MERGE  = 0
MAX_PER_REQ       = 110  # <=125 recomendado

# -------------------- Helpers de decodificación --------------------
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

# -------------------- Cargar mapping y preparar lectura --------------------
mapping = {}
meta_by_addr = {}
meta_default_word_order = "ABCD"

def load_mapping(path: Path, func_filter: int):
    global mapping, meta_by_addr, meta_default_word_order
    if not path.exists():
        raise SystemExit(f"❌ No existe mapping '{path.resolve()}'.")

    mapping = json.loads(path.read_text(encoding="utf-8"))
    reg_list = mapping.get("registers", [])
    meta_by_addr = { int(r["addr_dec"]): r for r in reg_list }
    meta_default_word_order = mapping.get("meta", {}).get("byte_order", "ABCD").upper()

    regs = []
    for r in reg_list:
        fc = str(r.get("fc","0x04")).lower()
        if func_filter == 4 and fc != "0x04":
            continue
        if func_filter == 3 and fc != "0x03":
            continue
        regs.append( (int(r["addr_dec"]), int(r.get("words",2))) )

    regs.sort(key=lambda x: x[0])
    if not regs:
        raise SystemExit("❌ Mapping sin registros compatibles con la FUNC seleccionada.")
    return regs

def build_min_blocks(regs):
    if not regs: return []
    blocks = []
    cur_start = regs[0][0]
    cur_end   = regs[0][0] + regs[0][1] - 1
    for addr, words in regs[1:]:
        need_start = addr
        need_end   = addr + words - 1
        # agrupa con pequeño gap permitido
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
        n = min(remaining, MAX_PER_REQ)
        if func == 4:
            rr = mb.read_input_registers(address=curr_addr, count=n, slave=slave_id)
        else:
            rr = mb.read_holding_registers(address=curr_addr, count=n, slave=slave_id)
        if not rr or (hasattr(rr, "isError") and rr.isError()):
            regs_concat.extend([0] * n)
        else:
            regs = list(getattr(rr, "registers", []) or [])
            if len(regs) < n:
                regs += [0] * (n - len(regs))
            regs_concat.extend(regs)
        curr_addr += n
        remaining -= n
    return regs_concat

# Decodifica los labels definidos en el mapping que estén dentro del bloque leído
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
            wo_map = mapping.get("meta", {}).get("byte_order", "ABCD").upper()
            wo = WORD_ORDER or wo_map
            val = decode_value(wordsN, wo, dtype, scale, offset)
        except Exception:
            continue
        label = meta.get("label", f"R{addr_dec}")
        out_labeled[label] = val

def blocks_to_labeled(blocks):
    labeled = {}
    for b in blocks:
        start_addr = int(b["start_addr"])
        regs = b["regs"]
        _decode_from_block(WORD_ORDER, start_addr, regs, labeled)
    return labeled

# -------------------- Construcción de payloads por línea --------------------
FIELDS_L1 = {
    "U_L1": "U", "I_L1": "I", "P_L1": "P", "Q_L1": "Q",
    "S_L1": "S", "PF_L1": "PF", "THDi_L1": "THDi", "FREQ": "FREQ",
}
FIELDS_L2 = {
    "U_L2": "U", "I_L2": "I", "P_L2": "P", "Q_L2": "Q",
    "S_L2": "S", "PF_L2": "PF", "THDi_L2": "THDi", "FREQ": "FREQ",
}

def build_phase_payload(labeled: dict, fmap: dict) -> dict:
    out = {}
    for k_src, k_dst in fmap.items():
        if k_src in labeled:
            out[k_dst] = labeled[k_src]
    out["ts"] = datetime.now(timezone.utc).isoformat()
    return out

# -------------------- MQTT Connect --------------------
def mqtt_connect():
    c = mqtt.Client(client_id=CLIENT_ID, clean_session=True, protocol=mqtt.MQTTv311)
    c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.tls_set()  # certificados del sistema
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
    return c

# -------------------- Main --------------------
def main():
    # 1) Mapping -> bloques mínimos
    regs_needed = load_mapping(MAPPING_PATH, FUNC)  # [(addr,words),...]
    blocks_plan = build_min_blocks(regs_needed)     # [(start,count),...]

    # 2) Modbus
    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=TIMEOUT_S)
    if not mb.connect():
        raise SystemExit(f"❌ No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}")

    # 3) MQTT
    mq = mqtt_connect()
    mq.loop_start()

    try:
        while True:
            # Leer todos los bloques planificados
            blocks = []
            for (start, count) in blocks_plan:
                regs_concat = read_chunked(mb, start, count, SLAVE_ID, FUNC)
                blocks.append({
                    "start_addr": start,
                    "count": len(regs_concat),
                    "regs": regs_concat,
                    "func": FUNC
                })

            # Decodificar a etiquetas
            labeled = blocks_to_labeled(blocks)

            # Construir payloads por línea y publicar
            pL1 = build_phase_payload(labeled, FIELDS_L1)
            pL2 = build_phase_payload(labeled, FIELDS_L2)

            if len(pL1) > 1:
                mq.publish(TOPIC_L1, json.dumps(pL1, ensure_ascii=False), qos=MQTT_QOS, retain=RETAIN)
                print("[MQTT] L1 ->", pL1)
            if len(pL2) > 1:
                mq.publish(TOPIC_L2, json.dumps(pL2, ensure_ascii=False), qos=MQTT_QOS, retain=RETAIN)
                print("[MQTT] L2 ->", pL2)

            time.sleep(POLL_SEC)
    except KeyboardInterrupt:
        print("\nInterrupción por usuario. Saliendo…")
    finally:
        try:
            mq.loop_stop(); mq.disconnect()
        except Exception:
            pass
        mb.close()

if __name__ == "__main__":
    main()
