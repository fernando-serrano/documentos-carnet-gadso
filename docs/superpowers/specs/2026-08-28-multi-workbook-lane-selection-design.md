# Selección de workbook (lane) para evitar colisiones entre 2 dispositivos

Fecha: 2026-08-28
Autor: Fernando Serrano (con Claude Code)
Estado: Implementado. Ver "Actualización 2026-08-28" al final — el criterio
de disponibilidad de una lane cambió de "¿tiene EN PROCESO?" (con
vencimiento) a "¿le quedan filas con DNI y alguna columna de estado
vacía?", más simple y sin necesitar vencimiento. El resto del documento
describe el diseño original (útil como contexto), pero el mecanismo real
en producción es el de la actualización.

## 1. Contexto y problema

El proyecto orquesta 4 flujos (`run_galenius.py`, `run_foto_carne.py`,
`run_dj_fut.py`, `run_firma_digital.py`) vía `run.bat`, y todos leen/escriben
sobre **una sola hoja de cola compartida** ("BOT DOCUMENTOS", la URL de
`GALENIUS_QUEUE_SHEET_URL`, con fallback desde los otros 3 flujos vía
`os.getenv("X_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))`).
Cada flujo escribe su propia columna de estado en esa cola:

| Flujo | Columna de estado | Env var de "en proceso" |
|---|---|---|
| Galenius | `ESTADO CERTIFICADO MEDICO` | `GALENIUS_ESTADO_EN_PROCESO` (default `EN PROCESO`) |
| Foto Carné | `ESTADO FOTO CARNÉ` | `FOTO_CARNE_ESTADO_EN_PROCESO` (default `EN PROCESO`) |
| DJ FUT | `ESTADO DJ FUT` | `DJ_FUT_ESTADO_EN_PROCESO` (default `EN PROCESO`) |
| Firma Digital | `ESTADO FIRMA` / `ESTADO FIRMA DIGITAL` | `FIRMA_DIGITAL_ESTADO_EN_PROCESO` (default `EN PROCESO`) |

Cada `run_*.py` lee **toda** la cola una sola vez al arrancar (p.ej.
[run_foto_carne.py:329](../../../run_foto_carne.py#L329)) y recién marca
`EN PROCESO` fila por fila mientras procesa — no hay filtro de estado ni
reclamo atómico antes de construir la lista de trabajo. Cuando el mismo
`run.bat` corre en 2 dispositivos casi al mismo tiempo, ambos leen la misma
foto de la cola antes de que el otro haya marcado nada: se duplica el
procesamiento de los mismos DNI o falla la escritura porque la fila ya fue
modificada por el otro dispositivo.

Para mitigar esto se habilitó una **segunda pestaña dentro del mismo
spreadsheet** (mismo ID de archivo, `1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U`,
distinto `gid`: la pestaña actual es `gid=214579984` y la nueva es
`gid=1779457178`), con los mismos campos que la cola actual, pensada como una
"segunda cola" (lane B) a la que un dispositivo puede saltar si detecta que
la cola principal (lane A) ya está siendo trabajada. Las hojas "fuente" de
cada flujo (fotos, documentos DJ FUT, firma) no se duplican — son el mismo
catálogo de referencia para ambas colas, y no son donde ocurre la colisión
(la colisión es únicamente sobre las columnas de estado de la cola).

## 2. Objetivo

Antes de que un `run.bat` arranque cualquiera de los 4 flujos, decidir **una
sola vez, para toda la corrida**, cuál de las 2 pestañas de cola (lane A o
lane B, mismo spreadsheet, distinto `gid`) usar:

- Si la lane A (`gid=214579984`, la actual/production) no tiene ninguna fila
  marcada `EN PROCESO` en ninguna de sus 4 columnas de estado → usar A
  (comportamiento actual, sin cambios).
- Si A tiene registros en proceso → revisar la lane B (`gid=1779457178`). Si B
  está libre → usar B.
- Si **ambas** están ocupadas → abortar la corrida completa sin tocar ninguna
  hoja, y enviar un correo de aviso por el mismo canal que ya usa el proyecto
  para notificaciones (Microsoft Graph).

## 3. No objetivos

- No se reemplaza el marcado de estado por fila existente (`EN PROCESO` →
  `DESCARGADO`/`ERROR`/etc.) — sigue igual dentro de cada flujo.
- No se implementa reclamo atómico por fila (optimistic lock estilo
  `_intentar_reservar_registro_compare` de `ejemplos/carnet_emision.py`). Es
  una mejora conocida para un futuro; queda fuera de este alcance porque el
  usuario pidió específicamente el mecanismo de selección de lane.
- No se decide la lane por flujo individual — la decisión es una sola para
  los 4 flujos de esa corrida (confirmado con el usuario).
- No se duplican las hojas "fuente" de cada flujo — siguen siendo las mismas
  para ambas lanes (confirmado: la lane B es una pestaña nueva en el mismo
  spreadsheet, no un archivo separado).
- No se resuelve el caso de arranques simultáneos exactos (ambos dispositivos
  leen "A libre" en el mismo instante). Es una ventana de carrera que ya
  existía y que este diseño no elimina del todo — solo reduce drásticamente
  la probabilidad frente al esquema actual (sin ningún tipo de chequeo).

## 4. Diseño

### 4.1 Script selector: `scripts/select_workbook.py`

Nuevo script standalone, invocado por `run.bat` antes de arrancar cualquier
flujo (dentro de `:prepare_shared_lote`, antes de `run_galenius.bat`).

Responsabilidades:

1. Cargar `.env` (igual que los `run_*.py`).
2. Para la lane A: leer la cola (`GALENIUS_QUEUE_SHEET_URL`) con
   `flows.common.sheets.read_sheet_rows` (lectura pública vía CSV export, sin
   credenciales de escritura) y revisar las 4 columnas de estado listadas en
   §1. Se considera "en proceso" cualquier valor cuya versión normalizada
   (mismo `normalizar_columna` ya usado en el proyecto) **empiece con** el
   valor normalizado de la env var `*_ESTADO_EN_PROCESO` correspondiente
   (para tolerar sufijos como `EN PROCESO W1`).
3. Si A no tiene ninguna fila en proceso → imprimir `A` a stdout, exit 0.
4. Si A tiene alguna → repetir el mismo chequeo sobre la lane B, usando la
   variable de entorno `GALENIUS_QUEUE_SHEET_URL_B` (ver §4.2). Si B falta
   configurar (variable vacía) se trata como "no disponible" → pasa directo
   al caso de aborto (§4.3), registrando el motivo.
5. Si B está libre → imprimir `B` a stdout, exit 0.
6. Si B también tiene registros en proceso (o no está configurado) → aborto
   (§4.3): imprimir nada útil a stdout, exit code `3`.

Este script es puramente de lectura salvo el envío de correo en el caso de
aborto — no escribe en ninguna hoja, así que no hay riesgo de dejar un
estado a medio escribir si falla a mitad de camino.

### 4.2 Variable `.env` nueva (lane B)

Una sola variable obligatoria, mismo patrón que la existente con sufijo `_B`:

- `GALENIUS_QUEUE_SHEET_URL_B` = `https://docs.google.com/spreadsheets/d/1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U/edit?gid=1779457178#gid=1779457178`
  (la cola compartida de la lane B; los otros 3 flujos caen a esta por
  defecto, igual que hoy con la lane A vía `GALENIUS_QUEUE_SHEET_URL`).

Opcional (mismo patrón de override que ya existe hoy para la lane A, por si
algún flujo alguna vez deja de compartir cola con Galenius):
`FOTO_CARNE_QUEUE_SHEET_URL_B`, `DJ_FUT_QUEUE_SHEET_URL_B`,
`FIRMA_DIGITAL_QUEUE_SHEET_URL_B`.

No hace falta ninguna variante `_B` de las hojas fuente (`FOTO_CARNE_SOURCE_SHEET_URL`,
`DJ_FUT_SOURCE_SHEET_URL`, `FIRMA_DIGITAL_SOURCE_SHEET_URL`) — son las mismas
para ambas lanes (§3).

### 4.3 Aborto por ambos workbooks ocupados

El propio `scripts/select_workbook.py` arma y envía el correo (reutilizando
`flows.notifications.mail_config.load_mail_config`/`graph_mail_enabled` y
`flows.notifications.graph_client.send_mail`, el mismo canal que ya usa
`enviar_lote.py`). Cuerpo del correo: nombre del dispositivo (`%COMPUTERNAME%`
si está disponible), timestamp, y qué filas/estado se detectaron ocupados en
cada workbook (para diagnóstico rápido). Si el envío de correo falla (config
faltante o error de Graph), se loguea la excepción pero el script igual
termina con exit code 3 — el aborto de la corrida no depende de que el correo
se envíe con éxito.

### 4.4 Wiring en `run.bat`

Dentro de `:prepare_shared_lote` (se ejecuta una sola vez por corrida, ya sea
`run.bat all` o un flujo individual), antes de crear `GLOBAL_LOTE_DIR`:

```
for /f "delims=" %%W in ('".venv\Scripts\python.exe" scripts\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
if not defined ACTIVE_WORKBOOK (
  echo [ERROR] No se pudo determinar workbook activo ^(ambos ocupados o error^).
  exit /b 3
)
```

`ACTIVE_WORKBOOK` queda seteado en el entorno del proceso `run.bat`, por lo
que es heredado automáticamente por cada `run_*.bat`/`run_*.py` que se invoque
después, sin tocar el resto de `run.bat`.

### 4.5 Cambio mínimo en cada `load_*_config()`

Cada loader (`load_foto_carne_config`, `load_galenius_config`, análogos en
DJ FUT/Firma Digital) ya arma su URL de **cola** con el patrón:

```python
queue_sheet_url = str(os.getenv("FOTO_CARNE_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
```

(en `flows/galenius_flow/config.py` es un poco distinto porque Galenius es la
fuente del fallback: `queue_sheet_url = str(os.getenv("GALENIUS_QUEUE_SHEET_URL", <default hardcodeado>)).strip()`).

Se agrega, antes de resolver la URL de cola, un sufijo condicional — **solo
afecta la cola, nunca la hoja fuente** (`source_sheet_url` queda intacta en
los 4 loaders):

```python
suffix = "_B" if os.getenv("ACTIVE_WORKBOOK", "A").strip().upper() == "B" else ""
queue_sheet_url = str(os.getenv(f"FOTO_CARNE_QUEUE_SHEET_URL{suffix}", os.getenv(f"GALENIUS_QUEUE_SHEET_URL{suffix}", ""))).strip()
```

Es aditivo: sin `ACTIVE_WORKBOOK` seteado (o con valor `A`), el
comportamiento y las variables leídas son exactamente las de hoy.

### 4.6 Helper compartido

La lógica de "¿esta cola tiene alguna fila en proceso?" (§4.1 punto 2) se
extrae a una función reutilizable en `flows/common/workbook_lock.py`
(`hay_registros_en_proceso(queue_url) -> bool`) para poder testearla de forma
aislada y evitar duplicar la lista de columnas/estado entre el script CLI y
los tests.

## 5. Manejo de errores

- Falla de lectura de red al consultar A o B (timeout, sheet inaccesible):
  se trata igual que "ocupado" (conservador: si no se puede confirmar que
  está libre, no se asume libre) y se loguea el motivo específico en el
  correo de aborto.
- `GALENIUS_QUEUE_SHEET_URL_B` no configurado: no es un error de arranque —
  simplemente implica que no hay fallback y cualquier ocupación de A dispara
  aborto directo.
- Credenciales de Google Sheets: no se necesitan para este script (solo
  lectura pública CSV, igual que el resto del proyecto).

## 6. Testing

- Unit tests de `hay_registros_en_proceso` y de la función de decisión
  (A libre / A ocupado+B libre / ambos ocupados / URL B ausente), mockeando
  `read_sheet_rows` para no depender de red real.
- Prueba manual: correr `select_workbook.py` suelto contra las hojas reales
  (o copias de prueba) marcando manualmente una fila `EN PROCESO` para
  confirmar que detecta y salta a B.
- Prueba manual de `run.bat` completo con `ACTIVE_WORKBOOK` forzado a `B` para
  confirmar que los 4 flujos leen/escriben en las URLs `_B`.

## Actualización 2026-08-28: de "EN PROCESO + vencimiento" a "filas pendientes"

Tras implementar el diseño original se detectaron dos problemas en uso real:

1. **Falsos positivos de "ambas ocupadas".** Una corrida interrumpida (dos
   dispositivos compitiendo por la misma cuota de Google API) dejó filas
   colgadas en `EN PROCESO` para siempre — bloqueando esa lane hasta que
   alguien las resolviera a mano en la hoja. Se probó un vencimiento
   (`WORKBOOK_LOCK_STALE_MINUTES`, ver commit `1198b60`) comparando contra la
   columna `FECHA TRAMITE`, pero añadía una variable más que ajustar y seguía
   dependiendo de adivinar un tiempo "razonable".
2. **Lane B elegida sin nada que hacer.** Si A tenía marcas `EN PROCESO`
   (aunque estuvieran resueltas o no) y B estaba vacía, el sistema igual
   saltaba a B y corría los 4 flujos sobre una cola sin DNIs.

**Nuevo criterio (commit `21634f3`):** una lane está disponible si le queda
alguna fila con DNI y **al menos una de las 4 columnas de estado todavía
vacía** (nunca tocada por ningún flujo). El resultado de una fila ya
trabajada — éxito, error, o en proceso — no importa para esta decisión; eso
lo gestiona quien revisa la hoja vía observación/estado, no el selector de
lane. Si ninguna de las 2 lanes tiene pendientes, la corrida termina sin
ejecutar nada — ya no se trata como error ni se envía correo de aviso,
porque simplemente no hay trabajo nuevo.

Esto es más simple y no requiere vencimiento: una fila colgada en
`EN PROCESO` ya no bloquea la lane completa, porque el resto de sus columnas
normalmente siguen vacías (trabajo real pendiente para los otros 3 flujos).
