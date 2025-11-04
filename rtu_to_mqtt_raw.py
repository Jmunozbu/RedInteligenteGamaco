#!/usr/bin/env python3
# rtu_to_mqtt_raw.py  (solo direcciones mapeadas, en bloques)
import os, json, time, socket
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from pymodbus.client.tcp import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer
import paho.mqtt.client as mqtt

load_dotenv()

# ---- Modbus/Gateway ----
R10_IP   = os.getenv("R10_IP")
R10_PORT = int(os.getenv("R10_PORT", "502"))
SLAVE_ID = int(os.getenv("SLAVE_ID", "2"))
FUNC     = int(os.getenv("FUNC", "4"))           # default 04 (Input Registers)
WORD_ORDER = os.getenv("WORD_ORDER", "ABCD").upper()
POLL_SEC = float(os.getenv("POLL_SEC", "1"))

# ---- MQTT ----
MQTT_HOST  = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT  = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("PUB_MQTT_TOPIC", "sensors/adw220/dev01/raw")
MQTT_USER  = os.getenv("PUB_MQTT_USER", "")
MQTT_PASS  = os.getenv("PUB_MQTT_PASS", "")
MQTT_QOS   = int(os.getenv("PUB_MQTT_QOS", "0"))
CLIENT_ID  = os.getenv("PUB_MQTT_CLIENT_ID", f"adw_rtu_bridge_{socket.gethostname()}")

# ---- Mapping y estrategia ----
MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))
# Cómo agrupar direcciones contiguas del mapping:
#   MAX_GAP_TO_MERGE=0 => solo contiguas reales (sin huecos)
#   Si pones 1,2,... permites “tragarte” huecos pequeños para menos peticiones.
MAX_GAP_TO_MERGE = int(os.getenv("MAX_GAP_TO_MERGE", "0"))
# Límite por petición Modbus (≤125 regs)
MAX_PER_REQ = int(os.getenv("MAX_PER_REQ", "110"))

def mqtt_connect():
    c = mqtt.Client(client_id=CLIENT_ID, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return c

def load_mapping(path: Path):
    m = json.loads(path.read_text(encoding="utf-8"))
    regs = []
    for r in m.get("registers", []):
        addr  = int(r["addr_dec"])
        words = int(r.get("words", 2))
        fc    = str(r.get("fc", "0x04")).lower()
        # Filtra por función (solo leemos los que coinciden con FUNC)
        if FUNC == 4 and fc != "0x04": 
            continue
        if FUNC == 3 and fc != "0x03":
            continue
        regs.append((addr, words))
    if not regs:
        raise SystemExit("No hay registros compatibles con FUNC en el mapping.")
    regs.sort(key=lambda x: x[0])
    return regs

def build_min_blocks(regs):
    """
    regs: lista de (addr, words). Devuelve bloques mínimos [(start,count), ...]
    Solo cubren lo mapeado. Puedes permitir juntar bloques con huecos pequeños via MAX_GAP_TO_MERGE.
    """
    blocks = []
    if not regs: 
        return blocks
    # Inicializa primer bloque
    cur_start = regs[0][0]
    cur_end = regs[0][0] + regs[0][1] - 1

    for addr, words in regs[1:]:
        need_start = addr
        need_end   = addr + words - 1
        # Si el siguiente comienza dentro de (cur_end + gap permitido), extendemos
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
            # Rellena con ceros para mantener offsets
            regs_concat.extend([0]*this_count)
        else:
            regs = list(getattr(rr, "registers", []))
            if len(regs) != this_count:
                regs = regs + [0]*max(0, this_count - len(regs))
            regs_concat.extend(regs)
        curr_addr += this_count
        remaining -= this_count
    return regs_concat

def main():
    needed_regs = load_mapping(MAPPING_PATH)              # [(addr,words),...]
    blocks = build_min_blocks(needed_regs)                # [(start,count),...]
    # Nota: si tu mapping mezcla FC03/FC04, crea dos listas separadas por FUNC y corre dos lecturas.

    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=1.5)
    assert mb.connect(), f"No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}"

    mq = mqtt_connect()
    mq.loop_start()

    try:
        while True:
            block_payloads = []
            for (start, count) in blocks:
                regs_concat = read_chunked(mb, start, count, SLAVE_ID, FUNC)
                block_payloads.append({
                    "start_addr": start,
                    "count": len(regs_concat),
                    "regs": regs_concat,
                    "func": FUNC
                })

            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "device": "adw220",
                "slave": SLAVE_ID,
                "word_order": WORD_ORDER,
                "mode": "blocks",
                "blocks": block_payloads
            }
            mq.publish(MQTT_TOPIC, json.dumps(payload, ensure_ascii=False), qos=MQTT_QOS, retain=False)
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
