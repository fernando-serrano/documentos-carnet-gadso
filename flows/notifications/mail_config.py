"""Carga y validacion de la configuracion de correo (Microsoft Graph)."""
from __future__ import annotations

import os


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or ("1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "si", "sí"}


def split_addresses(value: str) -> list[str]:
    items = []
    for part in str(value or "").replace(";", ",").split(","):
        address = part.strip()
        if address:
            items.append(address)
    return items


def graph_mail_enabled() -> bool:
    return _env_bool("MS_GRAPH_MAIL_ENABLED", default=False)


def load_mail_config() -> dict:
    return {
        "tenant_id": str(os.getenv("MS_GRAPH_TENANT_ID", "") or "").strip(),
        "client_id": str(os.getenv("MS_GRAPH_CLIENT_ID", "") or "").strip(),
        "client_secret": str(os.getenv("MS_GRAPH_CLIENT_SECRET", "") or "").strip(),
        "sender": str(os.getenv("MS_GRAPH_SENDER", "") or "").strip(),
        "to": split_addresses(os.getenv("MS_GRAPH_TO", "")),
        "cc": split_addresses(os.getenv("MS_GRAPH_CC", "")),
        "subject_prefix": str(os.getenv("MS_GRAPH_SUBJECT_PREFIX", "BOT TIMER-SUCAMEC") or "BOT TIMER-SUCAMEC").strip(),
    }


def validate_mail_config(config: dict) -> str | None:
    if not config["tenant_id"]:
        return "falta MS_GRAPH_TENANT_ID"
    if not config["client_id"]:
        return "falta MS_GRAPH_CLIENT_ID"
    if not config["client_secret"]:
        return "falta MS_GRAPH_CLIENT_SECRET"
    if not config["sender"]:
        return "falta MS_GRAPH_SENDER"
    if not config["to"]:
        return "falta MS_GRAPH_TO"
    return None
