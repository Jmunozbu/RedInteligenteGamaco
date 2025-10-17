# watch_register_rtu.py
import os, time, argparse
from datetime import datetime, timezone
from pymodbus.client import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer
from pymodbus.payload import BinaryPayloadDecoder
from pymodbus.constants import Endian

def decode_payload(registers, dtype, byte_order, word_order):
    if dtype in ("uint16","int16"):
        v = registers[0] & 0xFFFF
        if dtype=="int16" and v>=0x8000: v -= 0x10000
        return float(v)
    dec = BinaryPayloadDecoder.fromRegisters(
        registers,
        byteorder=Endian.BIG if byte_order=="big" else Endian.LITTLE,
        wordorder=Endian.BIG if word_order=="big" else Endian.LITTLE,
    )
    return float(dec.decode_32bit_float())

def main():
    ap = argparse.ArgumentParser(description="Watcher de un registro Modbus (RTU-over-TCP).")
    ap.add_argument("--host", default=os.getenv("R10_IP","192.168.3.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("R10_PORT","502")))
    ap.add_argument("--unit", type=int, default=int(os.getenv("SLAVE_ID","2")))   # ¡Ojo! default 2 como en tu .env
    ap.add_argument("--func", type=int, default=int(os.getenv("FUNC","4")), choices=[3,4])
    ap.add_argument("--addr", type=int, required=True)
    ap.add_argument("--dtype", choices=["uint16","int16","float32"], default="float32")
    ap.add_argument("--byte-order", choices=["big","little"], default="big")
    ap.add_argument("--word-order", choices=["big","little"], default="little")
    ap.add_argument("--interval", type=float, default=float(os.getenv("POLL_SEC","1.0")))
    ap.add_argument("--timeout", type=float, default=2.0)
    ap.add_argument("--tol", type=float, default=0.0)
    ap.add_argument("--max-reads", type=int, default=0)
    args = ap.parse_args()

    count = 1 if args.dtype in ("uint16","int16") else 2

    # IMPORTANTE: RTU-over-TCP, igual que tu script funcional
    client = ModbusTcpClient(args.host, port=args.port, framer=ModbusRtuFramer, timeout=args.timeout)
    if not client.connect():
        print(f"[ERR] No se pudo conectar a {args.host}:{args.port}")
        return

    last_val = None
    last_change_ts = None
    reads = 0

    print(f"# Observando addr={args.addr} func={args.func} dtype={args.dtype} every {args.interval}s (RTU-over-TCP)")
    print("# Ctrl+C para salir\n")

    try:
        while True:
            rr = (client.read_input_registers if args.func==4 else client.read_holding_registers)(
                address=args.addr, count=count, slave=args.unit
            )

            ts = time.time()
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

            if rr.isError():
                print(f"{iso} [ERR] {rr}")
            else:
                regs = getattr(rr, "registers", [])
                try:
                    val = decode_payload(regs, args.dtype, args.byte_order, args.word_order)
                except Exception as e:
                    print(f"{iso} [ERR] Decodificación: {e} regs={regs}")
                    val = None

                if val is not None:
                    if last_val is None or abs(val - last_val) > args.tol:
                        dt = None if last_change_ts is None else (ts - last_change_ts)
                        last_change_ts = ts
                        last_val = val
                        if dt is None:
                            print(f"{iso} value={val}  (primer valor)")
                        else:
                            print(f"{iso} value={val}  Δt_change={dt:.3f}s  (tol={args.tol})")
                    else:
                        age = ts - last_change_ts if last_change_ts else 0.0
                        print(f"{iso} value={val}  since_last_change={age:.3f}s")

            reads += 1
            if args.max_reads and reads >= args.max_reads:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        pass
    finally:
        client.close()

if __name__ == "__main__":
    main()
