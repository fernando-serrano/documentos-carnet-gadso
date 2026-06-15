"""Servicio de alto nivel: emite la alerta de nueva fecha CON cupos a los colaboradores."""
from __future__ import annotations

from .builders import construir_asunto, construir_cuerpo_html
from .graph_client import send_mail
from .mail_config import graph_mail_enabled, load_mail_config, validate_mail_config


def enviar_alerta_fecha_nueva(
    sede: str,
    fecha_detectada: str,
    fecha_anterior: str,
    horarios: list[dict],
    hay_cupos: bool,
    fechas_disponibles: list[str],
    detectado_en: str,
    captura_path=None,
    logger=None,
) -> bool:
    """Envia el correo de alerta. Devuelve True si se envio, False si no.

    `horarios` es [{"hora", "cupos"}] solo con los que tienen cupos > 0 (vacio si la
    fecha aparecio sin cupos). `hay_cupos` indica que tipo de aviso es.
    Un fallo de correo NO invalida la deteccion; solo se registra.
    """
    def _log(nivel: str, msg: str):
        if logger:
            getattr(logger, nivel, logger.info)(msg)
        else:
            print(f"[{nivel.upper()}] {msg}")

    if not graph_mail_enabled():
        _log("warning", "Correo deshabilitado (MS_GRAPH_MAIL_ENABLED=0); no se envia alerta.")
        return False

    config = load_mail_config()
    falta = validate_mail_config(config)
    if falta:
        _log("error", f"Config de correo incompleta: {falta}. No se envia alerta.")
        return False

    total_cupos = sum(int(h.get("cupos", 0)) for h in horarios)
    adjuntos = [captura_path] if captura_path else []
    asunto = construir_asunto(config["subject_prefix"], sede, fecha_detectada, total_cupos, hay_cupos)
    cuerpo = construir_cuerpo_html(
        sede=sede,
        fecha_detectada=fecha_detectada,
        fecha_anterior=fecha_anterior,
        horarios=horarios,
        total_cupos=total_cupos,
        hay_cupos=hay_cupos,
        fechas_disponibles=fechas_disponibles,
        detectado_en=detectado_en,
        adjunta_captura=bool(captura_path),
    )

    try:
        send_mail(config, asunto, cuerpo, attachment_paths=adjuntos)
        adj = " (con captura adjunta)" if adjuntos else ""
        _log("info", f"Alerta enviada a: {', '.join(config['to'])}{adj}")
        return True
    except Exception as e:
        _log("error", f"Fallo el envio de la alerta: {e}")
        return False
