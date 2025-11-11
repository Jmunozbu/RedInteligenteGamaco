#!/usr/bin/env python3
# rtu_over_tcp_console_hard.py
# Passthrough Modbus RTU-over-TCP (R10) -> lee solo lo mapeado y imprime en consola (sin MQTT)
# Requiere: pip install pymodbus
# Mapping esperado en: meta/mapping_registers.json  (estructura como tus scripts previos)

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from pymodbus.client import ModbusTcpClient
from pymodbus.framer.rtu_framer import ModbusRtuFramer

# ---------- PARÁMETROS HARD-CODED ----------
R10_IP        = "192.168.3.1"     # IP de tu R10 (".1" como pediste)
R10_PORT      = 502               # Puerto Modbus TCP
SLAVE_ID      = 2                 # Dirección Modbus del medidor
DEFAULT_FUNC  = 4                 # Si el mapping no trae 'fc', usa 0x04 (Input Registers)
POLL_SEC      = 3.0               # Período de sondeo (s)
TIMEOUT_S     = 1.5               # Timeout de socket
WORD_ORDER    = "ABCD"            # guardado en payload por consistencia
MAX_PER_REQ   = 110               # Límite por petición Modbus (≤125 regs ideal)
# Dónde está el mapping (usa tu archivo existente)
MAPPING_PATH  = Path("meta/mapping_registers.json")
# Permite "tragarse" pequeños huecos al agrupar (igual que tu estilo previo)
MAX_GAP_TO_MERGE = 0
# -------------------------------------------


def load_mapping(path: Path):
    """
    Estructura esperada:
    {
      "registers": [
        {"addr_dec": 20128, "words": 2, "fc": "0x04"},
        {"addr_dec": 20130, "words": 2, "fc": "0x04"},
        ...
      ]
    }
    Devuelve lista de (addr, words, fc_int) ya filtrada por 0x03/0x04.
    """
    if not path.exists():
        raise SystemExit(f"❌ No existe mapping en {path.resolve()}")

    data = json.loads(path.read_text(encoding="utf-8"))
    regs = []
    for r in data.get("registers", []):
        addr  = int(r["addr_dec"])
        words = int(r.get("words", 2))
        fc    = str(r.get("fc", f"0x0{DEFAULT_FUNC}")).lower().strip()
        if fc not in ("0x03", "0x04"):
            # ignora otros FC
            continue
        fc_int = 3 if fc == "0x03" else 4
        regs.append((addr, words, fc_int))

    if not regs:
        raise SystemExit("❌ Mapping sin registros válidos (solo se soportan 0x03/0x04).")
    # Orden por función y dirección
    regs.sort(key=lambda x: (x[2], x[0]))
    return regs


def build_min_blocks(regs):
    """
    A partir de (addr, words, fc) agrupa en bloques mínimos [(start, count, fc), ...]
    Permite agrupar con pequeños huecos controlados por MAX_GAP_TO_MERGE.
    """
    if not regs:
        return []

    blocks = []
    cur_fc, cur_start = regs[0][2], regs[0][0]
    cur_end = regs[0][0] + regs[0][1] - 1

    for addr, words, fc in regs[1:]:
        need_start = addr
        need_end   = addr + words - 1
        # Solo agrupamos si es la misma función y el inicio está dentro del gap permitido
        if fc == cur_fc and need_start <= cur_end + MAX_GAP_TO_MERGE + 1:
            if need_end > cur_end:
                cur_end = need_end
        else:
            blocks.append((cur_start, cur_end - cur_start + 1, cur_fc))
            cur_fc, cur_start, cur_end = fc, need_start, need_end

    blocks.append((cur_start, cur_end - cur_start + 1, cur_fc))
    return blocks


def read_chunked(mb: ModbusTcpClient, start_addr: int, total_count: int, slave_id: int, func: int):
    """
    Hace lecturas segmentadas (MAX_PER_REQ) para cubrir total_count registros.
    Devuelve lista de enteros (cada 'register' 16 bits).
    """
    regs_concat = []
    remaining = total_count
    curr_addr = start_addr

    while remaining > 0:
        this_count = min(remaining, MAX_PER_REQ)
        if func == 4:
            rr = mb.read_input_registers(address=curr_addr, count=this_count, slave=slave_id)
        else:
            rr = mb.read_holding_registers(address=curr_addr, count=this_count, slave=slave_id)

        if not rr or (hasattr(rr, "isError") and rr.isError()):
            # Rellena con ceros para mantener offsets, y sigue
            regs_concat.extend([0] * this_count)
        else:
            regs = list(getattr(rr, "registers", []) or [])
            if len(regs) != this_count:
                regs += [0] * max(0, this_count - len(regs))
            regs_concat.extend(regs)

        curr_addr += this_count
        remaining -= this_count

    return regs_concat


def main():
    # Carga mapping y construye bloques mínimos por FC
    needed_regs = load_mapping(MAPPING_PATH)     # [(addr, words, fc), ...]
    blocks = build_min_blocks(needed_regs)       # [(start, count, fc), ...]

    # Cliente Modbus RTU-over-TCP (passthrough)
    mb = ModbusTcpClient(R10_IP, port=R10_PORT, framer=ModbusRtuFramer, timeout=TIMEOUT_S)

    if not mb.connect():
        raise SystemExit(f"❌ No se pudo abrir Modbus TCP {R10_IP}:{R10_PORT}")

    try:
        while True:
            block_payloads = []
            for (start, count, fc) in blocks:
                regs_concat = read_chunked(mb, start, count, SLAVE_ID, fc)
                block_payloads.append({
                    "start_addr": start,
                    "count": len(regs_concat),
                    "func": fc,
                    "regs": regs_concat
                })

            payload = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "device": "adw220",
                "slave": SLAVE_ID,
                "word_order": WORD_ORDER,
                "mode": "blocks_console",
                "ip": R10_IP,
                "port": R10_PORT,
                "blocks": block_payloads
            }
            print(json.dumps(payload, ensure_ascii=False))
            time.sleep(POLL_SEC)

    except KeyboardInterrupt:
        print("\nInterrupción por usuario, saliendo...")
    finally:
        mb.close()


if __name__ == "__main__":
    main()
