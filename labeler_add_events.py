#!/usr/bin/env python3
# labeler_add_events.py
# Aplica etiquetas de qué carga(s) estuvieron conectadas por rangos de tiempo.
# - Lee OUT_DIR/training/*_L1.ndjson / *_L2.ndjson (o rutas que pases)
# - Lee un CSV de intervalos: phase,start_ts,end_ts,loads_on
# - Escribe OUT_DIR/training/labeled/<archivo>_labeled.ndjson
#
# También soporta un modo alterno con "eventos toggle" (ON/OFF) en NDJSON (ver más abajo).

import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timezone

def parse_iso(ts: str):
    # Devuelve datetime aware UTC
    if not ts:
        return None
    t = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(t).astimezone(timezone.utc)

def in_interval(ts_dt, iv):
    return iv["start"] <= ts_dt < iv["end"]

def load_intervals_csv(csv_path: Path):
    """
    CSV con columnas:
      phase,start_ts,end_ts,loads_on
    loads_on: 'LED1' o 'KET1+PC1' para múltiples
    """
    intervals_by_phase = {"L1": [], "L2": []}
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ph = row.get("phase","").strip().upper()
            if ph not in intervals_by_phase:
                continue
            start = parse_iso(row.get("start_ts",""))
            end   = parse_iso(row.get("end_ts",""))
            if not start or not end or end <= start:
                continue
            loads = row.get("loads_on","").strip()
            loads_list = [s for s in loads.split("+") if s]
            intervals_by_phase[ph].append({
                "start": start, "end": end, "loads_on": loads_list
            })
    # Ordena por inicio
    for ph in intervals_by_phase:
        intervals_by_phase[ph].sort(key=lambda x: x["start"])
    return intervals_by_phase

def annotate_file(in_path: Path, intervals_by_phase, out_dir: Path):
    base = in_path.stem  # e.g., telemetry_2025-10-15_L1
    # Deducir fase del nombre; por seguridad, también leerla del doc si viene
    phase_guess = "L1" if base.upper().endswith("_L1") else ("L2" if base.upper().endswith("_L2") else None)
    out_path = out_dir / f"{base}_labeled.ndjson"
    written = 0

    with in_path.open("r", encoding="utf-8") as fin, out_path.open("w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line: 
                continue
            try:
                doc = json.loads(line)
            except Exception:
                continue

            ts = doc.get("ts")
            ts_dt = parse_iso(ts)
            if not ts_dt:
                continue

            ph = (doc.get("phase") or phase_guess or "").upper()
            ivs = intervals_by_phase.get(ph, [])

            # Busca etiquetas activas (soporta superposición -> unión)
            loads_on = []
            for iv in ivs:
                if in_interval(ts_dt, iv):
                    for lid in iv["loads_on"]:
                        if lid not in loads_on:
                            loads_on.append(lid)

            # Inserta etiqueta
            doc["load"] = loads_on
            """doc["load"] = {
                "phase": ph or "",
                "loads_on": loads_on   # [] si no hay ninguna activa
            }"""
            fout.write(json.dumps(doc, ensure_ascii=False) + "\n")
            written += 1

    print(f"[OK] {in_path.name} → {out_path.name}  (filas={written})")

def main():
    OUT_DIR = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))
    TRAIN_DIR = OUT_DIR / "training"
    LABELED_DIR = TRAIN_DIR / "labeled"
    LABELED_DIR.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) < 2:
        print("Uso:")
        print("  python labeler_add_events.py <labels.csv> [archivos_por_fase.ndjson ...]")
        print("Si no pasas archivos, buscará en OUT_DIR/training/*_L1.ndjson y *_L2.ndjson")
        sys.exit(1)

    labels_csv = Path(sys.argv[1])
    if not labels_csv.exists():
        print(f"[ERR] No existe {labels_csv}")
        sys.exit(1)

    intervals_by_phase = load_intervals_csv(labels_csv)

    if len(sys.argv) > 2:
        files = [Path(p) for p in sys.argv[2:]]
    else:
        files = sorted((TRAIN_DIR).glob("*_L[12].ndjson"))

    if not files:
        print("[ERR] No se encontraron archivos por fase para etiquetar.")
        sys.exit(1)

    for fp in files:
        annotate_file(fp, intervals_by_phase, LABELED_DIR)

if __name__ == "__main__":
    main()
