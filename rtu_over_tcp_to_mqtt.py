import os, json, time, socket
from datetime import datetime, timezone
from dotenv import load_dotenv

from pymodbus.client.tcp import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

import paho.mqtt.client as mqtt

load_dotenv()

# ---- Modbus (ajusta si lo necesitas) ----
R10_IP         = os.getenv("R10_IP")
R10_PORT       = int(os.getenv("R10_PORT"))
SLAVE_ID       = int(os.getenv("SLAVE_ID"))

# Lo que usaste en Modbus Poll:
FUNC           = 4                    # 04 Read Input Registers
START_ADDR     = int(os.getenv("START_ADDR"))
QUANTITY       = int(os.getenv("QUANTITY"))  # 54 regs = 27 floats

# Decodificación de floats (word order). Prueba "ABCD" (Big/Big) o "CDAB" (Big/Little)
WORD_ORDER     = os.getenv("WORD_ORDER").upper()

# Intervalo de muestreo (seg)
POLL_SEC       = float(os.getenv("POLL_SEC"))

# ---- MQTT (local) ----
MQTT_HOST      = os.getenv("MQTT_HOST")
MQTT_PORT      = int(os.getenv("MQTT_PORT"))
MQTT_TOPIC     = os.getenv("MQTT_TOPIC")
MQTT_USER      = os.getenv("MQTT_USER", "")
MQTT_PASS      = os.getenv("MQTT_PASS", "")
MQTT_QOS       = int(os.getenv("MQTT_QOS", "0"))
CLIENT_ID      = os.getenv("MQTT_CLIENT_ID", f"adw_rtu_bridge_{socket.gethostname()}")

# Opcional: nombres para las 27 variables leídas (puedes renombrarlas luego)
DEFAULT_NAMES = [
    "v1","v2","v3","v12","v23","v31","freq",
    "i1","i2","i3","i_zero",
    "p1","p2","p3","p_sum",
    "q1","q2","q3","q_sum",
    "s1","s2","s3","s_sum",
    "pf1","pf2","pf3","pf_sum"
]
# Si quieres poner tus propios nombres, usa NAMES=FREQ,U_L1,...
NAMES = [x.strip() for x in os.getenv("NAMES", ",".join(DEFAULT_NAMES)).split(",")]
if len(NAMES) != QUANTITY // 2:
    # Asegura longitud consistente
    NAMES = DEFAULT_NAMES[:QUANTITY // 2]

def make_decoder(registers, word_order="ABCD"):
    # registers = lista de enteros (0..65535)
    # ABCD: Big endian en registro y palabra -> usual en Acrel
    if word_order == "ABCD":
        byteorder = Endian.BIG
        wordorder = Endian.BIG
    elif word_order == "CDAB":
        byteorder = Endian.BIG
        wordorder = Endian.LITTLE
    elif word_order == "BADC":
        byteorder = Endian.LITTLE
        wordorder = Endian.BIG
    elif word_order == "DCBA":
        byteorder = Endian.LITTLE
        wordorder = Endian.LITTLE
    else:
        byteorder = Endian.BIG
        wordorder = Endian.BIG
    return BinaryPayloadDecoder.fromRegisters(registers, byteorder=byteorder, wordorder=wordorder)

def decode_as_floats(registers, names, word_order):
    dec = make_decoder(registers, word_order)
    out = {}
    for name in names:
        out[name] = round(dec.decode_32bit_float(), 6)  # 2 regs por float
    return out

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
                # por si quisieras función 03
                rr = mb.read_holding_registers(address=START_ADDR, count=QUANTITY, slave=SLAVE_ID)

            if rr.isError():
                print(f"[WARN] Error Modbus: {rr}")
            else:
                regs = list(rr.registers)  # list[int]
                # Decodifica como 27 floats
                data = decode_as_floats(regs, NAMES, WORD_ORDER)

                payload = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "slave": SLAVE_ID,
                    "start_addr": START_ADDR,
                    "count": QUANTITY,
                    "values": data
                }
                mq.publish(MQTT_TOPIC, json.dumps(payload, ensure_ascii=False), qos=MQTT_QOS, retain=False)

                # (opcional) imprime breve
                print(payload)

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
