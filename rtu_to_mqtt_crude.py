#!/usr/bin/env python3
# rtu_over_tcp_to_mqtt.py
import os, json, time, socket
from datetime import datetime, timezone
from dotenv import load_dotenv

from pymodbus.client.tcp import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer

import paho.mqtt.client as mqtt

load_dotenv()

# ---- Modbus (ajusta con tus valores) ----
R10_IP         = os.getenv("R10_IP")               # host RTU-over-TCP (bridge/gateway)
R10_PORT       = int(os.getenv("R10_PORT", "502"))
SLAVE_ID       = int(os.getenv("SLAVE_ID", "1"))

# Lo que usaste en Modbus Poll:
FUNC           = int(os.getenv("FUNC", "4"))       # 04 Read Input Registers (o 03)
START_ADDR     = int(os.getenv("START_ADDR"))      # inicio del bloque contiguo
QUANTITY       = int(os.getenv("QUANTITY"))        # cantidad de registros 16-bit (ej. 54 = 27 floats)

# Decodificación de floats (orden de bytes/palabras). Lo publicamos como metadato.
WORD_ORDER     = os.getenv("WORD_ORDER", "ABCD").upper()

# Intervalo de muestreo (seg)
POLL_SEC       = float(os.getenv("POLL_SEC", "1"))

# ---- MQTT (con autenticación) ----
MQTT_HOST      = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT      = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC     = os.getenv("MQTT_TOPIC", "sensors/adw220/dev01/raw")
MQTT_USER      = os.getenv("MQTT_USER", "")
MQTT_PASS      = os.getenv("MQTT_PASS", "")
MQTT_QOS       = int(os.getenv("MQTT_QOS", "0"))
CLIENT_ID      = os.getenv("MQTT_CLIENT_ID", f"adw_rtu_bridge_{socket.gethostname()}")

def mqtt_connect():
    client = mqtt.Client(client_id=CLIENT_ID, clean_session=True, userdata=None, protocol=mqtt.MQTTv311)
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASS)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    return client

def main():
    # Cliente Modbus: RTU-over-TCP = TcpClient + RtuFramer
    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=1.0)
    assert mb.connect(), f"No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}"

    mq = mqtt_connect()
    mq.loop_start()

    try:
        while True:
            if FUNC == 4:
                rr = mb.read_input_registers(address=START_ADDR, count=QUANTITY, slave=SLAVE_ID)
            else:
                rr = mb.read_holding_registers(address=START_ADDR, count=QUANTITY, slave=SLAVE_ID)

            if rr.isError():
                print(f"[WARN] Error Modbus: {rr}")
            else:
                regs = list(rr.registers)  # lista de enteros 0..65535 (QUANTITY elementos)

                payload = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "slave": SLAVE_ID,
                    "func": FUNC,
                    "start_addr": START_ADDR,
                    "count": QUANTITY,
                    "word_order": WORD_ORDER,  # metadato de cómo están codificados float32
                    "regs": regs               # bloque contiguo crudo (16-bit words)
                }

                mq.publish(MQTT_TOPIC, json.dumps(payload, ensure_ascii=False), qos=MQTT_QOS, retain=False)
                print(payload)  # descomenta si quieres ver tráfico

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
