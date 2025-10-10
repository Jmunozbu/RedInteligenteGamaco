# 📘 Configuración y lectura del ADW220 a través del R10 en modo Modbus RTU-over-TCP

## 🧩 Objetivo
Este documento describe la configuración necesaria para utilizar el **Acrel R10** como pasarela (“gateway”) entre el **analizador ADW220** (Modbus RTU) y un computador o servidor que lea los datos vía **Modbus RTU-over-TCP** y los publique en un **broker MQTT local**.

---

## 🔌 1. Conexión física

| Elemento | Descripción |
|-----------|--------------|
| **ADW220** | Analizador de red trifásico con salida RS-485 (Modbus RTU). |
| **R10** | Gateway IoT de Acrel (permite Modbus RTU ↔ TCP, MQTT, etc.). |
| **PC / Servidor** | Equipo que ejecuta Python y el broker MQTT local. |

### Cableado RS-485
| ADW220 | R10 |
|---------|-----|
| A (+)   | A |
| B (–)   | B |
| GND (opcional) | GND |

> ⚠️ Verifica polaridad y resistencia de terminación (120 Ω) si la línea es larga.

---

## ⚙️ 2. Configuración del R10

1. Conéctate a la **red Wi-Fi del R10** (por defecto `ACREL_R10_XXXX`).
2. Entra en el navegador a:  
   ```
   http://192.168.3.1
   ```
3. Inicia sesión (usuario por defecto `admin / admin`).

### 2.1. Parámetros Modbus RTU
En **Settings → Modbus → RTU**:
- **Baudrate:** 4800 bps  
- **Parity:** None (N)
- **Stop bits:** 1
- **Data bits:** 8
- **Modbus address:** (del ADW220, normalmente `2`)
- **Function:** Input Registers (04) o Holding (03) según manual

### 2.2. Habilitar “Modbus RTU over TCP”
En **Settings → Modbus TCP**:
- **Mode:** `RTU over TCP` ✅  
- **Port:** `502`
- **Unit ID passthrough:** habilitado (para que el gateway use el mismo ID del esclavo)
- **IP del R10:** `192.168.3.1` (por defecto, configurable)

### 2.3. Verificación
Prueba con **Modbus Poll** o **QModMaster** desde tu PC:

```
Protocol: Modbus RTU/ASCII over TCP/IP
IP Address: 192.168.3.1
Port: 502
Slave ID: 2
Function: 04 (Read Input Registers)
Start Address: 256
Quantity: 54
```

Si obtienes valores válidos → la pasarela funciona correctamente.

---

## 🐍 3. Script Python: `rtu_over_tcp_to_mqtt.py`

El siguiente script:
1. Se conecta por Modbus RTU-over-TCP al R10.  
2. Lee 54 registros (27 flotantes IEEE-754).  
3. Los publica como JSON en un broker MQTT local.

### 3.1. Instalación de dependencias
```bash
pip install pymodbus==3.6.6 paho-mqtt==2.1.0 python-dotenv
```

### 3.2. Código
Guarda el siguiente archivo como `rtu_over_tcp_to_mqtt.py`:

```python
<code omitted for brevity>
```

---

## ⚙️ 4. Archivo `.env`

Crea un archivo llamado `.env` en la misma carpeta:

```env
R10_IP=192.168.3.1
R10_PORT=502
SLAVE_ID=2
START_ADDR=256
QUANTITY=54
WORD_ORDER=ABCD
POLL_SEC=1.0

MQTT_HOST=127.0.0.1
MQTT_PORT=1883
MQTT_TOPIC=nilm/adw220/ch1
MQTT_USER=
MQTT_PASS=
MQTT_QOS=0

NAMES=FREQ,U_L1,U_L2,U_L3,U_L1L2,U_L2L3,U_L3L1,I_L1,I_L2,I_L3,I_ZERO,P_L1,P_L2,P_L3,P_SUM,Q_L1,Q_L2,Q_L3,Q_SUM,S_L1,S_L2,S_L3,S_SUM,PF_L1,PF_L2,PF_L3,PF_SUM
```

---

## ▶️ 5. Ejecución

```bash
python rtu_over_tcp_to_mqtt.py
```

El script:
- Se conecta al R10 vía TCP:502  
- Lee los registros Modbus del ADW220  
- Los publica cada `POLL_SEC` segundos en `nilm/adw220/ch1`  

Ejemplo de mensaje publicado:
```json
{
  "ts": "2025-10-09T18:25:32.012Z",
  "slave": 2,
  "start_addr": 256,
  "count": 54,
  "values": {
    "FREQ": 60.001,
    "U_L1": 120.48,
    "U_L2": 119.93,
    "U_L3": 121.01
  }
}
```

---

## ✅ 6. Pruebas y diagnóstico

### MQTT
Verifica recepción con:
```bash
mosquitto_sub -h 127.0.0.1 -t "nilm/adw220/#" -v
```

### Modbus
Si obtienes valores incoherentes (e.g. 3.4e38):
- Cambia `WORD_ORDER=CDAB` en el `.env`
- Reintenta lectura

---

## 📄 7. Integración

Este pipeline permite:
- Usar el R10 solo como **pasarela Modbus** (sin usar su MQTT interno).  
- Centralizar el procesamiento en Python y tu broker local.  
- Guardar y reenviar datos a ThingsBoard, InfluxDB, etc. posteriormente.

---

## 🧠 8. Referencias

- Acrel R10 User Manual  
- Acrel ADW220 Communication Manual  
- Pymodbus 3.6.6 documentation  
- Paho MQTT client 2.1.0  

---

**Autor:** Juan David Muñoz Buriticá  
**Proyecto:** RED Inteligente – NILM y medición trifásica  
**Fecha:** Octubre 2025  
