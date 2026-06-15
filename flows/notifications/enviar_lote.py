"""Comprime un lote y lo envia por correo (Microsoft Graph) como adjunto ZIP.

Se invoca al terminar una corrida (ver run.bat). Disenado para NO romper la corrida:
cualquier fallo de correo se loguea y se devuelve False; el proceso sale 0 siempre.

Uso CLI:
    python -m flows.notifications.enviar_lote "<lote_dir>" "<etiqueta>"
Si no se pasa <lote_dir>, usa la variable de entorno GLOBAL_LOTE_DIR.
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

from .graph_client import send_mail, send_mail_large
from .mail_config import (
    graph_mail_enabled,
    load_mail_config,
    split_addresses,
    validate_mail_config,
)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except Exception:
        return default


def _log(logger, level: str, msg: str, *args) -> None:
    if logger is not None and hasattr(logger, level):
        getattr(logger, level)(msg, *args)
    else:
        print("[ENVIAR LOTE] " + (msg % args if args else msg))


def _resumen_lote(lote_dir: Path) -> tuple[int, int]:
    """Devuelve (num_dni, num_archivos) del lote."""
    archivos = [p for p in lote_dir.rglob("*") if p.is_file()]
    dnis = {p.parent.name for p in archivos if p.parent != lote_dir}
    return len(dnis), len(archivos)


def _comprimir_lote(lote_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for archivo in sorted(lote_dir.rglob("*")):
            if archivo.is_file():
                zf.write(archivo, arcname=archivo.relative_to(lote_dir))


# Tipos de documento del expediente (etiqueta visible -> prefijo del archivo tratado).
_TIPOS_DOC = [
    ("Foto carne", "foto_carne_"),
    ("Certificado medico", "certificado_medico_"),
    ("DJ FUT", "djfut_"),
    ("Firma digital", "firma_digital_"),
]


def _contar_por_tipo(lote_dir: Path) -> list[tuple[str, int]]:
    """Cuenta los documentos tratados por tipo (excluye los *_original)."""
    nombres = [p.name for p in lote_dir.rglob("*") if p.is_file()]
    out = []
    for etiqueta, prefijo in _TIPOS_DOC:
        n = sum(1 for nm in nombres if nm.startswith(prefijo) and "_original" not in nm)
        out.append((etiqueta, n))
    return out


def _fecha_legible(lote_dir: Path) -> str:
    """Extrae la fecha del nombre del lote (…-DD-MM-YYYY-HH-MM-SS); si no, usa la mtime."""
    import re as _re

    m = _re.search(r"(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})-(\d{2})$", lote_dir.name)
    if m:
        d, mo, y, h, mi, _s = m.groups()
        return f"{d}/{mo}/{y} {h}:{mi}"
    try:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(lote_dir.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return "-"


def _cuerpo_html(
    etiqueta: str,
    lote_nombre: str,
    num_dni: int,
    num_archivos: int,
    tam_mb: float,
    fecha: str,
    breakdown: list[tuple[str, int]],
    adjuntado: bool,
    motivo: str = "",
    logo_cid: str | None = None,
) -> str:
    if adjuntado:
        banner_bg, banner_fg, banner = "#e6f4ea", "#1e7e34", f"&#10003;&nbsp; Adjunto incluido ({tam_mb:.2f} MB)"
        estado_txt = f"Incluido ({tam_mb:.2f} MB)"
    else:
        banner_bg, banner_fg, banner = "#fff4e5", "#9a6700", f"&#9888;&nbsp; Sin adjunto &middot; {motivo}"
        estado_txt = "No incluido"

    def fila(label, value, last=False):
        bb = "" if last else "border-bottom:1px solid #eef0f3;"
        return (
            f'<tr><td style="padding:8px 0;color:#5b6675;font-size:13px;{bb}">{label}</td>'
            f'<td style="padding:8px 0;color:#2b2f36;font-size:13px;text-align:right;font-weight:600;{bb}">{value}</td></tr>'
        )

    resumen = (
        fila("Flujo", etiqueta)
        + fila("Lote", lote_nombre)
        + fila("Fecha / hora", fecha)
        + fila("DNIs", num_dni)
        + fila("Archivos", num_archivos)
        + fila("Tamano ZIP", f"{tam_mb:.2f} MB")
        + fila("Adjunto", estado_txt, last=True)
    )

    desglose = "".join(
        fila(nombre, cant, last=(i == len(breakdown) - 1)) for i, (nombre, cant) in enumerate(breakdown)
    )

    if logo_cid:
        logo_cell = (
            f'<td style="padding-right:12px;vertical-align:middle;">'
            f'<img src="cid:{logo_cid}" alt="SUCAMEC" height="54" '
            f'style="display:block;height:54px;width:auto;border:0;"></td>'
        )
    else:
        logo_cell = ""

    return f"""\
<div style="background:#f4f5f7;padding:24px 0;font-family:'Segoe UI',Arial,sans-serif;">
 <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:#ffffff;border-radius:10px;overflow:hidden;border:1px solid #e6e8ec;">
   <tr><td style="background:#ffffff;padding:18px 24px 18px 14px;border-bottom:3px solid #1c9fd8;">
     <table role="presentation" cellpadding="0" cellspacing="0"><tr>{logo_cell}
       <td style="vertical-align:middle;">
         <div style="color:#0b3d59;font-size:18px;font-weight:700;">BOT DOCUMENTOS SUCAMEC</div>
         <div style="color:#5b6675;font-size:13px;margin-top:3px;">Lote procesado &middot; {etiqueta}</div>
       </td>
     </tr></table>
   </td></tr>
   <tr><td style="background:{banner_bg};padding:12px 28px;color:{banner_fg};font-size:14px;font-weight:600;">{banner}</td></tr>
   <tr><td style="padding:22px 28px;">
     <div style="font-size:11px;letter-spacing:.6px;color:#0e5680;font-weight:700;text-transform:uppercase;margin-bottom:4px;">Resumen</div>
     <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{resumen}</table>
     <div style="font-size:11px;letter-spacing:.6px;color:#0e5680;font-weight:700;text-transform:uppercase;margin:22px 0 4px;">Documentos por tipo</div>
     <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{desglose}</table>
   </td></tr>
   <tr><td style="background:#f4f5f7;padding:14px 28px;color:#9aa3b2;font-size:12px;text-align:center;">
     Generado automaticamente por el BOT &middot; No responder este correo
   </td></tr>
  </table>
 </td></tr></table>
</div>"""


def enviar_lote(lote_dir, etiqueta: str = "lote", logger=None) -> bool:
    """Comprime ``lote_dir`` y lo envia por correo. Devuelve True si se envio."""
    if not _env_bool("LOTE_MAIL_ENABLED", default=False):
        _log(logger, "info", "Envio de lote por correo deshabilitado (LOTE_MAIL_ENABLED=0).")
        return False

    if not graph_mail_enabled():
        _log(logger, "info", "Microsoft Graph deshabilitado (MS_GRAPH_MAIL_ENABLED=0); no se envia el lote.")
        return False

    lote_dir = Path(lote_dir)
    if not lote_dir.exists() or not lote_dir.is_dir():
        _log(logger, "warning", "Lote no existe o no es carpeta: %s", lote_dir)
        return False

    num_dni, num_archivos = _resumen_lote(lote_dir)
    if num_archivos == 0:
        _log(logger, "warning", "Lote vacio, no se envia: %s", lote_dir)
        return False

    config = load_mail_config()
    error = validate_mail_config(config)
    if error:
        _log(logger, "warning", "Config de correo invalida (%s); no se envia el lote.", error)
        return False

    # Override de destinatarios solo-para-lote (todo o nada): si LOTE_MAIL_TO tiene valor, se usan
    # LOTE_MAIL_TO/LOTE_MAIL_CC (CC vacio = sin copia). Si esta vacio, se usan MS_GRAPH_TO/CC.
    to_override = split_addresses(os.getenv("LOTE_MAIL_TO", ""))
    if to_override:
        config["to"] = to_override
        config["cc"] = split_addresses(os.getenv("LOTE_MAIL_CC", ""))

    inline_max_mb = _env_float("LOTE_MAIL_INLINE_MAX_MB", 3.0)
    max_mb = _env_float("LOTE_MAIL_MAX_MB", 30.0)

    tmp_dir = Path(tempfile.mkdtemp(prefix="lote_zip_"))
    zip_path = tmp_dir / f"{lote_dir.name}.zip"
    try:
        _comprimir_lote(lote_dir, zip_path)
        tam_mb = zip_path.stat().st_size / (1024 * 1024)

        asunto = f"{config['subject_prefix']} | {etiqueta} | {lote_dir.name}"
        fecha = _fecha_legible(lote_dir)
        breakdown = _contar_por_tipo(lote_dir)

        # Logo SUCAMEC incrustado (CID inline). Si no existe el archivo, se envia sin logo.
        logo_raw = os.getenv("LOTE_MAIL_LOGO_PATH", "flows/notifications/assets/sucamec_logo.png").strip()
        logo_path = Path(logo_raw) if logo_raw else None
        if logo_path and logo_path.exists():
            logo_cid = "sucamec_logo"
            inline_images = [(logo_cid, logo_path)]
        else:
            logo_cid, inline_images = None, None
            if logo_raw:
                _log(logger, "info", "Logo no encontrado en '%s'; se envia sin logo.", logo_raw)

        def _cuerpo(adjuntado: bool, motivo: str = "") -> str:
            return _cuerpo_html(
                etiqueta, lote_dir.name, num_dni, num_archivos, tam_mb, fecha, breakdown, adjuntado, motivo, logo_cid
            )

        # El adjunto es BEST-EFFORT; la confirmacion de ejecucion se envia SIEMPRE (Mail.Send).
        adjuntado = False
        motivo = ""
        try:
            if tam_mb > max_mb:
                motivo = f"ZIP de {tam_mb:.2f} MB supera el limite de {max_mb:.0f} MB"
            elif tam_mb <= inline_max_mb:
                send_mail(config, asunto, _cuerpo(True), attachment_paths=[zip_path], inline_images=inline_images)
                adjuntado = True
                _log(logger, "info", "Lote %s enviado (inline, %.2f MB) a %s", lote_dir.name, tam_mb, config["to"])
            else:
                send_mail_large(config, asunto, _cuerpo(True), zip_path, inline_images=inline_images)
                adjuntado = True
                _log(logger, "info", "Lote %s enviado (upload session, %.2f MB) a %s", lote_dir.name, tam_mb, config["to"])
        except Exception as exc_adj:
            s = str(exc_adj).lower()
            if "403" in s or "accessdenied" in s or "forbidden" in s:
                motivo = "falta el permiso Mail.ReadWrite en Azure (adjunto grande)"
            else:
                motivo = f"error al adjuntar ({exc_adj})"
            _log(logger, "warning", "Lote %s: no se pudo adjuntar -> %s", lote_dir.name, motivo)

        if adjuntado:
            return True

        # No se pudo adjuntar: igual se notifica la EJECUCION (sin adjunto), con el motivo en el banner.
        send_mail(config, asunto + " (SIN ADJUNTO)", _cuerpo(False, motivo), inline_images=inline_images)
        _log(logger, "info", "Lote %s: confirmacion SIN adjunto enviada a %s (%s)", lote_dir.name, config["to"], motivo)
        return True
    except Exception as exc:
        _log(logger, "error", "Fallo al enviar la confirmacion del lote %s: %s", lote_dir.name, exc)
        return False
    finally:
        try:
            if zip_path.exists():
                zip_path.unlink()
            tmp_dir.rmdir()
        except Exception:
            pass


def enviar_lote_post_run(lote_dir, etiqueta: str = "lote", logger=None) -> bool:
    """Para invocar desde los entrypoints run_*.py al terminar una corrida.

    En modo ORQUESTADO (run.bat all marca LOTE_MAIL_DEFER=1) NO envia aqui: run.bat envia
    UN solo correo del lote combinado al final. En cualquier otro caso (python run_*.py
    directo, o run.bat de un flujo individual) envia el lote del flujo. Nunca lanza."""
    if _env_bool("LOTE_MAIL_DEFER", default=False):
        _log(logger, "info", "Envio de correo diferido (run.bat all enviara el lote combinado al final).")
        return False
    try:
        return enviar_lote(lote_dir, etiqueta, logger)
    except Exception as exc:
        _log(logger, "warning", "No se pudo enviar el lote por correo: %s", exc)
        return False


def _main(argv: list[str]) -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    lote_dir = argv[1] if len(argv) > 1 and argv[1].strip() else os.getenv("GLOBAL_LOTE_DIR", "")
    etiqueta = argv[2] if len(argv) > 2 and argv[2].strip() else "lote"

    if not lote_dir:
        print("[ENVIAR LOTE] No se indico lote_dir ni GLOBAL_LOTE_DIR; nada que enviar.")
        return 0

    # Modo diagnostico: solo comprime y reporta tamano, sin enviar.
    if "--dry-run" in argv:
        lp = Path(lote_dir)
        if not lp.is_dir():
            print(f"[ENVIAR LOTE] Lote no existe: {lp}")
            return 0
        num_dni, num_archivos = _resumen_lote(lp)
        tmp = Path(tempfile.mkdtemp(prefix="lote_zip_"))
        zp = tmp / f"{lp.name}.zip"
        _comprimir_lote(lp, zp)
        print(f"[ENVIAR LOTE] dry-run | {lp.name} | DNIs={num_dni} archivos={num_archivos} "
              f"zip={zp.stat().st_size/1024/1024:.2f} MB")
        zp.unlink(); tmp.rmdir()
        return 0

    enviar_lote(lote_dir, etiqueta)
    return 0


if __name__ == "__main__":
    # Siempre sale 0: un fallo de correo no debe afectar el resultado de la corrida.
    sys.exit(_main(sys.argv))
