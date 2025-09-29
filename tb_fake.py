# tb_fake.py
import os, time, random, requests
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("TB_HOST", "http://localhost:8080").rstrip("/")
TOKENS = {
    "Toma_1": os.getenv("TOKEN_1"),
    "Toma_2": os.getenv("TOKEN_2"),
    "Toma_3": os.getenv("TOKEN_3"),
}

CLASSES = ["Bombillo LED", "Motor", "Cargador", "Desconectado"]

def check_tokens():
    missing = [k for k, v in TOKENS.items() if not v]
    if missing:
        raise RuntimeError(f"Faltan tokens en .env para: {', '.join(missing)}")

def push(device_token: str, payload: dict):
    url = f"{HOST}/api/v1/{device_token}/telemetry"
    try:
        r = requests.post(url, json=payload, timeout=5)
        ok = 200 <= r.status_code < 300
        print(("OK " if ok else "ERR"), r.status_code, payload)
        if not ok:
            print("Resp:", r.text[:300])
        return ok
    except Exception as e:
        print("EXC:", e)
        return False

def one_payload():
    return {
        "class": random.choice(CLASSES),
        "conf": round(random.uniform(0.55, 0.98), 3),
        "p_total": round(random.uniform(50, 2000), 1),
        "i_rms": round(random.uniform(0.2, 8.0), 2)
    }

if __name__ == "__main__":
    check_tokens()
    print("Publicando telemetría falsa a ThingsBoard (Ctrl+C para salir).")
    while True:
        for name, token in TOKENS.items():
            payload = one_payload()
            push(token, payload)
        time.sleep(5)
