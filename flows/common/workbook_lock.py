"""Decide qué lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Las 4 columnas de estado que escriben los flujos viven en la MISMA fila de la
cola compartida (una pestaña de Google Sheets por lane). Este módulo solo lee
esas columnas — nunca escribe una celda.

Regla binaria, sin punto medio: si la lane A tiene CUALQUIER fila marcada
EN PROCESO, se usa B — sin importar si A todavia tiene trabajo pendiente real
o si B tiene o no DNIs cargados. Si A no esta en proceso, se usa A.
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


def _leer_cola_actual(queue_url: str) -> tuple[list[dict], list[str]] | None:
    """Lee la cola preferentemente via Sheets API (fresca, sin cache), cayendo al
    CSV publico si no hay credenciales configuradas o la API falla por cualquier
    motivo. Devuelve None solo si ninguna de las dos vias pudo leer la hoja."""
    credentials_path = str(os.getenv("DRIVE_CREDENTIALS_JSON", "") or "").strip()
    if credentials_path:
        try:
            return _sheets.read_sheet_values_api(queue_url, credentials_path)
        except Exception:
            pass  # cae a la lectura publica (CSV) mas abajo

    try:
        return _sheets.read_sheet_rows(queue_url, **_READ_SETTINGS)
    except Exception:
        return None


def hay_en_proceso(queue_url: str) -> bool:
    """True si la cola tiene alguna fila EN PROCESO en cualquiera de las 4 columnas.

    Si la URL esta vacia o no se pudo leer la hoja, se responde False (no
    romper el flujo normal: por defecto se sigue usando la lane A)."""
    url = str(queue_url or "").strip()
    if not url:
        return False

    resultado = _leer_cola_actual(url)
    if resultado is None:
        return False
    rows, fieldnames = resultado

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
    """Devuelve 'A' si no esta en proceso; 'B' si A esta en proceso y B esta
    configurada; None si A esta en proceso y no hay B configurada."""
    url_a = str(os.getenv("GALENIUS_QUEUE_SHEET_URL", "")).strip()
    url_b = str(os.getenv("GALENIUS_QUEUE_SHEET_URL_B", "")).strip()

    if not hay_en_proceso(url_a):
        return "A"
    return "B" if url_b else None
