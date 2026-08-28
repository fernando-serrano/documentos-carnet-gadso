"""Decide qué lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Las 4 columnas de estado que escriben los flujos viven en la MISMA fila de la
cola compartida (una pestaña de Google Sheets por lane). Este módulo solo lee
esas columnas — nunca escribe una celda.

Una lane esta disponible si le quedan filas con DNI y con al menos una de las
4 columnas de estado todavia vacia (nunca tocada por ningun flujo). El
resultado de una fila ya trabajada (exito, error, o en proceso) no importa
para esta decision — eso lo gestiona quien revisa la hoja.
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

_ESTADO_COLUMNAS_CANDIDATOS: list[list[str]] = [
    ["ESTADO CERTIFICADO MEDICO", "ESTADO CERTIFICADO MÉDICO"],
    ["ESTADO FOTO CARNÉ", "ESTADO FOTO CARNE"],
    ["ESTADO DJ FUT"],
    ["ESTADO FIRMA", "ESTADO FIRMA DIGITAL"],
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


def hay_pendientes(queue_url: str) -> bool:
    """True si la cola tiene alguna fila con DNI y al menos una columna de estado vacia."""
    url = str(queue_url or "").strip()
    if not url:
        return False

    resultado = _leer_cola_actual(url)
    if resultado is None:
        return False
    rows, fieldnames = resultado

    dni_col = _sheets.resolver_columna(fieldnames, ["DNI"])
    if not dni_col:
        return False

    columnas_estado = [
        columna
        for candidatos in _ESTADO_COLUMNAS_CANDIDATOS
        if (columna := _sheets.resolver_columna(fieldnames, candidatos))
    ]
    if not columnas_estado:
        return False

    for row in rows:
        dni = str(row.get(dni_col, "") or "").strip()
        if not dni:
            continue
        if any(not str(row.get(columna, "") or "").strip() for columna in columnas_estado):
            return True

    return False


def decidir_lane() -> str | None:
    """Devuelve 'A' o 'B' segun cual tenga filas pendientes; None si ninguna tiene."""
    url_a = str(os.getenv("GALENIUS_QUEUE_SHEET_URL", "")).strip()
    url_b = str(os.getenv("GALENIUS_QUEUE_SHEET_URL_B", "")).strip()

    if hay_pendientes(url_a):
        return "A"
    if hay_pendientes(url_b):
        return "B"
    return None
