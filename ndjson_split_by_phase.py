#!/usr/bin/env python3
# ndjson_split_by_phase.py (versión robusta de ts)
# Divide un NDJSON en archivos por fase (L1/L2), sin tocar el original.

import os, sys, json, math
from pathlib import Path
from datetime import datetime, timezone

def _to_datetime_utc(ts_any):
    """Convierte ts en datetime UTC.
       Soporta:
       - str ISO (con/sin Z)
       - int/float epoch (s o ms, auto-detect)
    """
    if ts_any is None:
        return None
    # numérico -> epoch
    if isinstance(ts_any, (int, float)):
        x = float(ts_any)
        # heurística: si es muy grande, probablemente ms
        if x > 1e12:      # nanosegundos -> no soportado: recortar
            x = x / 1e6
        if x > 1e10:      # milisegundos
            x = x / 1000.0
        return datetime.fromtimestamp(x, tz=timezone.utc)
    # string ISO
    if isinstance(ts_any, str):
        t = ts_any.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(t).astimezone(timezone.utc)
        except Exception:
            return None
    return None

def _norm_iso(ts_any):
    dt = _to_datetime_utc(ts_any)
    if not dt:
        # fallback: ahora
        dt = datetime.now(timezone.utc)
    # ISO con Z
    return dt.isoformat().replace("+00:00", "Z")

def _pick_device(doc: dict):
    return doc.get("device") or doc.get("board") or "unknown"

def _split_wide(doc: dict, phases=("L1","L2")):
    ts = _norm_iso(doc.get("ts"))
    device = _pick_device(doc)
    per_phase = {ph:{} for ph in phases}
    globals_kv = {}

    for k, v in doc.items():
        if k in ("ts","device","board") or not isinstance(k, str):
            continue

        routed = False
        # si la clave termina en _L1/_L2, va a esa fase
        for ph in phases:
            suf = f"_{ph}"
            if k.endswith(suf):
                base = k[:-len(suf)]
                if base:
                    per_phase[ph][base] = v
                routed = True
                break
        if routed:
            continue

        # Si llega aquí y tiene otros sufijos (e.g. _L3, _SUM):
        if any(k.endswith(s) for s in DROP_SUFFIXES):
            if DROP_OTHER_SUFFIX:
                continue  # descártalo
            # si no quieres descartar, podrías guardarlo como global:
            # else: globals_kv[k] = v; continue
            continue

        # Globales (sin sufijo): replica solo los permitidos
        if k in KEEP_GLOBAL_KEYS:
            globals_kv[k] = v
        # Si quieres permitir más globales, añádelos a KEEP_GLOBAL_KEYS

    out_docs = []
    for ph in phases:
        merged = {"ts": ts, "device": device, "phase": ph}
        # duplica globales permitidos y añade métricas de la fase
        if globals_kv: merged.update(globals_kv)
        if per_phase[ph]: merged.update(per_phase[ph])
        if len(merged) > 3:
            out_docs.append(merged)
    return out_docs

def _split_tidy(doc: dict, phases=("L1","L2")):
    ts = _norm_iso(doc.get("ts"))
    device = _pick_device(doc)
    per_phase = {ph:{} for ph in phases}
    globals_kv = {}

    meas = doc.get("measurements") or []
    for m in meas:
        label = str(m.get("label",""))
        val   = m.get("value", None)
        routed = False
        for ph in phases:
            suf = f"_{ph}"
            if label.endswith(suf):
                base = label[:-len(suf)]
                if base: per_phase[ph][base] = val
                routed = True
                break
        if routed: 
            continue
        phase_tag = (m.get("phase") or "").upper()
        if phase_tag in ("A","L1"):
            per_phase["L1"][label] = val; continue
        if phase_tag in ("B","L2"):
            per_phase["L2"][label] = val; continue
        globals_kv[label] = val

    out_docs = []
    for ph in phases:
        merged = {"ts": ts, "device": device, "phase": ph}
        if globals_kv: merged.update(globals_kv)
        if per_phase[ph]: merged.update(per_phase[ph])
        if len(merged) > 3:
            out_docs.append(merged)
    return out_docs

ALLOWED_PHASES = ("L1","L2")              # fases válidas en tu tablero
DROP_OTHER_SUFFIX = True                  # True: descarta *_L3, *_SUM, etc.
DROP_SUFFIXES = ("_L3", "_SUM")           # sufijos a descartar si no pertenecen a la fase
KEEP_GLOBAL_KEYS = ("FREQ",)              # globales que sí quieres replicar a cada fase

def main():
    OUT_DIR = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))
    OUTPUT_MODE = os.getenv("OUTPUT_MODE", "wide").lower()
    phases = ("L1","L2")  # tablero bifásico

    if len(sys.argv) < 2:
        print("Uso: python ndjson_split_by_phase.py <archivo_ndjson> [<archivo_ndjson> ...]")
        print("Si no pasas archivos, intentará OUT_DIR/telemetry_*.ndjson")
        files = sorted((OUT_DIR).glob("telemetry_*.ndjson"))
        if not files:
            sys.exit(1)
    else:
        files = [Path(p) for p in sys.argv[1:]]

    TRAIN_DIR = OUT_DIR / "training"
    TRAIN_DIR.mkdir(parents=True, exist_ok=True)

    for in_path in files:
        if not in_path.exists():
            print(f"[WARN] no existe {in_path}")
            continue
        base = in_path.stem
        outs = {ph: (TRAIN_DIR / f"{base}_{ph}.ndjson").open("a", encoding="utf-8") for ph in phases}
        lines = 0
        with in_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line: 
                    continue
                try:
                    doc = json.loads(line)
                except Exception:
                    continue
                rows = _split_tidy(doc, phases) if OUTPUT_MODE == "tidy" else _split_wide(doc, phases)
                for row in rows:
                    ph = row.get("phase")
                    print(json.dumps(row, ensure_ascii=False), file=outs[ph])
                    lines += 1
        for fh in outs.values():
            fh.close()
        print(f"[OK] {in_path.name} → {base}_L1.ndjson / {base}_L2.ndjson  (filas={lines})")

if __name__ == "__main__":
    main()
