# Selección de workbook (lane) para evitar colisiones entre 2 dispositivos

Fecha: 2026-08-28
Autor: Fernando Serrano (con Claude Code)
Estado: Aprobado en chat, pendiente de escritura de plan de implementación

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

Para mitigar esto se habilitó un segundo workbook completo (mismo esquema de
pestañas y campos que el original) en Google Sheets, pensado como una
"segunda cola" a la que un dispositivo puede saltar si detecta que el
workbook principal ya está siendo trabajado.

## 2. Objetivo

Antes de que un `run.bat` arranque cualquiera de los 4 flujos, decidir **una
sola vez, para toda la corrida**, cuál de los 2 workbooks usar:

- Si el workbook A (el actual/production) no tiene ninguna fila marcada
  `EN PROCESO` en ninguna de sus 4 columnas de estado → usar A (comportamiento
  actual, sin cambios).
- Si A tiene registros en proceso → revisar el workbook B. Si B está libre →
  usar B.
- Si **ambos** están ocupados → abortar la corrida completa sin tocar ninguna
  hoja, y enviar un correo de aviso por el mismo canal que ya usa el proyecto
  para notificaciones (Microsoft Graph).

## 3. No objetivos

- No se reemplaza el marcado de estado por fila existente (`EN PROCESO` →
  `DESCARGADO`/`ERROR`/etc.) — sigue igual dentro de cada flujo.
- No se implementa reclamo atómico por fila (optimistic lock estilo
  `_intentar_reservar_registro_compare` de `ejemplos/carnet_emision.py`). Es
  una mejora conocida para un futuro; queda fuera de este alcance porque el
  usuario pidió específicamente el mecanismo de selección de workbook.
- No se decide el workbook por flujo individual — la decisión es una sola
  para los 4 flujos de esa corrida (confirmado con el usuario).
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
2. Para el workbook A: leer la cola (`GALENIUS_QUEUE_SHEET_URL`) con
   `flows.common.sheets.read_sheet_rows` (lectura pública vía CSV export, sin
   credenciales de escritura) y revisar las 4 columnas de estado listadas en
   §1. Se considera "en proceso" cualquier valor cuya versión normalizada
   (mismo `normalizar_columna` ya usado en el proyecto) **empiece con** el
   valor normalizado de la env var `*_ESTADO_EN_PROCESO` correspondiente
   (para tolerar sufijos como `EN PROCESO W1`).
3. Si A no tiene ninguna fila en proceso → imprimir `A` a stdout, exit 0.
4. Si A tiene alguna → repetir el mismo chequeo sobre el workbook B, usando
   las variables de entorno `*_B` (ver §4.2). Si B falta configurar
   (`GALENIUS_QUEUE_SHEET_URL_B` vacío) se trata como "no disponible" →
   pasa directo al caso de aborto (§4.3), registrando el motivo.
5. Si B está libre → imprimir `B` a stdout, exit 0.
6. Si B también tiene registros en proceso (o no está configurado) → aborto
   (§4.3): imprimir nada útil a stdout, exit code `3`.

Este script es puramente de lectura salvo el envío de correo en el caso de
aborto — no escribe en ninguna hoja, así que no hay riesgo de dejar un
estado a medio escribir si falla a mitad de camino.

### 4.2 Variables `.env` nuevas (workbook B)

Mismos nombres que las URLs existentes, con sufijo `_B`:

- `GALENIUS_QUEUE_SHEET_URL_B` (cola compartida del workbook B; los otros 3
  flujos caen a esta por defecto, igual que hoy con la A).
- `FOTO_CARNE_SOURCE_SHEET_URL_B`
- `DJ_FUT_SOURCE_SHEET_URL_B`
- `FIRMA_DIGITAL_SOURCE_SHEET_URL_B`

Opcional (si en el futuro algún flujo deja de compartir cola con Galenius):
`FOTO_CARNE_QUEUE_SHEET_URL_B`, `DJ_FUT_QUEUE_SHEET_URL_B`,
`FIRMA_DIGITAL_QUEUE_SHEET_URL_B` — con el mismo fallback a
`GALENIUS_QUEUE_SHEET_URL_B` que ya usan sus contrapartes A.

El usuario debe rellenar estas URLs con las pestañas reales del workbook B
provisto.

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
DJ FUT/Firma Digital) ya arma sus URLs con el patrón:

```python
queue_sheet_url = str(os.getenv("FOTO_CARNE_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
source_sheet_url = str(os.getenv("FOTO_CARNE_SOURCE_SHEET_URL", "")).strip()
```

Se agrega, antes de resolver cada URL, un sufijo condicional:

```python
suffix = "_B" if os.getenv("ACTIVE_WORKBOOK", "A").strip().upper() == "B" else ""
queue_sheet_url = str(os.getenv(f"FOTO_CARNE_QUEUE_SHEET_URL{suffix}", os.getenv(f"GALENIUS_QUEUE_SHEET_URL{suffix}", ""))).strip()
source_sheet_url = str(os.getenv(f"FOTO_CARNE_SOURCE_SHEET_URL{suffix}", "")).strip()
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
