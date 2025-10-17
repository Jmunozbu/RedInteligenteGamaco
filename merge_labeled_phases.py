#!/usr/bin/env python3
# merge_labeled_phases.py  (con orden por ts y opciones)
import os, sys, json, random
from pathlib import Path
from datetime import datetime, timezone

def to_dt_utc(ts_any):
    if ts_any is None: return None
    if isinstance(ts_any, (int, float)):
        x = float(ts_any)
        if x > 1e12: x /= 1e6
        if x > 1e10: x /= 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
    if isinstance(ts_any, str):
        t = ts_any.replace("Z","+00:00")
        try:
            return datetime.fromisoformat(t).astimezone(timezone.utc)
        except Exception:
            return None
    return None

def main():
    OUT_DIR = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))
    DROP_PHASE    = int(os.getenv("DROP_PHASE", "1"))     # 1: elimina 'phase'
    SHUFFLE       = int(os.getenv("SHUFFLE", "1"))        # 1: mezcla
    SORT_BY_TS    = int(os.getenv("SORT_BY_TS", "0"))     # 1: ordena por ts asc
    GROUP_BY_DAY  = int(os.getenv("GROUP_BY_DAY", "0"))   # 1: además, genera 1 archivo por día
    SAVE_PARQUET  = int(os.getenv("SAVE_PARQUET", "0"))   # requiere pandas/pyarrow si 1
    RANDOM_SEED   = os.getenv("RANDOM_SEED", None)        # semilla reproducible

    if RANDOM_SEED is not None:
        try: random.seed(int(RANDOM_SEED))
        except: pass

    TRAIN_DIR   = OUT_DIR / "training"
    LABELED_DIR = TRAIN_DIR / "labeled"
    MERGED_DIR  = TRAIN_DIR / "merged"
    MERGED_DIR.mkdir(parents=True, exist_ok=True)

    files = sorted(LABELED_DIR.glob("*_L[12]_labeled.ndjson"))
    if not files:
        print(f"[ERR] No se encontraron archivos etiquetados en {LABELED_DIR}")
        sys.exit(1)

    rows = []
    for fp in files:
        with fp.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: continue
                try:
                    doc = json.loads(line)
                except Exception:
                    continue
                if DROP_PHASE: doc.pop("phase", None)
                rows.append(doc)

    # Orden / mezcla
    if SORT_BY_TS:
        rows.sort(key=lambda d: (to_dt_utc(d.get("ts")) or datetime.min.replace(tzinfo=timezone.utc),
                                 d.get("device",""), d.get("label",{}).get("loads_on",[])))
    elif SHUFFLE:
        random.shuffle(rows)

    # Escribe combinado
    merged_path = MERGED_DIR / ("train_all_sorted.ndjson" if SORT_BY_TS else "train_all.ndjson")
    with merged_path.open("w", encoding="utf-8") as f:
        for doc in rows:
            f.write(json.dumps(doc, ensure_ascii=False) + "\n")
    print(f"[OK] Dataset combinado: {merged_path} (registros={len(rows)})")

    # (Opcional) archivos por día
    if GROUP_BY_DAY and SORT_BY_TS:
        day_buckets = {}
        for d in rows:
            dt = to_dt_utc(d.get("ts")) or datetime.min.replace(tzinfo=timezone.utc)
            day = dt.date().isoformat()
            day_buckets.setdefault(day, []).append(d)
        for day, docs in sorted(day_buckets.items()):
            p = MERGED_DIR / f"train_{day}.ndjson"
            with p.open("w", encoding="utf-8") as f:
                for doc in docs:
                    f.write(json.dumps(doc, ensure_ascii=False) + "\n")
            print(f"[OK] Día {day}: {len(docs)} → {p}")

    if SAVE_PARQUET:
        try:
            import pandas as pd
            pq_path = MERGED_DIR / ("train_all_sorted.parquet" if SORT_BY_TS else "train_all.parquet")
            pd.DataFrame(rows).to_parquet(pq_path, index=False)
            print(f"[OK] Parquet: {pq_path}")
        except Exception as e:
            print(f"[WARN] No se pudo guardar Parquet ({e}). Instala pandas/pyarrow o desactiva SAVE_PARQUET.")

if __name__ == "__main__":
    main()
