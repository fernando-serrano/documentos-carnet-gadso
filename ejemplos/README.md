# Flujo Emision de Carnet SUCAMEC

Automatizacion del flujo SEL de SUCAMEC con Playwright, OCR para captcha, cruce de Google Sheets, validacion documental en Google Drive y trazabilidad completa por logs.

## Estado actual (abril 2026)

- El modo operativo principal es scheduled multihilo.
- Se ejecutan workers en paralelo con reserva de filas para evitar colisiones.
- La lectura de Google Sheets tiene reintentos con backoff para tolerar cortes intermitentes.
- La validacion de alertas incluye estado OBSERVADO y estado TRANSMITIDO.
- Cada corrida scheduled crea su propia carpeta de logs (orquestador + workers + consola).

## Objetivo del sistema

Procesar registros pendientes desde hoja de comparacion hasta transmitirlos en bandeja SUCAMEC:

1. Leer hojas remotas (CSV publico).
2. Reservar un registro pendiente para un worker.
3. Validar expediente en Drive por DNI.
4. Completar formulario en CREAR SOLICITUD.
5. Validar alertas de negocio post-Buscar.
6. Reservar y validar secuencia de pago.
7. Guardar solicitud, transmitir en bandeja y actualizar hojas.

## Estructura del proyecto

- `carnet_emision.py`: flujo principal, orquestador y workers.
- `carne_flow.py`: utilidades de apoyo.
- `run_scheduled.bat`: launcher recomendado para scheduled multihilo.
- `run_carnet_emision.bat`: launcher basico.
- `README.md`: documentacion funcional y tecnica.
- `requirements.md`: dependencias Python.
- `logs/`: logs de ejecucion.
- `data/`: cache local y temporales.
- `secrets/carnet-drive-bot.json`: credenciales de cuenta de servicio.
- `test/`: scripts auxiliares.

## Arquitectura real de ejecucion

### 1) Orquestador scheduled multihilo

- Se activa con `RUN_MODE=scheduled` y `SCHEDULED_MULTIWORKER=1`.
- El orquestador crea hasta 4 workers en paralelo (`SCHEDULED_WORKERS`, limitado a 1..4).
- Cada worker se lanza como subproceso Python con variables de entorno propias:
  - `MULTIWORKER_CHILD=1`
  - `WORKER_ID`
  - `WORKER_RUN_ID`

### 2) Worker child

Cada worker ejecuta bucle continuo:

1. Busca candidatos en hoja comparacion.
2. Intenta reservar una fila escribiendo token en `ESTADO_TRAMITE`.
3. Procesa el registro completo (formulario + secuencia + bandeja).
4. Libera reserva de secuencia si aplica.
5. Continua hasta que no queden filas reservables.

### 3) Control de concurrencia por reservas

- Reserva de comparacion:
  - Token tipo `EN_PROCESO|RUN=...|W=...|DNI=...|TS=...`.
  - Verificacion de escritura para confirmar que el worker gano la reserva.
- Reserva de tercera hoja (secuencias):
  - Token tipo `RESERVADO|RUN=...|W=...|DNI=...|TS=...`.
  - Si una secuencia falla, se marca `NO ENCONTRADO` y se toma otra.
  - Si queda reserva colgada, se libera automaticamente cuando corresponde.
- Manejo de reservas expiradas:
  - Se soporta lease por tiempo para recuperar reservas antiguas.

## Flujo operativo del registro

### 1) Inicializacion

1. Carga `.env`.
2. Inicializa logger.
3. Confirma acceso a:
   - `HOJA_BASE`
   - `HOJA_COMPARACION`
   - `HOJA_TERCERA`

### 2) Cruce y preparacion

1. Cruza DNI de comparacion contra base.
2. Resuelve sede, modalidad y tipo de documento.
3. Determina grupo operativo (`JV` o `SELVA`).

### 3) Login y navegacion

1. Login con OCR captcha (con reintentos).
2. Navegacion DSSP -> CARNE -> CREAR SOLICITUD.
3. Confirmacion robusta de vista por campos reales del formulario (`createForm`).

### 4) Validacion de expediente Drive

Antes de llenar el formulario:

1. Ubica carpeta de DNI.
2. Verifica archivos soportados.
3. Descarga y prepara:
   - foto (`.jpg/.jpeg`)
   - DJFUT (`.pdf`)
   - certificado medico (`.pdf`)

Si falla, marca `ERROR EN TRAMITE` y termina el registro.

### 5) Llenado y busqueda por DNI

Orden operativo:

1. Sede.
2. Modalidad.
3. Tipo de registro (normaliza a `INICIAL` si corresponde).
4. Tipo de documento.
5. Ingreso de DNI y click en Buscar.

### 6) Validaciones post-Buscar

Se ejecutan con timeout corto configurable (`CARNET_POST_SEARCH_ALERT_WAIT_MS`):

1. Documento no existe.
2. Carnet vigente en distinta empresa.
3. Subvalidacion de cambio de empresa (carne cesado / ya cuenta con carne nro).
4. Misma modalidad en estado TRANSMITIDO.
5. Misma modalidad en estado OBSERVADO.
6. Prospecto sin curso vigente.
7. Para `INICIAL`, exige autocompletado de nombres y apellidos.

Si alguna alerta bloqueante aplica, se registra `ERROR EN TRAMITE` en comparacion.

### 7) Verificacion de secuencia

1. Worker reserva secuencia libre de tercera hoja.
2. Verifica en SUCAMEC con deteccion multinivel:
   - etiqueta Monto/Fecha
   - buffer growl JS
   - DOM growl
   - HTML completo
3. Resultado:
   - `ENCONTRADO`: continua.
   - `NO_ENCONTRADO`: marca tercera hoja y prueba siguiente.
   - `TIMEOUT`: criterio tolerante, asume exito para no bloquear falsos negativos.

### 8) Cierre transaccional

1. Guardar solicitud en `createForm:botonGuardar`.
2. Comparacion -> estado post-guardar (default `POR TRAMSMITIR`).
3. Tercera hoja -> secuencia `USADO` (+ trazabilidad DNI/nombre).
4. Navegacion a bandeja, filtro `CREADO`, seleccionar todos, transmitir.
5. Confirmacion de modal de transmision.
6. Comparacion -> `TRANSMITIDO` + observacion final.
7. Limpieza de cache local por DNI en `data/cache/upload_tmp/<dni>`.
8. Retorno a CREAR SOLICITUD para siguiente iteracion.

## Logging y trazabilidad

### Carpeta por corrida scheduled

`run_scheduled.bat` crea automaticamente:

- `logs/runs/scheduled_<timestamp>/run_scheduled_<timestamp>.log` (consola general).
- Log del orquestador dentro de la misma carpeta.
- Un archivo por worker: `worker_<id>_batch_<timestamp>.log`.

Esto se logra configurando `LOG_DIR` por corrida antes de ejecutar Python.

### Trazabilidad por worker

- En reservas y errores de tramite se registra responsable con tag de worker:
  - `BOT CARNE SUCAMEC W1`, `W2`, etc.
- Cada log de worker muestra DNI, fila de comparacion y fila de tercera hoja procesada.

### Politica de logs

- Modo archivo unico (`CARNET_LOG_SINGLE_FILE=1`): truncado por lineas con `CARNET_LOG_MAX_LINES`.
- Modo rotativo (`CARNET_LOG_SINGLE_FILE=0`): retencion por cantidad con `CARNET_LOG_ROTATING_KEEP_FILES`.

## Variables de entorno clave

### Ejecucion multihilo (obligatorio para produccion)

- `RUN_MODE=scheduled`
- `SCHEDULED_MULTIWORKER=1`
- `SCHEDULED_WORKERS=4`
- `CARNET_WORKER_SCAN_ROWS=200`
- `CARNET_WORKER_MAX_ROWS=0`

### Robustez de lectura y UI

- `CARNET_GSHEET_READ_RETRIES`
- `CARNET_GSHEET_TIMEOUT_SEC`
- `CARNET_GSHEET_RETRY_BASE_MS`
- `CARNET_CREAR_SOLICITUD_VALIDATION_TIMEOUT_MS`
- `CARNET_POST_SEARCH_ALERT_WAIT_MS`
- `CARNET_OCR_MAX_INTENTOS`

### Reservas y lease

- `CARNET_COMPARE_RESERVA_LEASE_MINUTES`
- `CARNET_COMPARE_ALLOW_STALE_IN_PROGRESS`
- `CARNET_TERCERA_RESERVA_LEASE_MINUTES`
- `CARNET_MAX_SECUENCIA_INTENTOS`

### Hojas y Drive

- `CARNET_GSHEET_URL`
- `CARNET_GSHEET_COMPARE_URL`
- `CARNET_GSHEET_THIRD_URL`
- `DRIVE_ROOT_FOLDER_ID`
- `DRIVE_CREDENTIALS_JSON`

### Estado y cierre

- `CARNET_ESTADO_POST_GUARDAR`
- `CARNET_OBSERVACION_POST_GUARDAR`
- `CARNET_BANDEJA_ESTADO_OBJETIVO`
- `CARNET_OBSERVACION_POST_TRANSMITIR`
- `CARNET_CACHE_CLEAN_ON_SUCCESS`

## Configuracion recomendada en .env

```env
RUN_MODE=scheduled
SCHEDULED_MULTIWORKER=1
SCHEDULED_WORKERS=4
CARNET_WORKER_SCAN_ROWS=200
CARNET_WORKER_MAX_ROWS=0

CARNET_CREAR_SOLICITUD_VALIDATION_TIMEOUT_MS=9000
CARNET_GSHEET_READ_RETRIES=6
CARNET_GSHEET_TIMEOUT_SEC=35
CARNET_GSHEET_RETRY_BASE_MS=800
CARNET_OCR_MAX_INTENTOS=6
```

## Instalacion

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Ejecucion

### Opcion recomendada (scheduled multihilo)

```bat
run_scheduled.bat
```

### Opcion basica

```bat
run_carnet_emision.bat
```

### Opcion directa

```bash
python carnet_emision.py
```

## Modos especiales

- `CARNET_SHEET_CROSSCHECK_ONLY=1`: solo cruce de hojas.
- `CARNET_SHEET_DEMO_ONLY=1`: solo valida acceso a hojas.
- `DRIVE_VALIDATE_ONLY=1`: solo valida acceso a Drive.

## Nuevo flujo Galenius (modular)

Se agrego una base no monolitica para el nuevo flujo de certificados medicos:

- `flows/galenius_flow/config.py`: carga y valida variables de entorno.
- `flows/galenius_flow/selectors.py`: selectores UI centralizados de Galenius.
- `flows/galenius_flow/scraping_utils.py`: barrido de elementos/mensajes por multiples selectores.
- `flows/galenius_flow/main_flow.py`: script unico del flujo (etapa actual: login robusto).
- `flows/galenius_flow/logging_utils.py`: logging por corrida y eventos JSONL.
- `run_galenius.py`: entrypoint unico del flujo Galenius.
- `run.bat`: launcher unico para los flujos principales.
- `scripts/bat/run_galenius_login.bat`: launcher interno de Galenius.

### Variables de entorno para Galenius

Se creo plantilla limpia: `.env.galenius.example`.

Variables minimas para probar login:

- `GALENIUS_URL_LOGIN`
- `GALENIUS_USERNAME`
- `GALENIUS_PASSWORD`

### Ejecucion (script unico)

```bat
run.bat
```

Log generado por corrida:

- `logs/galenius/galenius_flow_YYYYMMDD_HHMMSS/galenius_flow.log`
- `logs/galenius/galenius_flow_YYYYMMDD_HHMMSS/events.jsonl`

## Flujo Foto Carne (separado)

Runner independiente:

- `run_foto_carne.py`
- `run.bat foto_carne`

Launcher interno:

- `scripts/bat/run_foto_carne.bat`

Comportamiento:

- Lee cola desde `BOT DOCUMENTOS` por `DNI`.
- Busca `DNI` en hoja fuente de fotos.
- Descarga desde `Cargar Foto` y guarda JPG en el mismo lote compartido por `run.bat`.
- Actualiza en la hoja `ESTADO FOTO CARNÉ` y `OBSERVACION FOTO CARNÉ`.

Defaults actuales:

- `FOTO_CARNE_WORKERS=4`
- `FOTO_CARNE_MAX_KB=80`
- `FOTO_CARNE_HEADROOM_PCT=0.95`
- `FOTO_CARNE_OVERWRITE_EXISTING=0`
- `FOTO_CARNE_LOTES_DIR=lotes`
- `FOTO_CARNE_LOG_DIR=logs/foto_carne`
- `FOTO_CARNE_MAX_LOTE_DIRS` (si no se define, usa `GALENIUS_MAX_LOTE_DIRS`, default global 10)

Retencion global de lotes:

- El limite de lotes se aplica a toda la carpeta `lotes`.
- No se separa el conteo por tipo de flujo (Galenius vs Foto Carne).

Estados usados por defecto:

- `EN PROCESO W#`
- `DESCARGADO`
- `SIN REGISTROS`
- `ERROR`

## Requisitos Google Cloud

1. Habilitar Google Sheets API para la cuenta de servicio.
2. Compartir las hojas con el `client_email` de la cuenta con rol Editor.
3. Configurar ruta valida en `DRIVE_CREDENTIALS_JSON`.

## Resumen operativo

El sistema esta preparado para operar en paralelo como estrategia principal, con control de colisiones por reservas, reintentos de red para Google Sheets, validaciones de negocio actualizadas (incluyendo estado TRANSMITIDO) y trazabilidad por worker de punta a punta.
