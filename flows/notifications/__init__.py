"""Envio de alertas por Microsoft Graph (correo a los colaboradores del area)."""
from .mail_config import graph_mail_enabled, load_mail_config, validate_mail_config
from .service import enviar_alerta_fecha_nueva

__all__ = [
    "graph_mail_enabled",
    "load_mail_config",
    "validate_mail_config",
    "enviar_alerta_fecha_nueva",
]
