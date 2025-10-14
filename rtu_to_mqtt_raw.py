#!/usr/bin/env python3
# rtu_over_tcp_to_mqtt.py
import os, json, time, socket
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from pymodbus.client.tcp import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer

import paho.mqtt.client as mqtt

load_dotenv()

# ---- Modbus ----
R10_IP         = os.getenv("R10_IP")
R10_PORT       = int(os.getenv("R10_PORT", "502"))
SLAVE_ID       = int(os.getenv("SLAVE_ID", "1"))
FUNC           = int(os.getenv("FUNC", "4"))       # 04 input, 03 holding

# Rango manual (fallback)
START_ADDR     = os.getenv("START_ADDR")
QUANTITY       = os.getenv("QUANTITY")

# Decodificación de floats
WORD_ORDER     = os.getenv("WORD_ORDER", "ABCD").upper()

# Intervalo de muestreo (seg)
POLL_SEC       = float(os.getenv("POLL_SEC", "1"))

# ---- MQTT ----
MQTT_HOST      = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC     = os.getenv("MQTT_TOPIC", "sensors/adw220/dev01/raw")
MQTT_USER      = os.getenv("MQTT_USER", "")
MQTT_PASS      = os.getenv("MQTT_PASS", "")
MQTT_QOS       = int(os.getenv("MQTT_QOS", "0"))
CLIENT_ID      = os.getenv("MQTT_CLIENT_ID", f"adw_rtu_bridge_{socket.gethostname()}")

# ---- Auto range desde mapping ----
AUTO_RANGE_FROM_MAPPING = os.getenv("AUTO_RANGE_FROM_MAPPING", "0") == "1"
MAPPING_PATH = Path(os.getenv("MAPPING_PATH", "meta/mapping_registers.json"))

def mqtt_connect():
    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return client

def compute_range_from_mapping(mapping_path: Path):
    m = json.loads(mapping_path.read_text(encoding="utf-8"))
    regs = m.get("registers", [])
    addrs = []
    for r in regs:
        if r.get("fc", "0x04") != "0x04" and FUNC == 4:
            continue
        if r.get("fc", "0x03") != "0x03" and FUNC == 3:
            continue
        addr = int(r["addr_dec"])
        words = int(r.get("words", 2))
        addrs.append((addr, words))
    if not addrs:
        raise ValueError("No hay registros compatibles con FUNC en el mapping")
    start = min(a for a,_ in addrs)
    end   = max(a+w-1 for a,w in addrs)
    count = end - start + 1
    return start, count

def main():
    if AUTO_RANGE_FROM_MAPPING:
        s, c = compute_range_from_mapping(MAPPING_PATH)
        start_addr = s
        quantity = c
    else:
        if START_ADDR is None or QUANTITY is None:
            raise SystemExit("Define START_ADDR y QUANTITY o usa AUTO_RANGE_FROM_MAPPING=1")
        start_addr = int(START_ADDR)
        quantity = int(QUANTITY)

    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=1.0)
    assert mb.connect(), f"No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}"

    mq = mqtt_connect()
    mq.loop_start()

    try:
        while True:
            rr = mb.read_input_registers(address=start_addr, count=quantity, slave=SLAVE_ID) if FUNC == 4 \
                 else mb.read_holding_registers(address=start_addr, count=quantity, slave=SLAVE_ID)

            if rr.isError():
                print(f"[WARN] Error Modbus: {rr}")
            else:
                regs = list(rr.registers)
                payload = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "device": "adw220",
                    "slave": SLAVE_ID,
                    "func": FUNC,
                    "start_addr": start_addr,
                    "count": quantity,
                    "word_order": WORD_ORDER,
                    "regs": regs
                }
                mq.publish(MQTT_TOPIC, json.dumps(payload, ensure_ascii=False), qos=MQTT_QOS, retain=False)
                # print(payload)

            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        pass
    finally:
        try:
            mq.loop_stop()
            mq.disconnect()
        except Exception:
            pass
        mb.close()

if __name__ == "__main__":
    main()
