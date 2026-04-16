import importlib
import io
import re
import threading
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps

from .sheets import read_google_sheet_rows


_thread_local = threading.local()


def _normalizar_texto(texto: str) -> str:
    raw = str(texto or "").strip().lower()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _normalizar_dni(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _resolver_columna(fieldnames: list[str], candidatos: list[str]) -> str:
    normalizados = {_normalizar_texto(name): name for name in fieldnames}
    for candidato in candidatos:
        key = _normalizar_texto(candidato)
        if key in normalizados:
            return normalizados[key]
    return ""


def cargar_fuente_foto_por_dni(sheet_url: str, logger) -> dict[str, str]:
    rows, fieldnames = read_google_sheet_rows(sheet_url)
    dni_col = _resolver_columna(fieldnames, ["dni"])
    foto_col = _resolver_columna(fieldnames, ["cargar foto", "foto", "url foto", "link foto"])

    if not dni_col:
        raise RuntimeError("No se encontro columna DNI en hoja base de foto")
    if not foto_col:
        raise RuntimeError("No se encontro columna 'Cargar Foto' en hoja base")

    resultado: dict[str, str] = {}
    for row in rows:
        dni = _normalizar_dni(row.get(dni_col, ""))
        raw_foto = str(row.get(foto_col, "") or "").strip()
        if not dni or not raw_foto:
            continue
        resultado[dni] = raw_foto

    logger.info("[FOTO CARNE] Fuente cargada | filas=%s | dni_con_foto=%s", len(rows), len(resultado))
    return resultado


def _extraer_drive_file_id(raw: str) -> str:
    texto = str(raw or "").strip()
    if not texto:
        return ""

    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", texto):
        return texto

    m = re.search(r"/file/d/([A-Za-z0-9_-]+)", texto)
    if m:
        return m.group(1)

    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", texto)
    if m:
        return m.group(1)

    m = re.search(r"/d/([A-Za-z0-9_-]+)", texto)
    if m:
        return m.group(1)

    return ""


def _drive_service(credentials_path: str):
    svc = getattr(_thread_local, "drive_service", None)
    if svc is not None:
        return svc

    service_account = importlib.import_module("google.oauth2.service_account")
    google_build = importlib.import_module("googleapiclient.discovery").build

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    svc = google_build("drive", "v3", credentials=creds, cache_discovery=False)
    _thread_local.drive_service = svc
    return svc


def _descargar_drive_bytes(file_id: str, credentials_path: str) -> tuple[bytes, str]:
    service = _drive_service(credentials_path)
    meta = service.files().get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
    mime = str(meta.get("mimeType", "") or "")

    content = service.files().get_media(fileId=file_id, supportsAllDrives=True).execute()
    if not isinstance(content, (bytes, bytearray)):
        raise RuntimeError("Drive no devolvio binario para foto")
    return bytes(content), mime


def _cargar_cv2():
    cv2_mod = getattr(_thread_local, "cv2_mod", None)
    if cv2_mod is not None:
        return cv2_mod
    try:
        cv2_mod = importlib.import_module("cv2")
    except Exception:
        cv2_mod = False
    _thread_local.cv2_mod = cv2_mod
    return cv2_mod


def _cargar_rembg():
    rembg_mod = getattr(_thread_local, "rembg_mod", None)
    if rembg_mod is not None:
        return rembg_mod
    try:
        rembg_mod = importlib.import_module("rembg")
    except Exception:
        rembg_mod = False
    _thread_local.rembg_mod = rembg_mod
    return rembg_mod


def _detectar_rostros_frontal(img_rgb: Image.Image) -> list[tuple[int, int, int, int]]:
    cv2_mod = _cargar_cv2()
    if cv2_mod is False:
        return []

    cascade = getattr(_thread_local, "face_cascade", None)
    if cascade is None:
        try:
            cascade_path = str(cv2_mod.data.haarcascades) + "haarcascade_frontalface_default.xml"
            cascade = cv2_mod.CascadeClassifier(cascade_path)
            if cascade.empty():
                _thread_local.face_cascade = False
                return []
            _thread_local.face_cascade = cascade
        except Exception:
            _thread_local.face_cascade = False
            return []
    elif cascade is False:
        return []

    np_mod = importlib.import_module("numpy")
    arr = np_mod.array(img_rgb)
    gray = cv2_mod.cvtColor(arr, cv2_mod.COLOR_RGB2GRAY)
    gray = cv2_mod.equalizeHist(gray)

    faces = cascade.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=6,
        minSize=(80, 80),
    )
    boxes = [tuple(int(v) for v in face) for face in faces]
    boxes.sort(key=lambda box: box[2] * box[3], reverse=True)
    return boxes


def _seleccionar_rostro_confiable(
    faces: list[tuple[int, int, int, int]],
    img_w: int,
    img_h: int,
) -> tuple[int, int, int, int] | None:
    if not faces:
        return None

    face0 = faces[0]
    area0 = face0[2] * face0[3]
    if area0 < int(img_w * img_h * 0.04):
        return None

    if len(faces) > 1:
        area1 = faces[1][2] * faces[1][3]
        # Si hay dos rostros de tamano similar, evitamos recortes agresivos para no crear falsos positivos.
        if area0 < int(area1 * 1.35):
            return None

    return face0


def _calcular_recorte_formal(
    img_w: int,
    img_h: int,
    face: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    fx, fy, fw, fh = face
    target_aspect = 3.0 / 4.0

    crop_h = int(max(fh * 2.7, fh / 0.34))
    crop_h = max(fh + 80, min(crop_h, img_h))
    crop_w = int(crop_h * target_aspect)

    if crop_w > img_w:
        crop_w = img_w
        crop_h = int(crop_w / target_aspect)
        crop_h = min(crop_h, img_h)

    face_cx = fx + (fw / 2.0)
    face_cy = fy + (fh * 0.45)

    x1 = int(round(face_cx - (crop_w / 2.0)))
    y1 = int(round(face_cy - (crop_h * 0.38)))

    x1 = max(0, min(x1, img_w - crop_w))
    y1 = max(0, min(y1, img_h - crop_h))

    return (x1, y1, x1 + crop_w, y1 + crop_h)


def _forzar_relacion_3x4(img_rgb: Image.Image) -> tuple[Image.Image, str]:
    w, h = img_rgb.size
    target_aspect = 3.0 / 4.0
    current_aspect = w / float(h)

    if abs(current_aspect - target_aspect) <= 0.02:
        return img_rgb, ""

    if current_aspect > target_aspect:
        crop_w = int(h * target_aspect)
        x1 = max(0, (w - crop_w) // 2)
        cropped = img_rgb.crop((x1, 0, x1 + crop_w, h))
        return cropped, "aspect_crop_width"

    crop_h = int(w / target_aspect)
    y1 = max(0, int((h - crop_h) * 0.45))
    cropped = img_rgb.crop((0, y1, w, y1 + crop_h))
    return cropped, "aspect_crop_height"


def _fondo_es_mayormente_blanco(img_rgb: Image.Image) -> bool:
    cv2_mod = _cargar_cv2()
    if cv2_mod is False:
        return False

    np_mod = importlib.import_module("numpy")
    arr = np_mod.array(img_rgb)
    h, w = arr.shape[:2]
    border_px = max(12, int(min(h, w) * 0.08))

    strips = [
        arr[:border_px, :, :],
        arr[h - border_px :, :, :],
        arr[:, :border_px, :],
        arr[:, w - border_px :, :],
    ]
    border = np_mod.concatenate([s.reshape(-1, 3) for s in strips], axis=0)

    white_mask = (border[:, 0] >= 235) & (border[:, 1] >= 235) & (border[:, 2] >= 235)
    white_ratio = float(white_mask.mean()) if border.size else 0.0
    return white_ratio >= 0.72


def _limpiar_foto_ia(img_rgb: Image.Image) -> tuple[Image.Image, str, bool]:
    """
    Segmentación IA (rembg + U²-Net) para remover fondo.
    Mucho más preciso que OpenCV clásico para fotos con iluminación irregular.
    Devuelve (imagen_procesada, detalle, fue_aplicado).
    """
    rembg_mod = _cargar_rembg()
    if rembg_mod is False:
        return img_rgb, "bg_remove_ia_skip_rembg_unavailable", False

    try:
        # rembg.remove() retorna imagen con canal alpha (RGBA con fondo transparente)
        output = rembg_mod.remove(img_rgb, alpha_matting=True, alpha_matting_foreground_threshold=240)

        # Convertir a fondo blanco puro (SUCAMEC estándar)
        background = Image.new("RGBA", output.size, (255, 255, 255, 255))
        final = Image.alpha_composite(background, output)
        final = final.convert("RGB")

        # Post-procesado OpenCV ligero: bilateral filter para suavizar piel sin perder detalle
        cv2_mod = _cargar_cv2()
        if cv2_mod is not False:
            try:
                np_mod = importlib.import_module("numpy")
                img_cv = cv2_mod.cvtColor(np_mod.array(final), cv2_mod.COLOR_RGB2BGR)
                # Bilateral filter: suaviza piel pero preserva bordes
                img_cv = cv2_mod.bilateralFilter(img_cv, 9, 75, 75)
                img_cv = cv2_mod.cvtColor(img_cv, cv2_mod.COLOR_BGR2RGB)
                final = Image.fromarray(img_cv)
            except Exception:
                # Si bilateral falla, usamos resultado sin suavizado
                pass

        return final, "bg_white_ia_applied", True

    except Exception as exc:
        return img_rgb, f"bg_remove_ia_error={exc}", False


def _remover_fondo_blanco_conservador(img_rgb: Image.Image) -> tuple[Image.Image, str, bool]:
    cv2_mod = _cargar_cv2()
    if cv2_mod is False:
        return img_rgb, "bg_remove_skip_no_cv2", False

    np_mod = importlib.import_module("numpy")
    arr = np_mod.array(img_rgb)
    h, w = arr.shape[:2]

    faces = _detectar_rostros_frontal(img_rgb)
    face = _seleccionar_rostro_confiable(faces, w, h)
    if face is None:
        return img_rgb, "bg_remove_skip_face_not_confident", False

    fx, fy, fw, fh = face
    rect_x = max(1, int(fx - fw * 1.2))
    rect_y = max(1, int(fy - fh * 0.8))
    rect_w = min(w - rect_x - 1, int(fw * 3.4))
    rect_h = min(h - rect_y - 1, int(fh * 5.0))

    if rect_w < int(w * 0.35) or rect_h < int(h * 0.45):
        margin = max(6, int(min(w, h) * 0.05))
        rect_x = margin
        rect_y = margin
        rect_w = w - (margin * 2)
        rect_h = h - (margin * 2)

    if rect_w <= 2 or rect_h <= 2:
        return img_rgb, "bg_remove_skip_invalid_rect", False

    bgd_model = np_mod.zeros((1, 65), np_mod.float64)
    fgd_model = np_mod.zeros((1, 65), np_mod.float64)
    mask = np_mod.zeros((h, w), np_mod.uint8)

    try:
        cv2_mod.grabCut(
            arr,
            mask,
            (int(rect_x), int(rect_y), int(rect_w), int(rect_h)),
            bgd_model,
            fgd_model,
            3,
            cv2_mod.GC_INIT_WITH_RECT,
        )
    except Exception as exc:
        return img_rgb, f"bg_remove_skip_grabcut_error={exc}", False

    fg_mask = (mask == cv2_mod.GC_FGD) | (mask == cv2_mod.GC_PR_FGD)
    fg_ratio = float(fg_mask.mean())
    if fg_ratio < 0.18 or fg_ratio > 0.86:
        return img_rgb, "bg_remove_skip_unstable_mask", False

    out = arr.copy()
    out[~fg_mask] = (255, 255, 255)

    white_bg_ratio = float((out[~fg_mask] >= 245).all(axis=1).mean()) if (~fg_mask).any() else 0.0
    if white_bg_ratio < 0.95:
        return img_rgb, "bg_remove_skip_low_white_ratio", False

    out_img = Image.fromarray(out)
    return out_img, "bg_white_applied", True


def _aplicar_pretratamiento_general(image: Image.Image) -> tuple[Image.Image, str, bool]:
    img = ImageOps.exif_transpose(image).convert("RGB")
    detalles: list[str] = []
    fondo_blanco_aplicado = False

    try:
        faces = _detectar_rostros_frontal(img)
    except Exception:
        faces = []

    if faces:
        face = _seleccionar_rostro_confiable(faces, img.size[0], img.size[1])
        if face is not None:
            top_ratio = face[1] / float(max(1, img.size[1]))
            box = _calcular_recorte_formal(img.size[0], img.size[1], face)
            img = img.crop(box)
            if top_ratio > 0.22:
                detalles.append("top_margin_trimmed")
            detalles.append("face_crop_applied")
        else:
            detalles.append("face_ambiguous_skip_crop")
    else:
        detalles.append("face_not_detected_skip_crop")

    img, aspect_detail = _forzar_relacion_3x4(img)
    if aspect_detail:
        detalles.append(aspect_detail)

    if not _fondo_es_mayormente_blanco(img):
        # Intentar IA primero (rembg + U²-Net), fallback a OpenCV si falla
        img_bg, bg_detail, applied = _limpiar_foto_ia(img)
        detalles.append(bg_detail)
        if applied:
            img = img_bg
            fondo_blanco_aplicado = True
        else:
            # Fallback a OpenCV clásico si IA no fue aplicada
            img_bg, bg_detail_cv, applied_cv = _remover_fondo_blanco_conservador(img)
            detalles.append(bg_detail_cv)
            if applied_cv:
                img = img_bg
                fondo_blanco_aplicado = True
    else:
        detalles.append("bg_already_white")

    return img, ";".join(detalles), fondo_blanco_aplicado


def _jpeg_menor_a_limite(
    image: Image.Image,
    target_bytes: int,
    min_quality: int = 50,
    max_oversize_pct: float = 1.15,
) -> tuple[bytes, str]:
    if target_bytes <= 0:
        raise RuntimeError("Limite de bytes invalido para foto")

    img = image.convert("RGB")
    base_w, base_h = img.size
    allow_bytes = int(max(1, target_bytes * max_oversize_pct))
    best_candidate: tuple[int, int, float, bytes] | None = None

    quality_steps = tuple(range(95, min_quality - 1, -5))
    if quality_steps[-1] != min_quality:
        quality_steps += (min_quality,)
    scale_steps = (1.0, 0.92, 0.85, 0.78, 0.72, 0.66, 0.60)

    for scale in scale_steps:
        w = max(240, int(base_w * scale))
        h = max(320, int(base_h * scale))
        resized = img.resize((w, h), Image.LANCZOS)

        for quality in quality_steps:
            buffer = io.BytesIO()
            resized.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
                subsampling=2,
            )
            data = buffer.getvalue()
            size = len(data)

            if size <= target_bytes:
                detalle = f"jpeg_ok size={size} quality={quality} scale={scale:.2f}"
                return data, detalle

            if size <= allow_bytes:
                if best_candidate is None or quality > best_candidate[1] or (
                    quality == best_candidate[1] and size < best_candidate[0]
                ):
                    best_candidate = (size, quality, scale, data)

    if best_candidate is not None:
        size, quality, scale, data = best_candidate
        detalle = f"jpeg_ok_oversize size={size} quality={quality} scale={scale:.2f} oversize_pct={size/target_bytes:.2f}"
        return data, detalle

    # Segunda fase: solo si es estrictamente necesario intentamos versiones más agresivas,
    # pero no bajamos de quality=30 para evitar resultados destrozados.
    for scale in (0.50, 0.40, 0.32):
        w = max(160, int(base_w * scale))
        h = max(200, int(base_h * scale))
        resized = img.resize((w, h), Image.LANCZOS)

        for quality in (35, 30):
            buffer = io.BytesIO()
            resized.save(
                buffer,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
                subsampling=2,
            )
            data = buffer.getvalue()
            if len(data) <= target_bytes:
                detalle = f"jpeg_fallback_ok size={len(data)} quality={quality} scale={scale:.2f}"
                return data, detalle

    raise RuntimeError("No se pudo reducir foto por debajo del limite requerido")


def _guardar_foto_local(lote_dir: Path, dni: str, contenido_jpg: bytes, overwrite_existing: bool) -> Path:
    destino_dir = lote_dir / dni
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"foto_carne_{dni}.jpg"

    if destino.exists() and not overwrite_existing:
        return destino

    if destino.exists() and overwrite_existing:
        destino.unlink()

    destino.write_bytes(contenido_jpg)
    if not destino.exists() or destino.stat().st_size <= 0:
        raise RuntimeError("Foto local quedo vacia tras guardar")
    return destino


def procesar_foto_carne_por_dni(
    dni: str,
    foto_source_map: dict[str, str],
    credentials_path: str,
    lote_dir: Path,
    max_kb: int,
    headroom_pct: float,
    overwrite_existing: bool,
    min_jpeg_quality: int,
    max_jpeg_oversize_pct: float,
) -> dict:
    dni_digits = _normalizar_dni(dni)
    if not dni_digits:
        return {"status": "error", "observation": "DNI INVALIDO", "detail": "dni vacio"}

    raw = str(foto_source_map.get(dni_digits, "") or "").strip()
    if not raw:
        return {
            "status": "sin_registros",
            "observation": f"{dni_digits} SIN CARGAR FOTO EN FUENTE",
            "detail": "sin valor en Cargar Foto",
        }

    file_id = _extraer_drive_file_id(raw)
    if not file_id:
        return {
            "status": "error",
            "observation": f"{dni_digits} URL FOTO INVALIDA",
            "detail": f"valor_fuente={raw}",
        }

    content, mime = _descargar_drive_bytes(file_id, credentials_path)
    image = Image.open(io.BytesIO(content))

    pre_img = image
    pre_detail = "preprocess_skip"
    fondo_blanco_aplicado = False
    try:
        pre_img, pre_detail, fondo_blanco_aplicado = _aplicar_pretratamiento_general(image)
    except Exception as exc:
        pre_detail = f"preprocess_error={exc}"

    target_bytes = max(1, int(max_kb * 1024 * headroom_pct))
    out_jpg, detail = _jpeg_menor_a_limite(pre_img, target_bytes, min_quality=min_jpeg_quality, max_oversize_pct=max_jpeg_oversize_pct)
    local_path = _guardar_foto_local(lote_dir, dni_digits, out_jpg, overwrite_existing)

    detail_full = f"{pre_detail} {detail}".strip()
    observation = "DESCARGADO CON FONDO BLANCO" if fondo_blanco_aplicado else "DESCARGADO SIN OBSERVACIONES"

    return {
        "status": "ok",
        "observation": observation,
        "detail": f"mime={mime} {detail_full}",
        "local_path": str(local_path),
    }
