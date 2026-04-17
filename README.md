# Flujos Documentales - Galenius, Foto Carne, DJ FUT y Firma Digital

Este repositorio, en este contexto de trabajo, concentra los flujos documentales de:

- Galenius (certificado medico)
- Foto Carne
- DJ FUT
- Firma Digital

No incluye ni montara en este script el otro flujo de acceso/operacion en SUCAMEC.

## Proposito

Automatizar el tratamiento de documentos medicos para reducir carga operativa manual y asegurar trazabilidad por archivo y por ejecucion.

## Alcance Funcional

La arquitectura contempla 3 tratamientos documentales:

1. Descarga documental por lote desde Galenius.
2. Guardado local con nomenclatura estandarizada y control de unicidad.
3. Post-tratamiento documental (validaciones, normalizacion y consumo por procesos posteriores).

## Estado Actual

Actualmente se esta afinando el tratamiento 1:

- Descarga por lote.
- Guardado local de documentos.
- Trazabilidad de ejecucion por logs.

## Entrada Operativa

La hoja que inicializa el flujo es la pestaña `BOT DOCUMENTOS` del spreadsheet de Galenius.

Campos usados en esta etapa:

- `DNI`
- `ESTADO CERTIFICADO MEDICO`
- `RESPONSABLE`
- `FECHA TRAMITE`

El responsable se establece por defecto como `BOT DOCUMENTOS SUCAMEC` y la fecha de tramite se actualiza con la fecha y hora de la interacion.

## Salida Local

Cada ejecucion crea su propio directorio de lote en la raiz del proyecto:

- `lotes/lote-DD-MM-YYYY-HH-MM-SS/<dni>/certificado_medico_<dni>.pdf`

Si el PDF supera el umbral configurado, el flujo intenta optimizarlo antes de guardarlo.

## Estructura Relevante

- `run_galenius.py`: entrypoint unico del flujo.
- `run_galenius_login.bat`: launcher de Windows para ejecutar el flujo unico.
- `flows/galenius_flow/config.py`: configuracion por variables de entorno.
- `flows/galenius_flow/main_flow.py`: orquestacion del flujo actual.
- `flows/galenius_flow/selectors.py`: selectores de UI y candidatos de scraping.
- `flows/galenius_flow/scraping_utils.py`: utilidades de barrido y deteccion robusta.
- `flows/galenius_flow/logging_utils.py`: logging por corrida y eventos JSONL.
- `ejemplos/.env.galenius.example`: plantilla de variables para este flujo.

## Variables Minimas

Configurar en `.env`:

- `GALENIUS_URL_LOGIN`
- `GALENIUS_USERNAME`
- `GALENIUS_PASSWORD`

Opcionales recomendadas:

- `GALENIUS_OUTPUT_DIR`
- `GALENIUS_MAX_PDF_KB`
- `GALENIUS_BATCH_SIZE`
- `GALENIUS_OVERWRITE_EXISTING`
- `GALENIUS_LOG_DIR`

## Retencion De Auditoria

La carpeta de auditoria conserva como maximo 10 corridas historicas por defecto. Ese limite puede ajustarse con `GALENIUS_AUDIT_MAX_RUN_DIRS`.

## Retencion De Lotes

La carpeta `lotes` conserva como maximo 10 lotes por defecto de forma global (sin separar por tipo de flujo).

Esto significa que el tope se aplica al total combinado de lotes Galenius, Foto Carne, DJ FUT y Firma Digital dentro de `lotes`.

Variables de control del tope:

- `GALENIUS_MAX_LOTE_DIRS` (base global, default 10)
- `FOTO_CARNE_MAX_LOTE_DIRS` (opcional para Foto Carne; si no se define, usa `GALENIUS_MAX_LOTE_DIRS`)
- `DJ_FUT_MAX_LOTE_DIRS` (opcional para DJ FUT; si no se define, usa `GALENIUS_MAX_LOTE_DIRS`)
- `FIRMA_DIGITAL_MAX_LOTE_DIRS` (opcional para Firma Digital; si no se define, usa `GALENIUS_MAX_LOTE_DIRS`)

## Validacion De Peso

El peso maximo del PDF usa un margen de seguridad por debajo del umbral configurado en `GALENIUS_MAX_PDF_KB`, para evitar quedar al limite exacto del valor permitido.

## Ejecucion

```bat
run_galenius_login.bat
```

Por defecto el navegador corre en modo invisible (`GALENIUS_HEADLESS=1`) para evitar ventanas abiertas durante el procesamiento. Si necesitas depurar visualmente, puedes cambiarlo a `0` de forma temporal.

El flujo procesa por defecto con 4 workers en paralelo. Puedes ajustarlo con `GALENIUS_WORKERS`.

## Logs

Por cada corrida se generan archivos en:

- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/galenius_flow.log`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/events.jsonl`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/workers/worker_1/worker_1.log`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/workers/worker_2/worker_2.log`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/workers/worker_3/worker_3.log`
- `logs/galenius/runs/galenius_flow_YYYYMMDD_HHMMSS/workers/worker_4/worker_4.log`

Durante el procesamiento, el estado se registra como `EN PROCESO W#` para identificar qué worker está atendiendo cada fila.

## Nota De Contexto

Este README aplica a los flujos documentales de este repositorio (Galenius, Foto Carne, DJ FUT y Firma Digital).
Cualquier logica del otro flujo (SUCAMEC) se gestiona en otro script/chat y no forma parte de este alcance.

El tratamiento de Foto Carne fue desacoplado y se mantiene en una carpeta separada para evitar acoplamiento con este flujo.

Su lógica es independiente: toma la cola desde `BOT DOCUMENTOS` por `DNI`, busca ese `DNI` en la hoja base de fotos y, si encuentra coincidencia, descarga la URL de `Cargar Foto` para guardarla dentro de su lote propio.

Para ejecutarlo de forma independiente existe un runner separado en la raiz:

- `run_foto_carne.py`
- `run_foto_carne.bat`

Variables principales para Foto Carne:

- `FOTO_CARNE_QUEUE_SHEET_URL` o `GALENIUS_QUEUE_SHEET_URL` para leer la cola desde BOT DOCUMENTOS.
- `FOTO_CARNE_SOURCE_SHEET_URL` para la hoja base donde vive `Cargar Foto`.
- `FOTO_CARNE_DRIVE_CREDENTIALS_JSON` o `DRIVE_CREDENTIALS_JSON` para Google Drive/Sheets.

## Flujo Foto Carne

El flujo Foto Carne se ejecuta por separado, consume la misma cola `BOT DOCUMENTOS` y actualiza en la hoja:

- `ESTADO FOTO CARNÉ`
- `OBSERVACION FOTO CARNÉ`
- `RESPONSABLE`
- `FECHA TRAMITE`

Transiciones de estado durante el proceso:

- `EN PROCESO W#` mientras un worker atiende la fila.
- `DESCARGADO` cuando la foto se guarda correctamente.
- `SIN REGISTROS` cuando no hay foto asociada en la hoja fuente.
- `ERROR` ante fallas de descarga/compresión/procesamiento.

Salida local de Foto Carne:

- `lotes/lote-foto-carne-DD-MM-YYYY-HH-MM-SS/<dni>/foto_carne_<dni>.jpg`

### Defaults del flujo Foto Carne

- `FOTO_CARNE_WORKERS=4` (por defecto, maximo actual 4)
- `FOTO_CARNE_MAX_KB=80`
- `FOTO_CARNE_HEADROOM_PCT=0.95`
- `FOTO_CARNE_OVERWRITE_EXISTING=0`
- `FOTO_CARNE_LOTES_DIR=lotes`
- `FOTO_CARNE_LOG_DIR=logs/foto_carne`
- `FOTO_CARNE_MAX_LOTE_DIRS` usa `GALENIUS_MAX_LOTE_DIRS` por defecto

Nota de compresion:

- La conversion y compresion usan Pillow para intentar mantener formato JPG y cumplir el umbral configurado.

### Variables recomendadas de Foto Carne

- `FOTO_CARNE_QUEUE_SHEET_URL` (si no se define, usa `GALENIUS_QUEUE_SHEET_URL`)
- `FOTO_CARNE_SOURCE_SHEET_URL`
- `FOTO_CARNE_DRIVE_CREDENTIALS_JSON` (si no se define, usa `DRIVE_CREDENTIALS_JSON`)
- `FOTO_CARNE_RESPONSABLE_DEFAULT`
- `FOTO_CARNE_ESTADO_EN_PROCESO`
- `FOTO_CARNE_ESTADO_DESCARGADO`
- `FOTO_CARNE_ESTADO_ERROR`
- `FOTO_CARNE_ESTADO_SIN_REGISTROS`

### Ejecucion de Foto Carne

```bat
run_foto_carne.bat
```

## Flujo DJ FUT

El flujo DJ FUT se ejecuta por separado, consume la misma cola `BOT DOCUMENTOS` y actualiza en la hoja:

- `ESTADO DJ FUT`
- `OBSERVACION DJ FUT`
- `RESPONSABLE`
- `FECHA TRAMITE`

Transiciones de estado durante el proceso:

- `EN PROCESO W#` mientras un worker atiende la fila.
- `DESCARGADO` cuando el PDF se guarda correctamente.
- `SIN REGISTROS` cuando no hay DJ FUT asociado en la hoja fuente.
- `ERROR` ante fallas de descarga/compresion/procesamiento.

Salida local de DJ FUT:

- `lotes/lote-dj-fut-DD-MM-YYYY-HH-MM-SS/<dni>/djfut_<dni>.pdf`

### Logica de fecha en DJ FUT

La edicion de fecha se realiza sobre la tabla inferior izquierda `FECHA DE LA DECLARACION` del PDF.

Criterio aplicado:

- Se extrae DD/MM/AAAA desde la fila de fecha de la tabla inferior izquierda (no desde fechas superiores como aprobacion).
- MM: si el mes original es menor al mes actual, se actualiza al mes actual; si no, se conserva.
- DD: si el dia original es mayor al dia actual de ejecucion, se reemplaza por un valor aleatorio entre `1` y `dia_actual`; si no, se conserva.
- AAAA: siempre se actualiza al anio actual de ejecucion.

Implementacion operativa:

- Primero se descarga y guarda el PDF en disco.
- Luego se edita la fecha sobre el archivo guardado.
- Finalmente se intenta optimizar el peso si supera el umbral.

### Defaults del flujo DJ FUT

- `DJ_FUT_WORKERS=4` (por defecto, maximo actual 4)
- `DJ_FUT_MAX_KB=80`
- `DJ_FUT_HEADROOM_PCT=0.95`
- `DJ_FUT_OVERWRITE_EXISTING=0`
- `DJ_FUT_LOTES_DIR=lotes`
- `DJ_FUT_LOG_DIR=logs/dj_fut`
- `DJ_FUT_MAX_LOTE_DIRS` usa `GALENIUS_MAX_LOTE_DIRS` por defecto
- `DJ_FUT_STRICT_SIZE_LIMIT=0`
- `DJ_FUT_ALLOW_LOSSY=0`
- `DJ_FUT_DATE_EDIT_REQUIRED=1` (por defecto, la edicion de fecha es obligatoria)

### Variables recomendadas de DJ FUT

- `DJ_FUT_QUEUE_SHEET_URL` (si no se define, usa `GALENIUS_QUEUE_SHEET_URL`)
- `DJ_FUT_SOURCE_SHEET_URL`
- `DJ_FUT_DRIVE_CREDENTIALS_JSON` (si no se define, usa `DRIVE_CREDENTIALS_JSON`)
- `DJ_FUT_RESPONSABLE_DEFAULT`
- `DJ_FUT_ESTADO_EN_PROCESO`
- `DJ_FUT_ESTADO_DESCARGADO`
- `DJ_FUT_ESTADO_ERROR`
- `DJ_FUT_ESTADO_SIN_REGISTROS`

### Ejecucion de DJ FUT

```bat
run_dj_fut.bat
```

### Logs de DJ FUT

Por cada corrida se generan archivos en:

- `logs/dj_fut/dj_fut_YYYYMMDD_HHMMSS/dj_fut.log`

## Flujo Firma Digital

El flujo Firma Digital se ejecuta por separado, consume la misma cola `BOT DOCUMENTOS` y actualiza en la hoja:

- `ESTADO FIRMA`
- `OBSERVACIÓN FIRMA`
- `RESPONSABLE`
- `FECHA TRAMITE`

Transiciones de estado durante el proceso:

- `EN PROCESO W#` mientras un worker atiende la fila.
- `PROCESADO` cuando se descarga y normaliza la firma, pero no se ejecuta carga a expediente.
- `CARGADO` cuando la firma se descarga, procesa y carga en expediente (si el hook de carga esta habilitado).
- `REVISAR MANUAL` cuando la imagen no cumple condiciones minimas o existe riesgo de perder trazos.
- `SIN REGISTROS` cuando no hay firma asociada en la hoja fuente.
- `ERROR` ante fallas de descarga, procesamiento o carga.

Salida local de Firma Digital:

- `lotes/lote-firma-digital-DD-MM-YYYY-HH-MM-SS/<dni>/firma_digital_<dni>.png`

Archivo temporal de trazabilidad (opcional):

- `lotes/lote-firma-digital-DD-MM-YYYY-HH-MM-SS/<dni>/firma_digital_<dni>_tmp.png`

### Logica de procesamiento de Firma Digital

El procesamiento prioriza fidelidad del trazo y minimizacion de falsos positivos:

- Descarga desde Google Drive usando el valor de `Cargar Firma Digital` (columna AB o equivalente) en la hoja fuente.
- Validacion de imagen procesable antes de cualquier transformacion.
- Eliminacion/reduccion de fondo usando umbral global + umbral adaptativo.
- Limpieza conservadora de ruido (puntos/manchas) sin eliminar trazos reales.
- Engrosamiento de trazo solo cuando la firma es tenue.
- Recorte por bounding box con margen para mantener contexto del trazo.
- Normalizacion final para legibilidad sobre fondo claro.

Reglas aplicadas:

- No se rota la imagen en ningun caso.
- Se prioriza conservar trazo real sobre mejoras esteticas.
- Si la calidad no es confiable, se marca `REVISAR MANUAL`.
- No se sobrescribe archivo existente por defecto (`FIRMA_DIGITAL_OVERWRITE_EXISTING=0`).

### Carga a expediente

La carga se habilita de forma desacoplada para no acoplar aun los 4 flujos:

- `FIRMA_DIGITAL_UPLOAD_ENABLED=1` activa intento de carga.
- `FIRMA_DIGITAL_UPLOAD_CALLABLE=modulo:funcion` define el hook.

El hook debe aceptar `dni` y `file_path` (por nombre o posicion), y retornar `bool`, `tuple(ok, detail)` o `dict` con resultado.

### Defaults del flujo Firma Digital

- `FIRMA_DIGITAL_WORKERS=4` (por defecto; multihilo con 4 workers)
- `FIRMA_DIGITAL_MAX_KB=80`
- `FIRMA_DIGITAL_HEADROOM_PCT=0.95`
- `FIRMA_DIGITAL_OVERWRITE_EXISTING=0`
- `FIRMA_DIGITAL_LOTES_DIR=lotes`
- `FIRMA_DIGITAL_LOG_DIR=logs/firma_digital`
- `FIRMA_DIGITAL_MAX_LOTE_DIRS` usa `GALENIUS_MAX_LOTE_DIRS` por defecto
- `FIRMA_DIGITAL_STRICT_SIZE_LIMIT=0`
- `FIRMA_DIGITAL_UPLOAD_ENABLED=0`
- `FIRMA_DIGITAL_KEEP_TMP=0` (por defecto no conserva archivo temporal duplicado)

### Variables recomendadas de Firma Digital

- `FIRMA_DIGITAL_QUEUE_SHEET_URL` (si no se define, usa `GALENIUS_QUEUE_SHEET_URL`)
- `FIRMA_DIGITAL_SOURCE_SHEET_URL`
- `FIRMA_DIGITAL_DRIVE_CREDENTIALS_JSON` (si no se define, usa `DRIVE_CREDENTIALS_JSON`)
- `FIRMA_DIGITAL_RESPONSABLE_DEFAULT`
- `FIRMA_DIGITAL_ESTADO_COLUMN`
- `FIRMA_DIGITAL_OBSERVACION_COLUMN`
- `FIRMA_DIGITAL_ESTADO_EN_PROCESO`
- `FIRMA_DIGITAL_ESTADO_PROCESADO`
- `FIRMA_DIGITAL_ESTADO_CARGADO`
- `FIRMA_DIGITAL_ESTADO_REVISION_MANUAL`
- `FIRMA_DIGITAL_ESTADO_ERROR`
- `FIRMA_DIGITAL_ESTADO_SIN_REGISTROS`

### Ejecucion de Firma Digital

```bat
run_firma_digital.bat
```

### Logs de Firma Digital

Por cada corrida se generan archivos en:

- `logs/firma_digital/firma_digital_YYYYMMDD_HHMMSS/firma_digital.log`
