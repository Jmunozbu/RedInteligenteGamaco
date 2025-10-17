#!/usr/bin/env python3
# build_events_from_ndjson.py
#
# Detecta eventos (cambios escalón) en archivos NDJSON por fase y los guarda como:
#   OUT_DIR/training/events_<PHASE>.ndjson
#
# Modo 1 (default): solo detecta y exporta eventos con features (event="change").
# Modo 2 (opcional): si pasas un YAML de reglas, intenta asignar load_id y new_state
#   según ventanas de ΔP, ΔTHDi, etc., y mantiene estado para decidir ON/OFF.
#
# Uso:
#   $env:OUT_DIR="C:\...\dataset_nilm\raw"
#   python .\build_events_from_ndjson.py               # auto-descubre *_L1/_L2
#   python .\build_events_from_ndjson.py --rules rules.yaml  # aplica reglas
#   python .\build_events_from_ndjson.py files ...     # procesa rutas específicas
#
# Requiere solo stdlib.

import os, sys, json, math, argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta

try:
    import yaml  # opcional si usas --rules
    _HAS_YAML = True
except Exception:
    _HAS_YAML = False

# ----------------- utilidades de tiempo -----------------
def parse_iso(ts: str):
    if not ts:
        return None
    t = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(t).astimezone(timezone.utc)

def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00","Z")

# ----------------- filtros y robustez -----------------
def ema(prev, x, alpha):
    return x if prev is None else (alpha * x + (1 - alpha) * prev)

def rolling_median(values, w):
    arr = values[-w:]
    s = sorted(arr)
    n = len(s)
    if n == 0: return None
    mid = n // 2
    return (s[mid] if (n % 2 == 1) else 0.5*(s[mid-1]+s[mid]))

def mad(values, med):
    # Median Absolute Deviation
    abs_dev = [abs(v - med) for v in values]
    if not abs_dev: return 0.0
    s = sorted(abs_dev)
    n = len(s)
    mid = n // 2
    mad = (s[mid] if (n % 2 == 1) else 0.5*(s[mid-1]+s[mid]))
    # Escala a ~desv.std. gaussiana
    return 1.4826 * mad

# ----------------- lectura NDJSON por fase -----------------
def read_phase_ndjson(path: Path):
    # Devuelve listas alineadas por tiempo (asumiendo 1 Hz o similar)
    ts_list, P, Q, S, I, PF, THDi = [], [], [], [], [], [], []
    H = {}  # armónicos, p.ej. H3, H5, H7...
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                doc = json.loads(line)
            except Exception:
                continue
            ts = parse_iso(doc.get("ts",""))
            if not ts: 
                continue
            ts_list.append(ts)
            P.append(float(doc.get("P",  float("nan"))))
            Q.append(float(doc.get("Q",  float("nan"))))
            S.append(float(doc.get("S",  float("nan"))))
            I.append(float(doc.get("I",  float("nan"))))
            PF.append(float(doc.get("PF", float("nan"))))
            THDi.append(float(doc.get("THDi", float("nan"))))
            # Captura armónicos presentes
            for k, v in doc.items():
                if isinstance(k, str) and k.upper().startswith("H"):
                    try:
                        _ = int(k[1:])  # número de armónico
                        H.setdefault(k.upper(), []).append(float(v))
                    except Exception:
                        pass
    # Asegura longitud igual para armónicos (rellena con NaN)
    for hk, arr in H.items():
        if len(arr) != len(ts_list):
            # rellena al tamaño mayor
            while len(arr) < len(ts_list):
                arr.append(float("nan"))
    return {
        "ts": ts_list, "P": P, "Q": Q, "S": S, "I": I, "PF": PF, "THDi": THDi, "H": H
    }

# ----------------- extracción de features instantáneas -----------------
def snapshot(idx, series, H):
    def g(a, i):
        try:
            v = a[i]
            return (None if (v is None or (isinstance(v,float) and math.isnan(v))) else float(v))
        except Exception:
            return None
    snap = {
        "P": g(series["P"], idx),
        "Q": g(series["Q"], idx),
        "S": g(series["S"], idx),
        "I": g(series["I"], idx),
        "PF": g(series["PF"], idx),
        "THDi": g(series["THDi"], idx),
    }
    # añade armónicos
    for hk, arr in H.items():
        snap[hk] = g(arr, idx)
    return snap

def ratio(a, b):
    try:
        if a is None or b is None or b == 0 or (isinstance(b,float) and math.isnan(b)):
            return None
        return float(a)/float(b)
    except Exception:
        return None

# ----------------- detección de eventos -----------------
def detect_events(phase_name, series, cfg, out_path):
    ts = series["ts"]
    P = series["P"]; Q = series["Q"]; S = series["S"]; I = series["I"]; PF = series["PF"]; THDi = series["THDi"]
    H = series["H"]

    # Config
    SMOOTH_SEC     = int(os.getenv("SMOOTH_SEC",     cfg.get("smooth_sec", 3)))
    WINDOW_SEC     = int(os.getenv("WINDOW_SEC",     cfg.get("window_sec", 31)))
    Z_K            = float(os.getenv("Z_K",          cfg.get("z_k", 4.0)))   # sensibilidad robusta
    MIN_WATTS      = float(os.getenv("MIN_WATTS",    cfg.get("min_watts", 30.0)))
    REFRACTORY_SEC = int(os.getenv("REFRACTORY_SEC", cfg.get("refractory_sec", 2)))

    # EMA de P y ΔP
    alpha = 2.0 / (SMOOTH_SEC + 1.0)
    Ps = []
    prev = None
    for x in P:
        if x is None or (isinstance(x,float) and math.isnan(x)):
            prev = prev  # mantiene ultimo
        else:
            prev = ema(prev, x, alpha)
        Ps.append(prev if prev is not None else float("nan"))

    # Diferencias y detección robusta
    dP = [float("nan")]
    med_hist = []
    events = []
    last_event_t = None

    for i in range(1, len(Ps)):
        if any(math.isnan(v) for v in (Ps[i], Ps[i-1])): 
            dP.append(float("nan"))
            continue
        dp = Ps[i] - Ps[i-1]
        dP.append(dp)
        med_hist.append(dp)
        # armar ventana robusta
        if len(med_hist) > WINDOW_SEC:
            med_hist.pop(0)
        med = rolling_median(med_hist, len(med_hist))
        sigma = mad(med_hist, med) if med is not None else 0.0
        thr = Z_K * (sigma if sigma and sigma>0 else 1.0)  # evita 0
        # chequea umbral + mínimo salto absoluto
        if abs(dp) >= max(thr, MIN_WATTS):
            t_now = ts[i]
            if last_event_t and (t_now - last_event_t) < timedelta(seconds=REFRACTORY_SEC):
                continue  # evita re-disparos
            # snapshot antes/después
            before = snapshot(i-1, series, H)
            after  = snapshot(i,   series, H)
            feat = {
                "dP": dp,
                "dQ": (after["Q"]  - before["Q"])  if after["Q"]  is not None and before["Q"]  is not None else None,
                "dS": (after["S"]  - before["S"])  if after["S"]  is not None and before["S"]  is not None else None,
                "dI": (after["I"]  - before["I"])  if after["I"]  is not None and before["I"]  is not None else None,
                "dPF":(after["PF"] - before["PF"]) if after["PF"] is not None and before["PF"] is not None else None,
                "dTHDi": (after["THDi"] - before["THDi"]) if after["THDi"] is not None and before["THDi"] is not None else None,
            }
            # ratios armónicos si hay H1
            H1a, H1b = after.get("H1"), before.get("H1")
            for hk in sorted(H.keys()):
                if hk == "H1": 
                    continue
                feat[f"d{hk}"] = (after.get(hk) - before.get(hk)) if after.get(hk) is not None and before.get(hk) is not None else None
                feat[f"{hk}_ratio_before"] = ratio(before.get(hk), H1b)
                feat[f"{hk}_ratio_after"]  = ratio(after.get(hk),  H1a)

            ev = {
                "ts": iso_utc(t_now),
                "phase": phase_name,
                "event": "change",
                "delta": {"P": dp},
                "snap": {"before": before, "after": after},
                "features": feat
            }
            events.append(ev)
            last_event_t = t_now

    # Escribe archivo
    with out_path.open("w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return events

# ----------------- reglas opcionales para asignar load_id -----------------
def apply_rules(events, rules):
    """
    rules YAML ejemplo:
    phases:
      L1:
        - id: LED1
          on:
            dP_min: 15
            dP_max: 80
            dTHDi_min: 0.5    # opcional
          off:
            dP_min: -80
            dP_max: -15
      L2:
        - id: KET1
          on:  { dP_min: 800, dP_max: 1300, PF_min_after: 0.98 }
          off: { dP_min: -1300, dP_max: -800 }

    Mapea cada evento a un load_id y new_state según ventanas. Mantiene estado simple por carga.
    """
    state = {}  # (phase, load_id) -> bool (on/off)
    out = []
    for ev in events:
        ph = ev.get("phase")
        feat = ev.get("features", {})
        dp = float(ev.get("delta",{}).get("P", 0.0))
        snap_b = ev.get("snap",{}).get("before",{})
        snap_a = ev.get("snap",{}).get("after",{})
        matched = False
        for rule in (rules.get("phases",{}).get(ph, []) or []):
            lid = rule.get("id")
            # construye feature bag
            bag = {
                "dP": dp,
                "dTHDi": feat.get("dTHDi"),
                "PF_after": snap_a.get("PF"),
                "PF_before": snap_b.get("PF"),
            }
            def ok(block):
                if not block: return False
                def ge(key, thr): 
                    return (bag.get(key) is not None) and (bag.get(key) >= thr)
                def le(key, thr): 
                    return (bag.get(key) is not None) and (bag.get(key) <= thr)
                # soporta *_min/_max
                for k, v in block.items():
                    if k.endswith("_min"):
                        key = k[:-4]
                        if not ge(key, float(v)): return False
                    elif k.endswith("_max"):
                        key = k[:-4]
                        if not le(key, float(v)): return False
                return True
            # intenta ON u OFF según signo de dP
            if dp > 0 and ok(rule.get("on")):
                ev["event"] = "toggle"
                ev["load_id"] = lid
                ev["new_state"] = "on"
                state[(ph,lid)] = True
                matched = True
            elif dp < 0 and ok(rule.get("off")):
                ev["event"] = "toggle"
                ev["load_id"] = lid
                ev["new_state"] = "off"
                state[(ph,lid)] = False
                matched = True
            if matched:
                break
        out.append(ev)
    return out

# ----------------- main -----------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="Archivos por fase *_L1.ndjson / *_L2.ndjson")
    parser.add_argument("--rules", help="Ruta a YAML con reglas para asignar load_id/new_state")
    args = parser.parse_args()

    OUT_DIR = Path(os.getenv("OUT_DIR", "dataset_nilm/raw"))
    TRAIN_DIR = OUT_DIR / "training"
    EVENTS_DIR = TRAIN_DIR / "events"
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    # Carga reglas si se piden
    rules = None
    if args.rules:
        if not _HAS_YAML:
            print("[WARN] PyYAML no disponible; ignoro --rules.")
        else:
            p = Path(args.rules)
            if p.exists():
                with p.open("r", encoding="utf-8") as f:
                    rules = yaml.safe_load(f) or {}

    # Descubrimiento de archivos
    files = [Path(p) for p in args.files] if args.files else sorted(TRAIN_DIR.glob("*_L[12].ndjson"))
    if not files:
        print("[ERR] No se encontraron archivos por fase.")
        sys.exit(1)

    all_events = []
    for fp in files:
        phase = "L1" if fp.name.upper().endswith("_L1.NDJSON") else ("L2" if fp.name.upper().endswith("_L2.NDJSON") else "")
        series = read_phase_ndjson(fp)
        out_path = EVENTS_DIR / f"events_{phase or 'UNK'}.ndjson"
        evs = detect_events(phase or "UNK", series, cfg={}, out_path=out_path)
        all_events.extend(evs)
        print(f"[OK] {fp.name} → {out_path.name} ({len(evs)} eventos)")

    # Aplica reglas si hay
    if rules and _HAS_YAML:
        labeled_dir = EVENTS_DIR / "labeled"
        labeled_dir.mkdir(parents=True, exist_ok=True)
        by_phase = {}
        for ev in all_events:
            by_phase.setdefault(ev.get("phase","UNK"), []).append(ev)
        for ph, evs in by_phase.items():
            evs2 = apply_rules(evs, rules)
            out2 = labeled_dir / f"events_{ph}_labeled.ndjson"
            with out2.open("w", encoding="utf-8") as f:
                for ev in evs2:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            print(f"[OK] Reglas → {out2.name} ({len(evs2)} eventos)")

if __name__ == "__main__":
    main()
