# Selección de lane A/B para evitar colisiones entre 2 dispositivos — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Antes de que `run.bat` arranque cualquiera de los 4 flujos (Galenius, Foto Carné, DJ FUT, Firma Digital), decidir una sola vez si esa corrida usa la cola "lane A" (la actual, `gid=214579984`) o la "lane B" (pestaña nueva, `gid=1779457178`, mismo spreadsheet), para que 2 dispositivos corriendo `run.bat` al mismo tiempo no dupliquen ni pisen el procesamiento de los mismos registros.

**Architecture:** Un módulo puro `flows/common/workbook_lock.py` decide la lane leyendo la cola compartida (CSV público, sin credenciales de escritura) y buscando filas `EN PROCESO` en las 4 columnas de estado. Un script CLI `scripts/select_workbook.py` envuelve esa decisión, imprime `A`/`B` a stdout, y si ambas lanes están ocupadas envía un correo de aviso (Microsoft Graph, canal ya existente) y sale con código 3. `run.bat` captura la salida en `ACTIVE_WORKBOOK` antes de arrancar cualquier flujo. Los 4 `load_*_config()` existentes leen `ACTIVE_WORKBOOK` y, si vale `B`, resuelven la URL de cola con sufijo `_B` — la hoja fuente de cada flujo no cambia.

**Tech Stack:** Python 3.14 (stdlib `os`, `socket`, `sys`, `datetime`), `python-dotenv`, código propio en `flows/common/sheets.py` y `flows/notifications/*` ya existentes. Sin pytest — el proyecto no lo usa; los tests son scripts standalone en `tests/manual/` con `assert` y un `if __name__ == "__main__":`, igual que `tests/manual/test_firma_digital_processing.py`.

**Spec:** `docs/superpowers/specs/2026-08-28-multi-workbook-lane-selection-design.md`

## Global Constraints

- La hoja fuente de cada flujo (`FOTO_CARNE_SOURCE_SHEET_URL`, `DJ_FUT_SOURCE_SHEET_URL`, `FIRMA_DIGITAL_SOURCE_SHEET_URL`) **nunca** cambia con la lane — solo la URL de **cola** (`*_QUEUE_SHEET_URL`) se ve afectada.
- Sin `ACTIVE_WORKBOOK` seteado (o con valor `A`), el comportamiento de cada `load_*_config()` debe ser **idéntico** al actual — cambio puramente aditivo.
- `scripts/select_workbook.py` es de solo lectura sobre las hojas (nunca escribe una celda) — el único efecto secundario permitido es el envío de correo en el caso de aborto.
- Si no se puede confirmar que una lane está libre (error de red, URL vacía), tratarla como ocupada — nunca asumir "libre" por defecto.
- Todas las columnas de estado a revisar: `ESTADO CERTIFICADO MEDICO` (Galenius), `ESTADO FOTO CARNÉ` (Foto Carné), `ESTADO DJ FUT` (DJ FUT), `ESTADO FIRMA` / `ESTADO FIRMA DIGITAL` (Firma Digital) — todas con prefijo `EN PROCESO` por defecto (env vars `*_ESTADO_EN_PROCESO`).

---

## Task 1: `flows/common/workbook_lock.py` — detección de "en proceso" y decisión de lane

**Files:**
- Create: `flows/common/workbook_lock.py`
- Test: `tests/manual/test_workbook_lock.py`

**Interfaces:**
- Consumes: `flows.common.sheets.read_sheet_rows(sheet_url, *, user_agent, retries, timeout_sec, retry_base_ms) -> tuple[list[dict], list[str]]`, `flows.common.sheets.resolver_columna(fieldnames, candidatos) -> str`, `flows.common.sheets.normalizar_columna(texto) -> str` (todas ya existen en `flows/common/sheets.py`).
- Produces: `hay_registros_en_proceso(queue_url: str) -> bool` y `decidir_lane() -> str | None` (`"A"`, `"B"`, o `None` si ambas ocupadas/no disponibles). Estas dos funciones son las que consume Task 2.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/manual/test_workbook_lock.py`:

```python
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import flows.common.sheets as sheets
from flows.common import workbook_lock

_FIELDNAMES = [
    "DNI",
    "ESTADO CERTIFICADO MEDICO",
    "ESTADO FOTO CARNÉ",
    "ESTADO DJ FUT",
    "ESTADO FIRMA",
    "RESPONSABLE",
    "FECHA TRAMITE",
]


def _fila(dni: str, **estados: str) -> dict:
    row = {campo: "" for campo in _FIELDNAMES}
    row["DNI"] = dni
    row.update(estados)
    return row


def _fake_read_sheet_rows(rows_por_url: dict[str, list[dict]]):
    def _fake(sheet_url, **_settings):
        return rows_por_url.get(sheet_url, []), _FIELDNAMES
    return _fake


def _con_lecturas(rows_por_url, fn):
    original = sheets.read_sheet_rows
    sheets.read_sheet_rows = _fake_read_sheet_rows(rows_por_url)
    try:
        fn()
    finally:
        sheets.read_sheet_rows = original


def test_hay_registros_en_proceso_false_cuando_todo_pendiente():
    rows = {"https://sheet-a": [_fila("11111111"), _fila("22222222")]}

    def _run():
        assert workbook_lock.hay_registros_en_proceso("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_registros_en_proceso_true_si_alguna_columna_esta_en_proceso():
    rows = {
        "https://sheet-a": [
            _fila("11111111"),
            _fila("22222222", **{"ESTADO DJ FUT": "EN PROCESO W2"}),
        ]
    }

    def _run():
        assert workbook_lock.hay_registros_en_proceso("https://sheet-a") is True

    _con_lecturas(rows, _run)


def test_hay_registros_en_proceso_true_si_url_vacia():
    assert workbook_lock.hay_registros_en_proceso("") is True


def test_decidir_lane_usa_a_si_esta_libre():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111")],
        "https://sheet-b": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "A"

    _con_lecturas(rows, _run)


def test_decidir_lane_usa_b_si_a_ocupada_y_b_libre():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
        "https://sheet-b": [_fila("11111111")],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "B"

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_ambas_ocupadas():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
        "https://sheet-b": [_fila("22222222", **{"ESTADO DJ FUT": "EN PROCESO W1"})],
    }

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_a_ocupada_y_b_no_configurada():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ.pop("GALENIUS_QUEUE_SHEET_URL_B", None)
    rows = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})]}

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


if __name__ == "__main__":
    test_hay_registros_en_proceso_false_cuando_todo_pendiente()
    test_hay_registros_en_proceso_true_si_alguna_columna_esta_en_proceso()
    test_hay_registros_en_proceso_true_si_url_vacia()
    test_decidir_lane_usa_a_si_esta_libre()
    test_decidir_lane_usa_b_si_a_ocupada_y_b_libre()
    test_decidir_lane_none_si_ambas_ocupadas()
    test_decidir_lane_none_si_a_ocupada_y_b_no_configurada()
    print("OK - test_workbook_lock")
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `.venv\Scripts\python.exe tests\manual\test_workbook_lock.py`
Expected: `ModuleNotFoundError: No module named 'flows.common.workbook_lock'` (el módulo aún no existe).

- [ ] **Step 3: Implementar `flows/common/workbook_lock.py`**

```python
"""Decide qué lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Las 4 columnas de estado que escriben los flujos viven en la MISMA fila de la
cola compartida (una pestaña de Google Sheets por lane). Este módulo solo lee
esas columnas — nunca escribe una celda.
"""
from __future__ import annotations

import os

from . import sheets as _sheets

_READ_SETTINGS = {
    "user_agent": "Mozilla/5.0 (compatible; workbook-lock/1.0)",
    "retries": 4,
    "timeout_sec": 25,
    "retry_base_ms": 600,
}

# (candidatos de nombre de columna, env var con el prefijo "en proceso" de ese flujo)
_ESTADO_COLUMNAS: list[tuple[list[str], str]] = [
    (["ESTADO CERTIFICADO MEDICO", "ESTADO CERTIFICADO MÉDICO"], "GALENIUS_ESTADO_EN_PROCESO"),
    (["ESTADO FOTO CARNÉ", "ESTADO FOTO CARNE"], "FOTO_CARNE_ESTADO_EN_PROCESO"),
    (["ESTADO DJ FUT"], "DJ_FUT_ESTADO_EN_PROCESO"),
    (["ESTADO FIRMA", "ESTADO FIRMA DIGITAL"], "FIRMA_DIGITAL_ESTADO_EN_PROCESO"),
]


def hay_registros_en_proceso(queue_url: str) -> bool:
    """True si la cola tiene alguna fila EN PROCESO, o si no se pudo confirmar que está libre."""
    url = str(queue_url or "").strip()
    if not url:
        return True

    try:
        rows, fieldnames = _sheets.read_sheet_rows(url, **_READ_SETTINGS)
    except Exception:
        return True

    for candidatos, env_var in _ESTADO_COLUMNAS:
        columna = _sheets.resolver_columna(fieldnames, candidatos)
        if not columna:
            continue
        prefijo = _sheets.normalizar_columna(os.getenv(env_var, "EN PROCESO"))
        if not prefijo:
            continue
        for row in rows:
            valor = _sheets.normalizar_columna(str(row.get(columna, "") or ""))
            if valor and valor.startswith(prefijo):
                return True

    return False


def decidir_lane() -> str | None:
    """Devuelve 'A', 'B', o None si ambas lanes están ocupadas/no disponibles."""
    url_a = str(os.getenv("GALENIUS_QUEUE_SHEET_URL", "")).strip()
    url_b = str(os.getenv("GALENIUS_QUEUE_SHEET_URL_B", "")).strip()

    if not hay_registros_en_proceso(url_a):
        return "A"
    if not hay_registros_en_proceso(url_b):
        return "B"
    return None
```

- [ ] **Step 4: Ejecutar el test y verificar que pasa**

Run: `.venv\Scripts\python.exe tests\manual\test_workbook_lock.py`
Expected: imprime `OK - test_workbook_lock` sin trazas de `AssertionError`.

- [ ] **Step 5: Commit**

```bash
git add flows/common/workbook_lock.py tests/manual/test_workbook_lock.py
git commit -m "feat: agrega deteccion de lane A/B ocupada sobre la cola compartida"
```

---

## Task 2: `scripts/select_workbook.py` — CLI + aviso por correo si ambas lanes ocupadas

**Files:**
- Create: `scripts/select_workbook.py`
- Test: manual (Step 3 más abajo) — no hay test automatizado nuevo porque la lógica de decisión ya la cubre `test_workbook_lock.py`; este script es un wrapper delgado de I/O (stdout/stdin de entorno) que se verifica ejecutándolo.

**Interfaces:**
- Consumes: `flows.common.workbook_lock.decidir_lane() -> str | None` (Task 1); `flows.notifications.mail_config.graph_mail_enabled() -> bool`, `load_mail_config() -> dict`, `validate_mail_config(config: dict) -> str | None`; `flows.notifications.graph_client.send_mail(config, subject, html_body, attachment_paths=None, inline_images=None) -> None` (todas ya existen).
- Produces: `main() -> int` (0 = imprimió `A`/`B` a stdout; 3 = ambas lanes ocupadas/no disponibles, ya avisado por correo si estaba configurado). Este exit code y el stdout son lo que consume Task 3 (`run.bat`).

- [ ] **Step 1: Escribir `scripts/select_workbook.py`**

```python
"""CLI: decide que lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Imprime 'A' o 'B' a stdout y sale con codigo 0. Si ambas lanes estan
ocupadas (o la lane B no esta configurada), no imprime nada util a stdout,
intenta avisar por correo (Microsoft Graph, mismo canal que enviar_lote.py)
y sale con codigo 3. No escribe en ninguna hoja.

Uso (desde run.bat):
    for /f "delims=" %%W in ('".venv\\Scripts\\python.exe" scripts\\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
"""
from __future__ import annotations

import os
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from flows.common.workbook_lock import decidir_lane
from flows.notifications.graph_client import send_mail
from flows.notifications.mail_config import graph_mail_enabled, load_mail_config, validate_mail_config


def _avisar_ambas_lanes_ocupadas() -> None:
    if not graph_mail_enabled():
        return
    config = load_mail_config()
    if validate_mail_config(config):
        return

    dispositivo = str(os.getenv("COMPUTERNAME", "") or socket.gethostname() or "desconocido").strip()
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    subject = f"{config['subject_prefix']} - Ambas lanes de cola ocupadas ({dispositivo})"
    body = (
        f"<p>El dispositivo <b>{dispositivo}</b> intento iniciar run.bat el <b>{ahora}</b> "
        "pero tanto la lane A como la lane B de la cola BOT DOCUMENTOS ya tienen registros "
        "EN PROCESO.</p>"
        "<p>La corrida se aborto sin tocar ninguna hoja. Reintentar mas tarde o revisar "
        "manualmente el estado de ambas colas.</p>"
    )
    try:
        send_mail(config, subject, body)
    except Exception as exc:
        print(f"[SELECT_WORKBOOK] No se pudo enviar el correo de aviso: {exc}", file=sys.stderr)


def main() -> int:
    load_dotenv()
    lane = decidir_lane()
    if lane is None:
        _avisar_ambas_lanes_ocupadas()
        print("[SELECT_WORKBOOK] Ambas lanes ocupadas o lane B no configurada.", file=sys.stderr)
        return 3

    print(lane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verificar sintaxis e imports**

Run: `.venv\Scripts\python.exe -c "import ast; ast.parse(open('scripts/select_workbook.py', encoding='utf-8').read())"`
Expected: sin salida (sin `SyntaxError`).

- [ ] **Step 3: Prueba manual con lane A libre real**

Con el `.env` actual (sin `GALENIUS_QUEUE_SHEET_URL_B` todavía, se agrega en Task 5) y la cola de producción sin registros `EN PROCESO`:

Run: `.venv\Scripts\python.exe scripts\select_workbook.py; echo "exit=$?"`
Expected: imprime `A` y `exit=0`.

- [ ] **Step 4: Commit**

```bash
git add scripts/select_workbook.py
git commit -m "feat: agrega CLI select_workbook para elegir lane antes de run.bat"
```

---

## Task 3: Wiring en `run.bat`

**Files:**
- Modify: `run.bat:20-31` (bloque `:prepare_shared_lote`)

**Interfaces:**
- Consumes: `scripts/select_workbook.py` vía stdout (`A`/`B`) y exit code (Task 2).
- Produces: variable de entorno `ACTIVE_WORKBOOK` disponible para todo el proceso `run.bat` y, por herencia, para cada `scripts\bat\run_*.bat` / `run_*.py` invocado después (Task 4 la consume).

- [ ] **Step 1: Editar `:prepare_shared_lote` en `run.bat`**

Reemplazar:

```bat
:prepare_shared_lote
if defined GLOBAL_LOTE_DIR exit /b 0
set "RUN_TS="
```

por:

```bat
:prepare_shared_lote
if defined GLOBAL_LOTE_DIR exit /b 0

set "ACTIVE_WORKBOOK="
for /f "delims=" %%W in ('".venv\Scripts\python.exe" scripts\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
if not defined ACTIVE_WORKBOOK (
  echo [ERROR] No se pudo determinar la cola activa: ambas lanes ^(A/B^) estan ocupadas o hubo un error.
  exit /b 3
)
echo [RUN] Lane activa: %ACTIVE_WORKBOOK%

set "RUN_TS="
```

- [ ] **Step 2: Prueba manual — lane A libre**

Run: `.\run.bat foto_carne`
Expected: la primera línea de salida incluye `[RUN] Lane activa: A`, y el flujo continúa normalmente (sin `ACTIVE_WORKBOOK` seteado en `.env`, todos los `load_*_config()` de Task 4 siguen leyendo las URLs de siempre).

- [ ] **Step 3: Prueba manual — simular ambas lanes ocupadas**

Marcar manualmente en la hoja de producción (lane A) una fila con `ESTADO FOTO CARNÉ = EN PROCESO` y, sin `GALENIUS_QUEUE_SHEET_URL_B` configurada aún (Task 5 pendiente), correr:

Run: `.\run.bat foto_carne`
Expected: imprime `[ERROR] No se pudo determinar la cola activa...`, `run.bat` termina con código `3`, y ningún flujo llega a ejecutarse. Deshacer el marcado manual en la hoja al terminar la prueba.

- [ ] **Step 4: Commit**

```bash
git add run.bat
git commit -m "feat: run.bat selecciona lane A/B antes de arrancar cualquier flujo"
```

---

## Task 4: Los 4 `load_*_config()` respetan `ACTIVE_WORKBOOK`

**Files:**
- Modify: `flows/galenius_flow/config.py:63-68`
- Modify: `run_foto_carne.py:55`
- Modify: `run_dj_fut.py:53`
- Modify: `run_firma_digital.py:78`
- Test: `tests/manual/test_active_workbook_queue_urls.py`

**Interfaces:**
- Consumes: variable de entorno `ACTIVE_WORKBOOK` (`"A"` por defecto, o `"B"`) — la misma que setea Task 3 en `run.bat`.
- Produces: `GaleniusConfig.queue_sheet_url`, `FotoCarneConfig.queue_sheet_url`, `DjFutConfig.queue_sheet_url`, `FirmaDigitalConfig.queue_sheet_url` — sin cambios de tipo (siguen siendo `str`), solo cambia qué env var resuelven.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/manual/test_active_workbook_queue_urls.py`:

```python
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
for var in (
    "FOTO_CARNE_QUEUE_SHEET_URL", "FOTO_CARNE_QUEUE_SHEET_URL_B",
    "DJ_FUT_QUEUE_SHEET_URL", "DJ_FUT_QUEUE_SHEET_URL_B",
    "FIRMA_DIGITAL_QUEUE_SHEET_URL", "FIRMA_DIGITAL_QUEUE_SHEET_URL_B",
):
    os.environ.pop(var, None)

import run_dj_fut
import run_firma_digital
import run_foto_carne
from flows.galenius_flow.config import load_galenius_config


def test_lane_a_por_defecto():
    os.environ.pop("ACTIVE_WORKBOOK", None)
    assert load_galenius_config().queue_sheet_url == "https://sheet-a"
    assert run_foto_carne.load_foto_carne_config().queue_sheet_url == "https://sheet-a"
    assert run_dj_fut.load_dj_fut_config().queue_sheet_url == "https://sheet-a"
    assert run_firma_digital.load_firma_digital_config().queue_sheet_url == "https://sheet-a"


def test_lane_b_cuando_active_workbook_es_b():
    os.environ["ACTIVE_WORKBOOK"] = "B"
    try:
        assert load_galenius_config().queue_sheet_url == "https://sheet-b"
        assert run_foto_carne.load_foto_carne_config().queue_sheet_url == "https://sheet-b"
        assert run_dj_fut.load_dj_fut_config().queue_sheet_url == "https://sheet-b"
        assert run_firma_digital.load_firma_digital_config().queue_sheet_url == "https://sheet-b"
    finally:
        os.environ.pop("ACTIVE_WORKBOOK", None)


def test_source_sheet_no_cambia_con_la_lane():
    os.environ["FOTO_CARNE_SOURCE_SHEET_URL"] = "https://source-foto"
    os.environ["ACTIVE_WORKBOOK"] = "B"
    try:
        assert run_foto_carne.load_foto_carne_config().source_sheet_url == "https://source-foto"
    finally:
        os.environ.pop("ACTIVE_WORKBOOK", None)
        os.environ.pop("FOTO_CARNE_SOURCE_SHEET_URL", None)


if __name__ == "__main__":
    test_lane_a_por_defecto()
    test_lane_b_cuando_active_workbook_es_b()
    test_source_sheet_no_cambia_con_la_lane()
    print("OK - test_active_workbook_queue_urls")
```

- [ ] **Step 2: Ejecutar el test y verificar que falla**

Run: `.venv\Scripts\python.exe tests\manual\test_active_workbook_queue_urls.py`
Expected: `AssertionError` en `test_lane_b_cuando_active_workbook_es_b` (hoy `queue_sheet_url` sigue siendo `"https://sheet-a"` aunque `ACTIVE_WORKBOOK=B`, porque ningún loader lo lee todavía).

- [ ] **Step 3: Modificar `flows/galenius_flow/config.py`**

Reemplazar (líneas 63-68):

```python
    queue_sheet_url = str(
        os.getenv(
            "GALENIUS_QUEUE_SHEET_URL",
            "https://docs.google.com/spreadsheets/d/1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U/edit?gid=214579984#gid=214579984",
        )
    ).strip()
```

por:

```python
    _active_workbook = os.getenv("ACTIVE_WORKBOOK", "A").strip().upper()
    _default_queue_url_a = "https://docs.google.com/spreadsheets/d/1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U/edit?gid=214579984#gid=214579984"
    _queue_env_name = "GALENIUS_QUEUE_SHEET_URL_B" if _active_workbook == "B" else "GALENIUS_QUEUE_SHEET_URL"
    _queue_default = "" if _active_workbook == "B" else _default_queue_url_a
    queue_sheet_url = str(os.getenv(_queue_env_name, _queue_default)).strip()
```

- [ ] **Step 4: Modificar `run_foto_carne.py`**

Reemplazar (línea 55):

```python
    queue_sheet_url = str(os.getenv("FOTO_CARNE_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
```

por:

```python
    _active_workbook = os.getenv("ACTIVE_WORKBOOK", "A").strip().upper()
    _suffix = "_B" if _active_workbook == "B" else ""
    queue_sheet_url = str(
        os.getenv(f"FOTO_CARNE_QUEUE_SHEET_URL{_suffix}", os.getenv(f"GALENIUS_QUEUE_SHEET_URL{_suffix}", ""))
    ).strip()
```

- [ ] **Step 5: Modificar `run_dj_fut.py`**

Reemplazar (línea 53):

```python
    queue_sheet_url = str(os.getenv("DJ_FUT_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
```

por:

```python
    _active_workbook = os.getenv("ACTIVE_WORKBOOK", "A").strip().upper()
    _suffix = "_B" if _active_workbook == "B" else ""
    queue_sheet_url = str(
        os.getenv(f"DJ_FUT_QUEUE_SHEET_URL{_suffix}", os.getenv(f"GALENIUS_QUEUE_SHEET_URL{_suffix}", ""))
    ).strip()
```

- [ ] **Step 6: Modificar `run_firma_digital.py`**

Reemplazar (línea 78):

```python
    queue_sheet_url = str(os.getenv("FIRMA_DIGITAL_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
```

por:

```python
    _active_workbook = os.getenv("ACTIVE_WORKBOOK", "A").strip().upper()
    _suffix = "_B" if _active_workbook == "B" else ""
    queue_sheet_url = str(
        os.getenv(f"FIRMA_DIGITAL_QUEUE_SHEET_URL{_suffix}", os.getenv(f"GALENIUS_QUEUE_SHEET_URL{_suffix}", ""))
    ).strip()
```

- [ ] **Step 7: Ejecutar el test y verificar que pasa**

Run: `.venv\Scripts\python.exe tests\manual\test_active_workbook_queue_urls.py`
Expected: imprime `OK - test_active_workbook_queue_urls`.

- [ ] **Step 8: Commit**

```bash
git add flows/galenius_flow/config.py run_foto_carne.py run_dj_fut.py run_firma_digital.py tests/manual/test_active_workbook_queue_urls.py
git commit -m "feat: los 4 flujos resuelven su cola segun ACTIVE_WORKBOOK (lane A/B)"
```

---

## Task 5: `.env` con la lane B real + documentación

**Files:**
- Modify: `.env`
- Modify: `README.md:85-113` (sección "7. Configurar el archivo .env" y "8. Ejecutar el flujo")
- Modify: `README.md:172-186` (sección "Variables Minimas" de Galenius)

**Interfaces:**
- Consumes: nada de tareas anteriores (es config/documentación).
- Produces: `GALENIUS_QUEUE_SHEET_URL_B` en `.env`, consumido en runtime por `flows/common/workbook_lock.decidir_lane()` (Task 1) y por los 4 `load_*_config()` (Task 4).

- [ ] **Step 1: Agregar la variable en `.env`**

Ubicar la línea `CARNET_GSHEET_URL=` (o la sección de `GALENIUS_QUEUE_SHEET_URL` si existe explícita) y agregar, junto a las variables de Galenius:

```
GALENIUS_QUEUE_SHEET_URL_B=https://docs.google.com/spreadsheets/d/1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U/edit?gid=1779457178#gid=1779457178
```

- [ ] **Step 2: Documentar en `README.md`**

En la sección "### 7. Configurar el archivo `.env`" (después de la línea 93, la de "Como minimo, completar..."), agregar un párrafo:

```markdown
Si vas a correr `run.bat` en 2 dispositivos a la vez, configura además `GALENIUS_QUEUE_SHEET_URL_B` con la pestaña alterna de la cola BOT DOCUMENTOS (mismo spreadsheet, otro `gid`). Antes de arrancar cualquier flujo, `run.bat` revisa si la cola principal ya tiene registros `EN PROCESO`: si los tiene, usa automáticamente la cola `_B`; si ambas están ocupadas, aborta la corrida y avisa por correo (si `MS_GRAPH_MAIL_ENABLED=1`).
```

En la sección "## Variables Minimas" (línea ~180-186, bajo "Opcionales recomendadas"), agregar:

```markdown
- `GALENIUS_QUEUE_SHEET_URL_B` (pestaña alterna para evitar colisiones entre 2 dispositivos corriendo `run.bat` a la vez)
```

- [ ] **Step 3: Verificar manualmente el caso normal**

Run: `.venv\Scripts\python.exe scripts\select_workbook.py; echo "exit=$?"`
Expected: sigue imprimiendo `A` (porque la lane A de producción sigue libre) y `exit=0` — confirma que agregar `GALENIUS_QUEUE_SHEET_URL_B` no rompe el caso normal.

- [ ] **Step 4: Verificar manualmente la lane B forzada de punta a punta**

Forzar `ACTIVE_WORKBOOK=B` y correr un flujo real para confirmar que lee/escribe en la pestaña `gid=1779457178`:

Run: `set "ACTIVE_WORKBOOK=B" && .venv\Scripts\python.exe run_foto_carne.py`
Expected: el log imprime `[FOTO CARNE] Cola source=...` con la URL de `GALENIUS_QUEUE_SHEET_URL_B` (gid=1779457178), y las filas que procesa/marca son las de esa pestaña, no las de la lane A. Revisar en Google Sheets que la pestaña `gid=1779457178` recibió las marcas `EN PROCESO`/`DESCARGADO`.

- [ ] **Step 5: Commit (solo el README — `.env` está en `.gitignore` y nunca se commitea)**

```bash
git add README.md
git commit -m "docs: agrega GALENIUS_QUEUE_SHEET_URL_B (lane B) y su explicacion en el README"
```
