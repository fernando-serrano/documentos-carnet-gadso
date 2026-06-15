"""Cliente minimo de Microsoft Graph para enviar correo (OAuth client_credentials)."""
from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from pathlib import Path


def graph_token(config: dict) -> str:
    token_url = f"https://login.microsoftonline.com/{config['tenant_id']}/oauth2/v2.0/token"
    payload = urllib.parse.urlencode({
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }).encode("utf-8")
    request = urllib.request.Request(token_url, data=payload, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    token = str(body.get("access_token", "") or "").strip()
    if not token:
        raise Exception("Microsoft Graph no devolvio access_token")
    return token


_EXT_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".pdf": "application/pdf",
    ".zip": "application/zip",
}


def _content_type_por_ext(file_path: Path) -> str:
    return _EXT_CONTENT_TYPE.get(file_path.suffix.lower(), "application/octet-stream")


def attachment_from_path(file_path: Path, content_type: str | None = None) -> dict:
    content = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": file_path.name,
        "contentType": content_type or _content_type_por_ext(file_path),
        "contentBytes": content,
    }


def inline_image_from_path(file_path: Path, content_id: str) -> dict:
    """Imagen INLINE (logo) referenciada en el HTML como <img src='cid:content_id'>."""
    att = attachment_from_path(file_path)
    att["contentId"] = content_id
    att["isInline"] = True
    return att


def _construir_attachments(attachment_paths, inline_images) -> list[dict]:
    items = []
    for p in attachment_paths or []:
        if p and Path(p).exists():
            items.append(attachment_from_path(Path(p)))
    for cid, p in inline_images or []:
        if p and Path(p).exists():
            items.append(inline_image_from_path(Path(p), cid))
    return items


def send_mail(
    config: dict,
    subject: str,
    html_body: str,
    attachment_paths: list[Path] | None = None,
    inline_images: list[tuple[str, Path]] | None = None,
) -> None:
    token = graph_token(config)
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in config["to"]],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in config["cc"]],
    }
    attachments = _construir_attachments(attachment_paths, inline_images)
    if attachments:
        message["attachments"] = attachments
    payload = {"message": message, "saveToSentItems": True}

    sender = urllib.parse.quote(config["sender"])
    url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=60) as response:
        status_code = getattr(response, "status", 0) or 0
    if int(status_code) != 202:
        raise Exception(f"Microsoft Graph devolvio estado inesperado: {status_code}")


# Tamano del chunk para upload session: debe ser multiplo de 320 KB (requisito de Graph).
_UPLOAD_CHUNK = 320 * 1024 * 10  # ~3.2 MB


def _graph_post_json(url: str, token: str, body: dict, timeout: int = 60) -> tuple[int, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status_code = int(getattr(response, "status", 0) or 0)
        raw = response.read().decode("utf-8") if status_code not in (202, 204) else ""
    data = json.loads(raw) if raw else {}
    return status_code, data


def send_mail_large(
    config: dict,
    subject: str,
    html_body: str,
    attachment_path,
    inline_images: list[tuple[str, Path]] | None = None,
) -> None:
    """Envia un correo con UN adjunto grande (p.ej. ZIP de 9-12 MB) via upload session.

    El archivo es UNO solo; el upload session solo lo transporta por chunks y Graph lo
    reensambla del lado del servidor. El destinatario recibe un unico correo con un unico
    adjunto. Requiere permisos de aplicacion Mail.ReadWrite (crear borrador) + Mail.Send.
    Las imagenes inline (logo) van directo en el borrador (son pequenas).
    """
    path = Path(attachment_path)
    if not path.exists():
        raise Exception(f"adjunto no existe: {path}")

    token = graph_token(config)
    sender = urllib.parse.quote(config["sender"])
    base = f"https://graph.microsoft.com/v1.0/users/{sender}"

    # 1) Crear borrador con el mensaje (logo inline incluido; el ZIP grande va aparte).
    message = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [{"emailAddress": {"address": a}} for a in config["to"]],
        "ccRecipients": [{"emailAddress": {"address": a}} for a in config["cc"]],
    }
    inline_att = _construir_attachments(None, inline_images)
    if inline_att:
        message["attachments"] = inline_att
    status, draft = _graph_post_json(f"{base}/messages", token, message)
    message_id = str(draft.get("id", "") or "")
    if status not in (200, 201) or not message_id:
        raise Exception(f"no se pudo crear borrador (status={status})")

    # 2) Crear la upload session para el adjunto.
    total = int(path.stat().st_size)
    item = {
        "AttachmentItem": {
            "attachmentType": "file",
            "name": path.name,
            "size": total,
            "contentType": _content_type_por_ext(path),
        }
    }
    status, sess = _graph_post_json(
        f"{base}/messages/{message_id}/attachments/createUploadSession", token, item
    )
    upload_url = str(sess.get("uploadUrl", "") or "")
    if not upload_url:
        raise Exception(f"no se obtuvo uploadUrl (status={status})")

    # 3) Subir el archivo por chunks (Content-Range). El ultimo chunk cierra la sesion.
    with path.open("rb") as fh:
        start = 0
        while start < total:
            chunk = fh.read(_UPLOAD_CHUNK)
            if not chunk:
                break
            end = start + len(chunk) - 1
            put = urllib.request.Request(upload_url, data=chunk, method="PUT")
            put.add_header("Content-Length", str(len(chunk)))
            put.add_header("Content-Range", f"bytes {start}-{end}/{total}")
            with urllib.request.urlopen(put, timeout=120) as response:
                put_status = int(getattr(response, "status", 0) or 0)
            if put_status not in (200, 201, 202):
                raise Exception(f"fallo al subir chunk {start}-{end} (status={put_status})")
            start = end + 1

    # 4) Enviar el borrador ya con el adjunto.
    send_status, _ = _graph_post_json(f"{base}/messages/{message_id}/send", token, {})
    if send_status not in (202, 200, 204):
        raise Exception(f"Microsoft Graph devolvio estado inesperado al enviar: {send_status}")
