# ADW220 → Modbus RTU-over-TCP → MQTT → NDJSON/Parquet
**Fecha:** 2025-10-10  
**Autor:** Pipeline acordado en conversación (ADW220 sin R10, registros continuos CH1, sin THD)

---

## 1) Resumen del flujo
```
ADW220 → (Modbus RTU-over-TCP) → rtu_over_tcp_to_mqtt.py → MQTT topic (crudo)
                            ↓
                   mqtt_to_ndjson.py (mapea + decodifica + dedup + rotación diaria)
                            ↓
               dataset_nilm/raw/telemetry_YYYY-MM-DD.ndjson  (+ meta_YYYY-MM-DD.json)
                            ↓
          ndjson_to_parquet.py → dataset_nilm/parquet/telemetry_YYYY-MM-DD_{wide|tidy}.parquet
```
- **Publisher** (`rtu_over_tcp_to_mqtt.py`): lee registros definidos en `meta/mapping_registers.json` y publica **crudo** por registro (addr hex/dec + words) en MQTT. **No** incluye etiquetas.
- **Subscriber** (`mqtt_to_ndjson.py`): lee del topic crudo, aplica el **mapping**, reconstruye `float32`, **deduplica** por hash de contenido y escribe **NDJSON** (rotación diaria) en `dataset_nilm/raw`.
- **Batch** (`ndjson_to_parquet.py`): convierte NDJSON → **Parquet** (modo **wide** o **tidy**).

---

## 2) Estructura de carpetas
```
.
├─ meta/
│  └─ mapping_registers.json       # mapping completo (continuos CH1, sin THD) + semántica (phase, quantity, unit, etc.)
├─ dataset_nilm/
│  ├─ raw/
│  │  ├─ telemetry_YYYY-MM-DD.ndjson
│  │  └─ meta_YYYY-MM-DD.json      # snapshot del mapping del día (para reproducibilidad)
│  └─ parquet/
│     ├─ telemetry_YYYY-MM-DD_wide.parquet
│     └─ telemetry_YYYY-MM-DD_tidy.parquet
├─ rtu_over_tcp_to_mqtt.py         # publisher (Modbus→MQTT crudo)
├─ mqtt_to_ndjson.py               # subscriber (MQTT→NDJSON mapeado + dedup + rotación)
└─ ndjson_to_parquet.py            # batch NDJSON→Parquet
```

---

## 3) Variables de entorno recomendadas
Crea un `.env` o define variables en tu entorno/sistema:

**Publisher (`rtu_over_tcp_to_mqtt.py`)**
- `MAPPING_PATH=meta/mapping_registers.json`
- `RTU_TCP_HOST=192.168.3.1` (gateway/bridge RTU-over-TCP)
- `RTU_TCP_PORT=502`
- `UNIT_ID=1`
- `DEVICE_NAME=adw220-dev01`
- `MQTT_BROKER=localhost`
- `MQTT_PORT=1883`
- `MQTT_TOPIC=sensors/adw220/dev01/raw`
- `POLL_SECONDS=1` (periodo de sondeo)

**Subscriber (`mqtt_to_ndjson.py`)**
- `MAPPING_PATH=meta/mapping_registers.json`
- `OUT_DIR=dataset_nilm/raw`
- `MQTT_BROKER=localhost`
- `MQTT_PORT=1883`
- `MQTT_SUB=sensors/adw220/+/raw`
- `OUTPUT_MODE=wide`  (`wide` o `tidy`)

---

## 4) ¿Cómo usar cada componente?

### 4.1 Publisher: `rtu_over_tcp_to_mqtt.py`
- **Rol**: leer los registros indicados en el mapping y publicar **crudo** en MQTT.
- **Se ejecuta**: en continuo (como servicio o proceso permanente).
- **Rotación**: no aplica (publica en MQTT).

**Ejemplo (Linux)**
```bash
export MAPPING_PATH=meta/mapping_registers.json
export RTU_TCP_HOST=192.168.3.1
export RTU_TCP_PORT=502
export UNIT_ID=1
export DEVICE_NAME=adw220-dev01
export MQTT_BROKER=localhost
export MQTT_PORT=1883
export MQTT_TOPIC=sensors/adw220/dev01/raw
export POLL_SECONDS=1

python rtu_over_tcp_to_mqtt.py
```

### 4.2 Subscriber: `mqtt_to_ndjson.py`
- **Rol**: suscribir al topic crudo, aplicar mapping (etiquetas, phase, quantity), **deduplicar** y **persistir NDJSON** (rotación diaria).
- **Se ejecuta**: en continuo (como servicio o proceso permanente).
- **Rotación**: **diaria**, un archivo por día: `telemetry_YYYY-MM-DD.ndjson`. Adicionalmente guarda `meta_YYYY-MM-DD.json` una vez por día.

**Ejemplo (Linux)**
```bash
export MAPPING_PATH=meta/mapping_registers.json
export OUT_DIR=dataset_nilm/raw
export MQTT_BROKER=localhost
export MQTT_PORT=1883
export MQTT_SUB=sensors/adw220/+/raw
export OUTPUT_MODE=wide   # o tidy

python mqtt_to_ndjson.py
```

> **Deduplicado**: si el contenido de las mediciones es idéntico a la lectura anterior, **no se escribe** una nueva línea. Evita inflar el dataset cuando no hay cambios.

### 4.3 Batch: `ndjson_to_parquet.py`
- **Rol**: convertir el NDJSON del día a Parquet.
- **Se ejecuta**: por lotes (tarea programada diaria o bajo demanda).
- **Rotación**: un Parquet por día y por modo (`wide` y/o `tidy`).

**Ejemplos**
```bash
# Wide → Parquet (día específico)
python ndjson_to_parquet.py   --in-dir dataset_nilm/raw   --out-dir dataset_nilm/parquet   --day 2025-10-10   --mode wide

# Tidy → Parquet (día específico)
python ndjson_to_parquet.py   --in-dir dataset_nilm/raw   --out-dir dataset_nilm/parquet   --day 2025-10-10   --mode tidy
```

> Si existieran múltiples NDJSON del mismo día (p.ej. `telemetry_YYYY-MM-DD_*.ndjson`), el script **los fusiona** automáticamente antes de escribir el Parquet.

---

## 5) Política de rotación y cuándo correr cada cosa

### Procesos en continuo
- **Publisher** (`rtu_over_tcp_to_mqtt.py`): siempre encendido.
- **Subscriber** (`mqtt_to_ndjson.py`): siempre encendido (gestiona la rotación diaria por nombre de archivo).

### Tareas por lotes (diarias)
- **Conversión a Parquet** (`ndjson_to_parquet.py`):
  - **Cuándo**: cada noche, poco después de medianoche local, para convertir el **día anterior**.
  - **Qué**: genera 1 o 2 archivos:
    - `telemetry_YYYY-MM-DD_wide.parquet` (si haces dashboards/plots)
    - `telemetry_YYYY-MM-DD_tidy.parquet` (si harás ML por fase / análisis granular)

#### Ejemplo CRON (Linux, zona horaria del sistema)
Convertir el **día anterior** a las 00:10 cada día:
```cron
10 0 * * * cd /ruta/a/proyecto && DAY=$(date -d "yesterday" +\%F) && python ndjson_to_parquet.py --in-dir dataset_nilm/raw --out-dir dataset_nilm/parquet --day "$DAY" --mode wide && python ndjson_to_parquet.py --in-dir dataset_nilm/raw --out-dir dataset_nilm/parquet --day "$DAY" --mode tidy
```

#### Ejemplo Windows (Task Scheduler)
- **Trigger**: Diario a las 00:10.
- **Action**: `Program/script`: `python`
- **Add arguments**:
```
ndjson_to_parquet.py --in-dir "C:uta\dataset_nilmaw" --out-dir "C:uta\dataset_nilm\parquet" --day $(Get-Date).AddDays(-1).ToString('yyyy-MM-dd') --mode wide
```
(Agregar otra acción para `--mode tidy` o crear una segunda tarea).

---

## 6) Wide vs Tidy (qué guardar y para qué)
- **Wide**: compacto y óptimo para gráficos/consultas rápidas. La semántica (phase, unit, quantity) está en el **mapping** (y snapshot diario `meta_YYYY-MM-DD.json`).
- **Tidy**: **autodescriptivo** (cada fila tiene phase, quantity, unit, etc.). Ideal para NILM y *groupbys* por fase. Más pesado.

**Recomendación**: producir **ambos** diariamente; usar **wide** para dashboards y **tidy** para analítica/ML.

---

## 7) Notas sobre mapping y semántica
- `meta/mapping_registers.json` define **registros**, **etiquetas** y **semántica** (phase/quantity/unit/channel/group,…).
- El suscriptor escribe un **snapshot** por día en `dataset_nilm/raw/meta_YYYY-MM-DD.json`.
- Si cambias el mapping (p.ej. añades THD), incrementa `mapping_version` y conservarás trazabilidad.

---

## 8) Verificación rápida (smoke tests)
- **Publisher**: en MQTT Explorer deberías ver mensajes JSON con `regs` y palabras por cada dirección.
- **Subscriber**: debería crear `dataset_nilm/raw/telemetry_YYYY-MM-DD.ndjson` y (primera escritura del día) `meta_YYYY-MM-DD.json`.
- **Parquet**: `python -c "import pandas as pd; print(pd.read_parquet('dataset_nilm/parquet/telemetry_YYYY-MM-DD_wide.parquet').head())"`

---

## 9) Checklist de operación
- [ ] `mapping_registers.json` correcto y consistente con el ADW220.
- [ ] Publisher y Subscriber corriendo (servicios o procesos).
- [ ] NDJSON rotando por día en `dataset_nilm/raw`.
- [ ] Cron/Task para Parquet (wide y tidy) sobre el **día anterior**.
- [ ] Backups/retención: defínelos al nivel de NDJSON y/o Parquet según espacio disponible.

---

## 10) Preguntas frecuentes
- **¿Y si no quiero guardar NDJSON?**  
  Puedes escribir directo a Parquet, pero NDJSON es más robusto frente a fallos y portable para depuración/exportación.
- **¿Cómo agrego THD/armónicos?**  
  Añade entradas en `mapping_registers.json` con `group: "thd"` o `"harmonics"`, ajusta `dtype/scale`, y el suscriptor los incluirá.
- **¿Cómo cambio la tasa de muestreo?**  
  Modifica `POLL_SECONDS` en el publisher.

---

## 11) Comandos de uso rápido

**Iniciar publisher (Linux)**
```bash
POLL_SECONDS=1 MQTT_BROKER=localhost python rtu_over_tcp_to_mqtt.py
```

**Iniciar subscriber (Linux)**
```bash
OUTPUT_MODE=wide python mqtt_to_ndjson.py
```

**Convertir a Parquet (día actual)**
```bash
python ndjson_to_parquet.py --in-dir dataset_nilm/raw --out-dir dataset_nilm/parquet --day $(date +%F) --mode wide
python ndjson_to_parquet.py --in-dir dataset_nilm/raw --out-dir dataset_nilm/parquet --day $(date +%F) --mode tidy
```

---

## 12) Próximos pasos sugeridos
- Añadir **CH2–CH4** en el mapping (offsets) si vas a medir más circuitos.
- Incorporar **THD** y (opcional) **armónicos** como otro *group*.
- Añadir **métricas de calidad** (por ejemplo, validar `range_engineering` antes de escribir).

---

**Fin del documento.**
