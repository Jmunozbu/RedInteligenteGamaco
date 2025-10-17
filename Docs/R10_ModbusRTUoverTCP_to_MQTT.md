# 📘 Configuración y lectura del ADW220 a través del R10 en modo Modbus RTU-over-TCP

## Objetivo
Este documento describe la configuración necesaria para utilizar el **R10** como pasarela (“gateway”) entre el **analizador ADW220** (Modbus RTU) y un computador o servidor que lea los datos vía **Modbus RTU-over-TCP** y los publique en un **broker MQTT local**.

---

## 1. Conexión física

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

---

## 2. Configuración del R10

1. Conexión a la **red Wi-Fi del R10** o cable Ethernet.
2. Entrar en el navegador a:  
   ```
   http://192.168.3.1
   ```
3. Iniciar sesión.

### 2.1. Parámetros Modbus RTU
En **Settings → Modbus → RTU**:
- **Baudrate:** 4800 bps  
- **Parity:** None (N)
- **Stop bits:** 1
- **Data bits:** 8
- **Modbus address:** `2`
- **Function:** Input Registers (04) o Holding (03) según manual

### 2.2. Habilitar “Modbus RTU over TCP”
En **Settings → Modbus TCP**:
- **Mode:** `RTU over TCP`  
- **Port:** `502`
- **Unit ID passthrough:** habilitado (para que el gateway use el mismo ID del esclavo)
- **IP del R10:** `192.168.3.1` (por defecto, configurable)

### 2.3. Verificación
Prueba con **Modbus Poll** o **QModMaster** desde el PC:

```
Protocol: Modbus RTU/ASCII over TCP/IP
IP Address: 192.168.3.1
Port: 502
Slave ID: 2
Function: 04 (Read Input Registers)
Start Address: 256
Quantity: 54
```

Si se obtienen valores válidos entonces la pasarela funciona correctamente.

---

## 3. Script Python: `rtu_to_mqtt_raw.py`

El siguiente script:
1. Se conecta por Modbus RTU-over-TCP al R10.  
2. Lee los registros configurados en el mapeo.  
3. Los publica como JSON en el broker MQTT local.

### 3.1. Instalación de dependencias
```bash
pip install requirements.txt
```
---

## 4. Archivo `.env`

Con un archivo llamado `.env` en la misma carpeta:

```env
MAPPING_PATH=C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\meta\mapping_registers.json
AUTO_RANGE_FROM_MAPPING=1
OUT_DIR=C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\dataset_nilm\raw
OUTPUT_MODE=wide            
DEBUG_NDJSON=0             
DISABLE_DEDUP=0             
MAX_GAP_TO_MERGE=0        
MAX_PER_REQ=110          

MQTT_HOST=192.168.3.205
MQTT_PORT=1883

R10_IP=192.168.3.1
R10_PORT=502
SLAVE_ID=2
FUNC=4                   
WORD_ORDER=ABCD           
POLL_SEC=10               

PUB_MQTT_TOPIC=sensors/adw220/dev01/raw
PUB_MQTT_USER=
PUB_MQTT_PASS=             
PUB_MQTT_QOS=0
PUB_MQTT_CLIENT_ID=adw_rtu_bridge_dev01        

SUB_MQTT_TOPIC=sensors/adw220/+/raw
SUB_MQTT_USER=
SUB_MQTT_PASS=
SUB_MQTT_QOS=0
SUB_MQTT_CLIENT_ID=adw_ndjson_sub             
```

---

## 5. Ejecución

```bash
python rtu_to_mqtt_raw.py
```

El script:
- Se conecta al R10 vía TCP:502  
- Lee los registros Modbus del ADW220  
- Los publica cada `POLL_SEC` segundos en `PUB_MQTT_TOPIC`  

Ejemplo de mensaje publicado:
```json
{
  "ts": "2025-10-15T14:01:02.123456+00:00",
  "device": "adw220",
  "slave": 2,
  "word_order": "ABCD",
  "mode": "blocks",
  "blocks": [
    {
      "start_addr": 256,
      "count": 40,
      "regs": [123, 456, 789, ...],
      "func": 4
    },
    {
      "start_addr": 825,
      "count": 3,
      "regs": [98, 77, 12],
      "func": 4
    }
  ]
}
```

---

## 6. Integración

Este pipeline permite:
- Usar el R10 solo como **pasarela Modbus** (sin usar su MQTT interno).  
- Centralizar el procesamiento en Python y broker local.  
- Guardar y reenviar datos donde sean necesarios posteriormente.

---

## 7. Referencias

- R10 User Manual  
- Acrel ADW220 Communication Manual  
- Pymodbus 3.6.6 documentation  
- Paho MQTT client 2.1.0  

---

**Autor:** Juan David Muñoz Buriticá  
**Proyecto:** RED Inteligente – NILM y medición trifásica  
**Fecha:** Octubre 2025  
