#!/usr/bin/env python3
# ndjson_to_parquet.py
"""
Convierte NDJSON rotado por día a Parquet (wide o tidy).

Uso:
  python ndjson_to_parquet.py \
    --in-dir dataset_nilm/raw \
    --out-dir dataset_nilm/parquet \
    --day 2025-10-10 \
    --mode wide

  python ndjson_to_parquet.py \
    --in-dir dataset_nilm/raw \
    --out-dir dataset_nilm/parquet \
    --day 2025-10-10 \
    --mode tidy
"""

import argparse
import json
from pathlib import Path
from typing import List
import pandas as pd

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except Exception as e:
    raise SystemExit("ERROR: Necesitas instalar pyarrow (pip install pyarrow)") from e


def read_ndjson_lines(path: Path) -> List[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # Si hay líneas corruptas, las ignoramos pero podrías loguearlas
                continue
    return rows


def write_parquet_from_df(df: pd.DataFrame, out_path: Path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out_path, compression="snappy")


def main():
    ap = argparse.ArgumentParser(description="NDJSON → Parquet (wide/tidy)")
    ap.add_argument("--in-dir", required=True, type=Path, help="Directorio de NDJSON (p.ej. dataset_nilm/raw)")
    ap.add_argument("--out-dir", required=True, type=Path, help="Directorio de salida Parquet")
    ap.add_argument("--day", required=True, help="Día a procesar (YYYY-MM-DD)")
    ap.add_argument("--mode", choices=["wide", "tidy"], required=True, help="Formato de salida")
    ap.add_argument("--outfile", default=None, help="Nombre del archivo Parquet (opcional)")
    ap.add_argument("--device-partition", action="store_true",
                    help="(tidy) Particionar por dispositivo en columnas; (wide) no aplica.")
    args = ap.parse_args()

    ndjson_glob = [
        args.in_dir / f"telemetry_{args.day}.ndjson",
        # por si dividiste archivos del mismo día:
        # telemetry_YYYY-MM-DD_*.ndjson
        *sorted(args.in_dir.glob(f"telemetry_{args.day}_*.ndjson"))
    ]
    ndjson_files = [p for p in ndjson_glob if p.exists()]

    if not ndjson_files:
        raise SystemExit(f"ERROR: No encontré NDJSON para el día {args.day} en {args.in_dir}")

    # Acumular todas las filas del día (puede haber varios archivos)
    rows = []
    for p in ndjson_files:
        rows.extend(read_ndjson_lines(p))

    if not rows:
        raise SystemExit("ERROR: No hay filas válidas en los NDJSON seleccionados.")

    # Construcción del DataFrame según modo
    if args.mode == "tidy":
        # Expandir lista 'measurements'
        expanded = []
        for r in rows:
            ts = r.get("ts")
            dev = r.get("device", "adw220")
            measurements = r.get("measurements", [])
            if not isinstance(measurements, list):
                # Si vino wide por error, intentamos transformarlo
                # (toma cada clave distinta a ts/device como una medición)
                measures = [
                    {"label": k, "value": v}
                    for k, v in r.items()
                    if k not in ("ts", "device")
                ]
                measurements = measures

            for m in measurements:
                if not isinstance(m, dict) or "value" not in m or "label" not in m:
                    # Saltar registros mal formados
                    continue
                row = {"ts": ts, "device": dev}
                # Copiar todos los campos de la medición (label, value, unit, quantity, phase, etc.)
                for k, v in m.items():
                    row[k] = v
                expanded.append(row)

        if not expanded:
            raise SystemExit("ERROR: No se pudo expandir a formato tidy (¿no hay 'measurements'?).")

        df = pd.DataFrame(expanded)

        # (Opcional) Particionar por device en columnas adicionales
        # Nota: La partición real por directorios es una tarea de un writer
        # más complejo; aquí solo añadimos una columna 'device' que ya existe.
        # Si quisieras archivos por dispositivo, podrías agrupar y escribir N archivos.
        if args.device_partition:
            # Ejemplo: escribir un parquet por device
            for dev, df_dev in df.groupby("device"):
                out_name = args.outfile or f"telemetry_{args.day}_tidy_{dev}.parquet"
                out_path = args.out_dir / out_name
                write_parquet_from_df(df_dev, out_path)
            print(f"OK → {args.out_dir} (un archivo por dispositivo)")
            return

        out_name = args.outfile or f"telemetry_{args.day}_tidy.parquet"
        out_path = args.out_dir / out_name
        write_parquet_from_df(df, out_path)
        print(f"OK → {out_path}")

    else:
        # wide
        # Espera filas con {ts, device, ... columnas de medidas ...}
        df = pd.DataFrame(rows)
        if "ts" not in df.columns:
            raise SystemExit("ERROR: Modo wide requiere columna 'ts' en NDJSON.")
        if "device" not in df.columns:
            df["device"] = "adw220"

        out_name = args.outfile or f"telemetry_{args.day}_wide.parquet"
        out_path = args.out_dir / out_name
        write_parquet_from_df(df, out_path)
        print(f"OK → {out_path}")


if __name__ == "__main__":
    main()
