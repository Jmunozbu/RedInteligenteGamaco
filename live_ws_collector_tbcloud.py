# live_ws_collector_tbcloud.py
import os, json, asyncio, requests, websockets
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TB_URL         = os.getenv("TB_URL", "https://thingsboard.cloud")
TB_USER        = os.getenv("TB_TENANT_USER")
TB_PASS        = os.getenv("TB_TENANT_PASS")
DEVICE_ID      = os.getenv("DEVICE_ID")                 # UUID del dispositivo
DEVICE_NAME    = os.getenv("TB_DEVICE_NAME")            # opcional si no pones DEVICE_ID
OUT_DIR        = os.getenv("OUT_DIR", "dataset_nilm/raw")
FILE_ROTATE    = os.getenv("FILE_ROTATE", "daily")      # "daily" o "single"

# --- utilidades ---
def http_headers(jwt): 
    return {"X-Authorization": f"Bearer {jwt}"}

def tb_login_get_jwt():
    r = requests.post(f"{TB_URL}/api/auth/login",
                      json={"username": TB_USER, "password": TB_PASS}, timeout=30)
    r.raise_for_status()
    return r.json()["token"]

def get_device_id_by_name(jwt, device_name):
    r = requests.get(f"{TB_URL}/api/tenant/devices?deviceName={device_name}",
                     headers=http_headers(jwt), timeout=30)
    r.raise_for_status()
    return r.json()["id"]["id"]

def ndjson_path():
    Path(OUT_DIR).mkdir(parents=True, exist_ok=True)
    if FILE_ROTATE == "daily":
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return str(Path(OUT_DIR) / f"telemetry_{day}.ndjson")
    return str(Path(OUT_DIR) / "telemetry.ndjson")

def _coerce_number(x):
    try:
        return float(x)
    except Exception:
        return x

async def run():
    if not (TB_USER and TB_PASS):
        raise RuntimeError("Falta TB_TENANT_USER o TB_TENANT_PASS")

    jwt = tb_login_get_jwt()

    device_id = DEVICE_ID
    if not device_id:
        if not DEVICE_NAME:
            raise RuntimeError("Define DEVICE_ID o TB_DEVICE_NAME")
        device_id = get_device_id_by_name(jwt, DEVICE_NAME)

    # ws(s) según http(s)
    ws_url = f"{TB_URL.replace('http', 'ws')}/api/ws/plugins/telemetry?token={jwt}"
    out_file = ndjson_path()

    # suscripciones: LATEST_TELEMETRY y TIME_SERIES en vivo
    sub_msg = {
        "attrSubCmds": [],
        "tsSubCmds": [
            {"entityType":"DEVICE","entityId":device_id,"scope":"LATEST_TELEMETRY","cmdId":1},
            {"entityType":"DEVICE","entityId":device_id,"scope":"TIME_SERIES","cmdId":2}
        ],
        "historyCmds": []
    }

    print(f"Conectando a {ws_url} y escribiendo en {out_file} ...")
    # cache para evitar duplicados por clave: guardamos el último ts visto
    last_ts_by_key = {}

    async with websockets.connect(ws_url, ping_interval=20) as ws:
        await ws.send(json.dumps(sub_msg))
        with open(out_file, "a", buffering=1, encoding="utf-8") as f:
            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                # Formato NDJSON: una línea por punto {ts, ts_iso, deviceId, key, value, source}
                data = msg.get("data", {})
                for key, points in data.items():
                    for ts, val in points:
                        # --- filtro de duplicados: sólo escribir si ts es estrictamente mayor al último visto para esa key
                        last_ts = last_ts_by_key.get(key, -1)
                        if ts <= last_ts:
                            continue
                        last_ts_by_key[key] = ts
                        # ---------------------------------------------------------------

                        doc = {
                            "ts": int(ts),
                            "ts_iso": datetime.fromtimestamp(ts/1000, tz=timezone.utc).isoformat(),
                            "deviceId": device_id,
                            "key": key,          # ej: REG20128, THDi_L1, etc.
                            "value": _coerce_number(val),
                            "source": "thingsboard_ws"
                        }
                        f.write(json.dumps(doc, ensure_ascii=False) + "\n")

if __name__ == "__main__":
    asyncio.run(run())
