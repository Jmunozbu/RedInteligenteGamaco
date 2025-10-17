# Guía rápida - Broker MQTT local con Mosquitto en Windows (config final)

**Fecha:** 2025-10-07

Esta guía resume exactamente el funcionamiento de un **broker Mosquitto en Windows**, con:
- Archivo de configuración en `C:\Program Files\mosquitto\mosquitto.conf`.
- **Sin logs a archivo** (solo `log_type all`).
- Autenticación habilitada (`allow_anonymous false`) con `passwd.txt`.
- Persistencia habilitada con carpeta `persist`.
- Servicio de Windows configurado para usar ese `mosquitto.conf`.
- Reglas de firewall para permitir el puerto 1883 y (opcional) salidas.
- Configuración de ACLs (Access Control Lists) para los usuarios y tópicos relacionados.

---

## 1) Contenido de archivos (estado final)

### 1.1 `C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\mosquitto.conf`
```conf
listener 1883

allow_anonymous false
password_file C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt

persistence true
persistence_location C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\persist

acl_file C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\acls.conf

log_type all
# Intencionalmente sin 'log_dest file ...' para evitar bloqueos/ACL en Windows
```

### 1.2 `C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt`
- Archivo generado con `mosquitto_passwd` (hashes PBKDF2). **No editar a mano**.
- Debe existir antes de iniciar el servicio si `allow_anonymous = false`.

### 1.3 `C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\persist`
- Carpeta donde Mosquitto guarda la persistencia. Debe existir.

---

## 2) Preparación de carpetas y credenciales

> Se ejecuta PowerShell **como Administrador**.

```powershell
# Crear carpeta ProgramData de Mosquitto y persistencia
New-Item -ItemType Directory -Force "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data" | Out-Null
New-Item -ItemType Directory -Force "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\persist" | Out-Null

# Crear/actualizar usuario y contraseña (no interactivo)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -b -c "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" r10user TU_PASS
#  (Para añadir más usuarios o cambiar la clave de uno existente)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -b "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" otro_user NUEVA_PASS
#  (Eliminar un usuario)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -D "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" r10user
#  (Actualizar el formato/hasheo del archivo si fuera necesario)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -U "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt"
```

---

## 3) Servicio de Windows - apuntar al `mosquitto.conf` correcto

> En algunas versiones de Mosquitto para Windows **no existe** el subcomando `install`.

```powershell
# Detener cualquier instancia del servicio y proceso suelto
net stop mosquitto
taskkill /IM mosquitto.exe /F 2>$null

# Inicar el servicio apuntando al conf modificado
```powershell
& "C:\Program Files\mosquitto\mosquitto.exe" -c "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\mosquitto.conf" -v

# Iniciar servicio
net start mosquitto
```

**Verificación del proceso/argumentos y escucha:**
```powershell
# Debe mostrar LISTENING en 1883
netstat -ano | findstr :1883

---

## 4) Reglas de firewall

### 4.1 Entrada (Inbound) 1883/TCP
```powershell
New-NetFirewallRule -DisplayName "Mosquitto MQTT Inbound 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow
```

### 4.2 (Opcional) Salida (Outbound) por **aplicación**
```powershell
New-NetFirewallRule `
  -DisplayName "Mosquitto MQTT Outbound (App)" `
  -Direction Outbound `
  -Program "C:\Program Files\mosquitto\mosquitto.exe" `
  -Action Allow
```

### 4.3 (Opcional) Salida por **puerto**
```powershell
# MQTT sin TLS
New-NetFirewallRule -DisplayName "Mosquitto MQTT Outbound 1883" -Direction Outbound -Protocol TCP -RemotePort 1883 -Action Allow
# MQTT con TLS (si más adelante se activa)
New-NetFirewallRule -DisplayName "Mosquitto MQTT Outbound 8883" -Direction Outbound -Protocol TCP -RemotePort 8883 -Action Allow
```

---

## 5) Pruebas de funcionamiento

### 5.1 Prueba en **primer plano** (útil para ver errores inmediatamente)
```powershell
# Cierra servicio e instancias si fuera necesario
net stop mosquitto
taskkill /IM mosquitto.exe /F 2>$null

# Ejecutar en primer plano con verbosidad
& "C:\Program Files\mosquitto\mosquitto.exe" -c "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\mosquitto.conf" -v
```
En **otra** consola, se prueba autenticación:
```powershell
# 1) Debe FALLAR (sin credenciales)
mosquitto_sub -h 127.0.0.1 -p 1883 -t test/topic

# 2) Debe FALLAR (clave incorrecta)
mosquitto_sub -h 127.0.0.1 -p 1883 -u r10user -P wrong -t test/topic

# 3) Debe CONECTAR (clave correcta)
mosquitto_sub -h 127.0.0.1 -p 1883 -u r10user -P TU_PASS -t test/topic
mosquitto_pub -h 127.0.0.1 -p 1883 -u r10user -P TU_PASS -t test/topic -m "hola"
```

### 5.2 Prueba contra el **servicio**
```powershell
# Arrancar de nuevo el servicio
net start mosquitto

# Debe mostrar LISTENING
netstat -ano | findstr :1883

# Repite las 3 pruebas de autenticación arriba (deben comportarse igual)
```

---

## 6) Gestión de usuarios (resumen)

```powershell
# Crear archivo y primer usuario (no interactivo)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -b -c "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" r10user TU_PASS

# Añadir/cambiar clave de usuario
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -b "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" usuario NUEVA_PASS

# Eliminar usuario
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -D "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt" usuario

# Actualizar formato/hasheo del passwd.txt (opcional)
& "C:\Program Files\mosquitto\mosquitto_passwd.exe" -U "C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\mosquitto_data\passwd.txt"

# Tras cambios de usuarios/clave, reinicia el servicio (recomendado)
net stop mosquitto
net start mosquitto
```
---
