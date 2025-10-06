import os, json, requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()  # para leer .env si lo usas

TB_URL    = os.getenv("TB_URL", "https://thingsboard.cloud")
#TB_URL    = os.getenv("TB_URL", "").rstrip("/")
TB_USER   = os.getenv("TB_TENANT_USER")
TB_PASS   = os.getenv("TB_TENANT_PASS")
DEVICE_ID = os.getenv("DEVICE_ID")
KEYS_CSV  = os.getenv("TB_KEYS", "REG20128,REG20130,REG20132,REG20134,REG20136")

def require_env(name, val):
    if not val:
        raise RuntimeError(f"Falta variable de entorno: {name}")

def jwt():
    require_env("TB_URL", TB_URL)
    require_env("TB_TENANT_USER", TB_USER)
    require_env("TB_TENANT_PASS", TB_PASS)
    url = f"{TB_URL}/api/auth/login"
    try:
        r = requests.post(url, json={"username": TB_USER, "password": TB_PASS}, timeout=30)
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Error de red al conectar a {url}: {e}") from e
    if r.status_code != 200:
        # Mensaje útil para depurar
        raise RuntimeError(
            f"Login 401/403 en {url}. Revisa TB_URL, usuario y contraseña.\n"
            f"status={r.status_code}, body={r.text[:500]}"
        )
    try:
        return r.json()["token"]
    except Exception:
        raise RuntimeError(f"Respuesta inesperada en login: {r.text[:500]}")

def export_timeseries(device_id, start_ts, end_ts, keys_csv, limit=10000):
    token = jwt()
    headers={"X-Authorization": f"Bearer {token}"}
    url = f"{TB_URL}/api/plugins/telemetry/DEVICE/{device_id}/values/timeseries"
    params={"keys": keys_csv, "startTs": start_ts, "endTs": end_ts, "limit": limit, "agg":"NONE"}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code == 403:
        raise RuntimeError("403 Forbidden: el usuario no tiene permisos para leer ese dispositivo.")
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    require_env("DEVICE_ID", DEVICE_ID)

    # Usa objeto timezone-aware para evitar el warning
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=4)

    print(f"TB_URL={TB_URL}")
    print(f"TB_USER (masked)={TB_USER[:2]}***")
    print(f"DEVICE_ID={DEVICE_ID}")
    print(f"Ventana: {start.isoformat()} → {end.isoformat()}")
    print(f"Keys: {KEYS_CSV}")

    data  = export_timeseries(
        DEVICE_ID,
        int(start.timestamp()*1000),
        int(end.timestamp()*1000),
        KEYS_CSV
    )

    out = Path("export.ndjson")
    with out.open("w", encoding="utf-8") as f:
        for key, rows in data.items():
            for row in rows:
                f.write(json.dumps({
                    "ts": row["ts"],
                    "ts_iso": datetime.fromtimestamp(row["ts"]/1000, tz=timezone.utc).isoformat(),
                    "deviceId": DEVICE_ID,
                    "key": key,
                    "value": float(row["value"]) if row["value"] is not None else None,
                    "source":"thingsboard_rest"
                }, ensure_ascii=False) + "\n")
    print(f"OK → {out} ({sum(len(v) for v in data.values())} puntos)")
