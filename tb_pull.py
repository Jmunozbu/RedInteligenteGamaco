import os, time, json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import pandas as pd
from tb_rest_client.rest_client_ce import RestClientCE

load_dotenv()
HOST = os.getenv("TB_HOST")
USER = os.getenv("TB_USER")
PASS = os.getenv("TB_PASS")
DEV_NAME = os.getenv("TB_DEVICE_NAME")
KEYS = os.getenv("TB_KEYS").split(",")

# rango de tiempo: última hora
end_ts = int(time.time() * 1000)
start_ts = end_ts - 60*60*1000

client = RestClientCE(base_url=HOST)
client.login(username=USER, password=PASS)

# buscar el deviceId por nombre
devices = client.get_tenant_device_infos(0, 100, text_search=DEV_NAME)
dev = next((d for d in devices.data if d.name == DEV_NAME), None)
assert dev, f"Dispositivo {DEV_NAME} no encontrado"
device_id = dev.id.id

# pedir timeseries
ts = client.get_timeseries(entity_type="DEVICE", entity_id=device_id, keys=",".join(KEYS),
                           start_ts=start_ts, end_ts=end_ts, interval=1000, limit=100000, agg="NONE")

# normalizar a DataFrame
records = []
for k, arr in ts.items():
    for p in arr:
        records.append({"ts": int(p["ts"]), "key": k, "value": float(p["value"])})
df = pd.DataFrame(records)
if df.empty:
    print("Sin datos en el rango.")
else:
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_convert("America/Bogota")
    df = df.pivot_table(index="ts", columns="key", values="value").sort_index()
    out = f"dataset/raw/{DEV_NAME}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.parquet"
    os.makedirs("dataset/raw", exist_ok=True)
    df.to_parquet(out)
    print(f"Guardado: {out}  ({len(df)} filas)")
