#!/usr/bin/env python3
# ndjson_to_csv.py
# Convierte un NDJSON (una línea = un JSON) a CSV para usar en Orange u otros programas.

import os, sys, json, csv
from pathlib import Path

def flatten(value):
    """Convierte listas o dicts simples en texto legible."""
    if isinstance(value, list):
        return "+".join(str(v) for v in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return value

def collect_keys(path):
    """Primera pasada: recopila todas las claves (columnas)."""
    keys = set()
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except Exception:
                continue
            keys.update(doc.keys())
    return sorted(keys)

def convert_ndjson_to_csv(path_in, path_out=None):
    path_in = Path(path_in)
    path_out = Path(path_out) if path_out else path_in.with_suffix(".csv")

    keys = collect_keys(path_in)
    print(f"[INFO] Columnas detectadas ({len(keys)}): {keys}")

    with open(path_in, "r", encoding="utf-8") as fin, open(path_out, "w", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=keys)
        writer.writeheader()
        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                doc = json.loads(line)
            except Exception:
                continue
            flat_doc = {k: flatten(doc.get(k, "")) for k in keys}
            writer.writerow(flat_doc)
    print(f"[OK] CSV generado: {path_out}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python ndjson_to_csv.py <archivo.ndjson> [salida.csv]")
        sys.exit(1)
    convert_ndjson_to_csv(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
