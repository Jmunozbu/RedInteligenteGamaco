# process_join.py
import os
import json
import pandas as pd
from pathlib import Path
from datetime import datetime

# --- rutas base ---
BASE_DIR = Path(__file__).resolve().parent
RAW_DIR = BASE_DIR / "raw"
META_DIR = BASE_DIR / "raw"
PROCESSED_DIR = BASE_DIR / "processed"
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

# --- cargar mapeo ---
mapping_path = "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\meta\mapping_registers.json"
if not mapping_path.exists():
    raise FileNotFoundError(f"No se encontró {mapping_path}. Crea primero el archivo de mapeo.")

with open(mapping_path, "r", encoding="utf-8") as f:
    mapping = json.load(f)

map_df = pd.DataFrame(mapping).T.reset_index().rename(columns={"index": "key"})

# --- procesar todos los NDJSON en raw/ ---
files = sorted(RAW_DIR.glob("*.ndjson"))
if not files:
    raise FileNotFoundError("No se encontraron archivos .ndjson en dataset_nilm/raw/")

print(f"Procesando {len(files)} archivos NDJSON...")

for file in files:
    print(f" → {file.name}")
    df = pd.read_json(file, lines=True)

    # unir con el mapeo
    df = df.merge(map_df, on="key", how="left")

    # eliminar claves no mapeadas (por si alguna telemetría no está en mapping)
    df = df[~df["feature"].isna()]

    # convertir timestamp a datetime UTC
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

    # reorganizar columnas
    cols = ["datetime", "deviceId", "feature", "value", "unit", "phase", "description", "key"]
    df = df[[c for c in cols if c in df.columns]]

    # pivot: columnas = features
    pivot = df.pivot_table(index="datetime", columns="feature", values="value", aggfunc="last").sort_index()

    # generar nombre de salida
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    out_path = PROCESSED_DIR / f"features_{date_str}.parquet"

    # guardar
    pivot.to_parquet(out_path)
    print(f"   Guardado: {out_path} ({len(pivot)} filas, {len(pivot.columns)} columnas)")

print("✅ Proceso completado.")
