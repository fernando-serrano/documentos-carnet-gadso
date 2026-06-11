"""Integración con Google Sheets del flujo Galenius.

Wrapper delgado sobre :mod:`flows.common.sheets`: fija el User-Agent, los
prefijos de variables de entorno y la cadena de credenciales propios de
Galenius. El esquema de columnas (``resolve_sheet_columns``) es específico de
este flujo y se mantiene aquí.
"""

import os

from flows.common import sheets as _common

_USER_AGENT = "Mozilla/5.0 (compatible; galenius-bot/1.0)"


def _read_settings() -> dict:
    return {
        "user_agent": _USER_AGENT,
        "retries": max(1, int(str(os.getenv("GALENIUS_GSHEET_READ_RETRIES", "4") or "4").strip())),
        "timeout_sec": max(8, int(str(os.getenv("GALENIUS_GSHEET_TIMEOUT_SEC", "25") or "25").strip())),
        "retry_base_ms": max(200, int(str(os.getenv("GALENIUS_GSHEET_RETRY_BASE_MS", "600") or "600").strip())),
    }


def read_google_sheet_rows(sheet_url: str) -> tuple[list[dict], list[str]]:
    return _common.read_sheet_rows(sheet_url, **_read_settings())


def update_sheet_row(sheet_url: str, row_number: int, updates: dict[str, str], fieldnames: list[str] | None = None, sheet_title: str | None = None) -> None:
    credentials_path = str(os.getenv("GALENIUS_SHEETS_CREDENTIALS_JSON", os.getenv("DRIVE_CREDENTIALS_JSON", "")) or "").strip()
    if not credentials_path:
        raise RuntimeError("Falta GALENIUS_SHEETS_CREDENTIALS_JSON o DRIVE_CREDENTIALS_JSON en .env")
    _common.update_sheet_row(
        sheet_url,
        row_number,
        updates,
        credentials_path=credentials_path,
        read_sheet_settings=_read_settings(),
        fieldnames=fieldnames,
        sheet_title=sheet_title,
    )


def resolve_sheet_columns(fieldnames: list[str]) -> dict[str, str]:
    esquema = [
        ("dni", ["dni"]),
        ("estado_certificado_medico", ["estado certificado medico", "estado certificado médico"]),
        ("observacion_certificado_medico", ["observacion certificado medico", "observacion certificado médico"]),
        ("estado_dj_fut", ["estado dj fut"]),
        ("observacion_dj_fut", ["observacion dj fut"]),
        ("responsable", ["responsable"]),
        ("fecha_tramite", ["fecha tramite", "fecha trámite"]),
    ]
    return {nombre: _common.resolver_columna(fieldnames, candidatos) for nombre, candidatos in esquema}
