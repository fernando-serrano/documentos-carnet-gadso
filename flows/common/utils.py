"""Utilidades compartidas entre los subflujos documentales.

Por ahora concentra el guardado de la imagen **original** de la fuente en el
expediente (foto y firma), para que el expediente conserve siempre tanto la
original descargada como la versión tratada.
"""

import io
from pathlib import Path


_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/pjpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/x-ms-bmp": "bmp",
    "image/tiff": "tiff",
    "image/gif": "gif",
    "image/heic": "heic",
    "image/heif": "heic",
    "application/pdf": "pdf",
}

_PIL_FORMAT_EXT = {
    "JPEG": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "BMP": "bmp",
    "TIFF": "tiff",
    "GIF": "gif",
    "HEIF": "heic",
}


def ext_desde_mime(mime: str, content: bytes | None = None) -> str:
    """Resuelve la extensión de archivo a partir del MIME de Drive.

    Si el MIME no es reconocido y se pasa ``content``, intenta inferir el
    formato real con PIL. Como último recurso devuelve ``bin``.
    """
    clave = str(mime or "").strip().lower().split(";", 1)[0]
    ext = _MIME_EXT.get(clave)
    if ext:
        return ext

    if content:
        try:
            from PIL import Image

            with Image.open(io.BytesIO(content)) as img:
                fmt = str(img.format or "").upper()
            if fmt in _PIL_FORMAT_EXT:
                return _PIL_FORMAT_EXT[fmt]
        except Exception:
            pass

    return "bin"


def guardar_original(
    lote_dir: Path,
    dni: str,
    content: bytes,
    mime: str,
    prefix: str,
    overwrite_existing: bool = False,
) -> Path:
    """Guarda la imagen original (sin tratar) en ``lote_dir/<dni>/``.

    El nombre sigue el patrón ``<prefix>_<dni>_original.<ext>`` (p.ej.
    ``foto_carne_12345678_original.jpg``). Respeta ``overwrite_existing`` igual
    que el guardado de la versión tratada.
    """
    if not content:
        raise RuntimeError("contenido original vacio, no se puede guardar")

    destino_dir = lote_dir / dni
    destino_dir.mkdir(parents=True, exist_ok=True)

    ext = ext_desde_mime(mime, content)
    destino = destino_dir / f"{prefix}_{dni}_original.{ext}"

    if destino.exists() and not overwrite_existing:
        return destino

    if destino.exists() and overwrite_existing:
        destino.unlink()

    destino.write_bytes(content)
    if not destino.exists() or destino.stat().st_size <= 0:
        raise RuntimeError("original local quedo vacio tras guardar")
    return destino
