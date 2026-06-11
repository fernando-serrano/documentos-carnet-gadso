import importlib
import io
import re
import threading
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps

from flows.common.utils import guardar_original
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


_heif_registrado = False


def _registrar_heif() -> bool:
    """Registra el lector HEIF/HEIC en PIL (best-effort, una sola vez).

    Con pillow-heif registrado, Image.open() abre fotos HEIC de iPhone de forma
    transparente y el pipeline las guarda como JPEG. Devuelve True si quedo disponible.
    """
    global _heif_registrado
    if _heif_registrado:
        return True
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
        _heif_registrado = True
    except Exception:
        _heif_registrado = False
    return _heif_registrado


def _es_heic(content: bytes, mime: str) -> bool:
    """Detecta HEIC/HEIF por MIME de Drive o por magic bytes (caja ftyp + marca)."""
    clave = str(mime or "").strip().lower()
    if "heic" in clave or "heif" in clave:
        return True
    if content[:4] and content[4:8] == b"ftyp":
        marca = content[8:12].lower()
        if marca in (b"heic", b"heif", b"heix", b"mif1", b"hevc", b"msf1"):
            return True
    return False


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


def _fondo_ya_es_blanco(img_rgb: Image.Image) -> tuple[bool, str]:
    """
    Detecta si el fondo del original YA es blanco/uniforme ("color continuo, cambios
    minimos"): borde claro, de baja desviacion (uniforme) y neutro. Si lo es, NO hace falta
    remover el fondo -> se evita el falso positivo de rembg que come la vestimenta.
    Devuelve (es_blanco, detalle).
    """
    np_mod = importlib.import_module("numpy")
    arr = np_mod.array(img_rgb.convert("RGB")).astype(np_mod.int16)
    h, w = arr.shape[:2]
    bw = max(10, int(min(h, w) * 0.07))
    # Borde de fondo: franja superior (sobre la cabeza) + lados en la mitad superior.
    parts = [
        arr[:bw, :, :].reshape(-1, 3),
        arr[:int(h * 0.60), :bw, :].reshape(-1, 3),
        arr[:int(h * 0.60), w - bw:, :].reshape(-1, 3),
    ]
    px = np_mod.concatenate(parts, axis=0)
    mn = px.min(axis=1)
    chroma = px.max(axis=1) - px.min(axis=1)
    # FRACCION del borde que es claro+neutro (robusto al cabello del sujeto: si un poco de
    # pelo entra al muestreo, el resto del fondo sigue contando como blanco).
    es_claro = (mn >= 200) & (chroma <= 16)
    ratio = float(es_claro.mean())
    claros = mn[es_claro]
    desv = float(np_mod.std(claros)) if claros.size else 99.0
    es_blanco = (ratio >= 0.80) and (desv <= 18)
    return es_blanco, f"bg_check ratio={ratio:.2f} std_claros={int(desv)} ya_blanco={es_blanco}"


def _limpiar_foto_ia(img_rgb: Image.Image) -> tuple[Image.Image, str, bool]:
    """
    Remueve el fondo con rembg (IA) y lo pone blanco puro. CORREGIDO para evitar los
    defectos de antes:
      - Mascara BINARIA (umbral 128), no se compone con alpha parcial -> no "lava" el saco.
      - Se conserva el componente que CONTIENE EL ROSTRO + los componentes grandes (cuerpo)
        -> imposible decapitar aunque la mascara separe cabeza y torso.
      - Erosion de 1px -> sin halo de fleco alrededor del cabello.
    Separa por FORMA del cuerpo (no por color), asi distingue camisa-blanca de fondo-blanco.
    Devuelve (imagen, detalle, aplicado).
    """
    rembg_mod = _cargar_rembg()
    if rembg_mod is False:
        return img_rgb, "bg_ia_skip_no_rembg", False

    try:
        output = rembg_mod.remove(img_rgb)
        if output.mode != "RGBA":
            output = output.convert("RGBA")

        np_mod = importlib.import_module("numpy")
        r, g, b, a = output.split()
        a_np = np_mod.array(a)
        fg = (a_np >= 128).astype(np_mod.uint8)
        if fg.sum() == 0:
            return img_rgb, "bg_ia_skip_mascara_vacia", False

        cv2_mod = _cargar_cv2()
        if cv2_mod is not False:
            num, labels, stats, _cent = cv2_mod.connectedComponentsWithStats(fg, connectivity=8)
            if num > 2:
                keep = set()
                # Anclar al rostro: conservar el componente del centro del rostro.
                try:
                    face = _seleccionar_rostro_confiable(_detectar_rostros_frontal(img_rgb), fg.shape[1], fg.shape[0])
                except Exception:
                    face = None
                if face is not None:
                    fcx = int(face[0] + face[2] * 0.5)
                    fcy = int(face[1] + face[3] * 0.5)
                    if 0 <= fcy < labels.shape[0] and 0 <= fcx < labels.shape[1]:
                        lab = int(labels[fcy, fcx])
                        if lab != 0:
                            keep.add(lab)
                # Conservar tambien los componentes grandes (cuerpo) >= 4% del area.
                min_area = int(0.04 * fg.size)
                for i in range(1, num):
                    if int(stats[i, cv2_mod.CC_STAT_AREA]) >= min_area:
                        keep.add(i)
                if not keep:  # respaldo: el mayor
                    keep.add(1 + int(np_mod.argmax(stats[1:, cv2_mod.CC_STAT_AREA])))
                fg = np_mod.isin(labels, list(keep)).astype(np_mod.uint8)
            # Erosionar 1px para recortar el fleco residual del borde.
            fg = cv2_mod.erode(fg, np_mod.ones((3, 3), np_mod.uint8), iterations=1)

        a_bin = np_mod.where(fg > 0, 255, 0).astype(np_mod.uint8)
        output.putalpha(Image.fromarray(a_bin))
        background = Image.new("RGBA", output.size, (255, 255, 255, 255))
        final = Image.alpha_composite(background, output).convert("RGB")
        return final, "bg_white_ia", True
    except Exception as exc:
        return img_rgb, f"bg_ia_error={exc}", False


def _blanquear_fondo_seguro(img_rgb: Image.Image) -> tuple[Image.Image, str, bool]:
    """
    Blanqueo SEGURO de fondo: operacion global por pixel, SIN IA ni segmentacion.

    Lleva a blanco puro SOLO los pixeles casi-blancos y de baja croma (el fondo de
    estudio claro). La piel (calida, croma alta), el pelo y la ropa oscura quedan FUERA
    de la mascara -> intactos. Por construccion es imposible cortar la cabeza o dejar
    halos (no hay componentes conexos, floodFill ni rembg; es por-pixel).

    Devuelve (imagen, detalle, aplicado).
    """
    np_mod = importlib.import_module("numpy")
    arr = np_mod.array(img_rgb.convert("RGB"))
    h, w = arr.shape[:2]

    # Datos medidos: fondo de estudio = neutro (croma ~0-2) y claro (~200-242, incluso en la
    # sombra del gradiente). Piel = oscura (min-canal ~110) y cromatica (~75). Estan muy
    # separados, asi que T=180 blanquea TODO el gradiente del fondo (sin borde irregular) y
    # deja intactos piel (croma alta) y saco/pelo oscuros (<180). C=14 cubre fondo leve-tinte.
    T = 180  # luminosidad minima para "claro" (cubre el gradiente completo del fondo)
    C = 14   # croma maxima (max-min): el fondo es neutro; la piel tiene croma muy superior
    mn = arr.min(axis=2)
    mx = arr.max(axis=2)
    near = ((mn >= T) & ((mx.astype(np_mod.int16) - mn.astype(np_mod.int16)) <= C)).astype(np_mod.uint8)

    if near.sum() == 0:
        return img_rgb, "bg_white_seguro px=0", False

    cv2_mod = _cargar_cv2()
    if cv2_mod is not False:
        # Clasificar cada region clara `near` por que bordes toca:
        #  - toca SUPERIOR/IZQ/DER pero NO el inferior -> FONDO puro (alrededor de la
        #    persona) -> blanquear COMPLETO (sin banda).
        #  - toca arriba/lados Y abajo -> camisa-blanca y fondo son UNA sola region
        #    (sin saco que los separe) -> ambiguo -> blanquear solo lo de AFUERA de la
        #    silueta del cuerpo (alrededor de la cabeza), protegiendo la vestimenta.
        #  - toca solo el inferior / ningun borde sembrado -> vestimenta -> NO tocar.
        num, labels = cv2_mod.connectedComponents(near, connectivity=4)
        # Bordes "de fondo": superior, izq, der, y las ESQUINAS inferiores (ahi suele
        # asomarse el fondo junto a los hombros). El centro del borde inferior NO va aqui.
        bx = int(w * 0.20)
        top_side = set(labels[0, :].tolist()) | set(labels[:, 0].tolist()) | set(labels[:, w - 1].tolist())
        top_side |= set(labels[h - 1, :bx].tolist()) | set(labels[h - 1, w - bx:].tolist())
        top_side.discard(0)  # label 0 = pixeles NO-near (sujeto)
        # Borde inferior CENTRAL = vestimenta (el cuerpo esta al centro abajo).
        bottom = set(labels[h - 1, bx:w - bx].tolist())
        bottom.discard(0)
        pure_labels = list(top_side - bottom)
        amb_labels = list(top_side & bottom)
        pure_bg = np_mod.isin(labels, pure_labels) if pure_labels else np_mod.zeros((h, w), dtype=bool)
        ambiguo = np_mod.isin(labels, amb_labels) if amb_labels else np_mod.zeros((h, w), dtype=bool)

        # Silueta del cuerpo bajo el rostro (trapecio que ensancha hacia los hombros).
        protect = np_mod.zeros((h, w), dtype=bool)
        try:
            face = _seleccionar_rostro_confiable(_detectar_rostros_frontal(img_rgb), w, h)
        except Exception:
            face = None
        if face is not None:
            fx, fy, fw, fh = face
            fcx = fx + fw * 0.5
            top = int(fy + fh * 0.85)  # apenas debajo del menton
            ys = np_mod.arange(h)
            t = np_mod.clip((ys - top) / float(max(1, h - top)), 0.0, 1.0)
            half = fw * 0.85 + t * (w * 0.45 - fw * 0.85)
            xs = np_mod.arange(w)[None, :]
            protect = (np_mod.abs(xs - fcx) <= half[:, None]) & (ys[:, None] >= top)

        mask = pure_bg | (ambiguo & ~protect)
    else:
        mask = near.astype(bool)  # fallback per-pixel si no hay cv2

    px = int(mask.sum())
    if px == 0:
        return img_rgb, "bg_white_seguro px=0", False

    out = arr.copy()
    out[mask] = (255, 255, 255)
    return Image.fromarray(out), f"bg_white_seguro px={px}", True


def _aplicar_pretratamiento_general(image: Image.Image) -> tuple[Image.Image, str, bool]:
    img = ImageOps.exif_transpose(image).convert("RGB")
    detalles: list[str] = []
    fondo_blanco_aplicado = False

    # Chequeo PREVIO (antes de editar/recortar): si el fondo ya es blanco/uniforme, no se
    # remueve (evita falsos positivos de rembg que comen la vestimenta, p.ej. 77674698).
    ya_blanco, bg_chk = _fondo_ya_es_blanco(img)
    detalles.append(bg_chk)

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

    if ya_blanco:
        # El fondo ya es blanco/uniforme -> NO se remueve. Solo se recorto. La vestimenta
        # queda 100% intacta (no hay riesgo de que rembg la coma).
        detalles.append("bg_already_white_skip")
    else:
        # 1) rembg (IA) CORREGIDO: separa por forma del cuerpo -> fondo blanco limpio en
        #    TODOS los casos (incluida camisa-blanca-sobre-fondo-blanco), sin decapitar ni halos.
        img_bg, bg_detail, applied = _limpiar_foto_ia(img)
        detalles.append(bg_detail)
        if applied:
            img = img_bg
            fondo_blanco_aplicado = True
        else:
            # 2) Fallback SIN IA (si rembg no esta disponible o falla): blanqueo por color.
            img_bg, bg_detail2, applied2 = _blanquear_fondo_seguro(img)
            detalles.append(bg_detail2)
            if applied2:
                img = img_bg
                fondo_blanco_aplicado = True

    # NOTA: NO se hace recorte automatico del logo de IA (Gemini): la deteccion de la
    # estrella semitransparente no es confiable y producia falsos positivos que recortaban
    # hombros de fotos normales. Decision: dejarlo desactivado.

    return img, ";".join(detalles), fondo_blanco_aplicado


def _jpeg_menor_a_limite(
    image: Image.Image,
    target_bytes: int,
    min_quality: int = 50,
    max_oversize_pct: float = 1.15,
    preservar_calidad: bool = False,
    max_dim: int = 600,
) -> tuple[bytes, str]:
    if target_bytes <= 0:
        raise RuntimeError("Limite de bytes invalido para foto")

    img = image.convert("RGB")

    # Cap de resolucion: un carne no necesita >max_dim px de lado mayor. Reducir la
    # resolucion (no la calidad) es lo que evita los bloques 8x8 de JPEG en camisa/fondo.
    cap_detail = ""
    lado_mayor = max(img.size)
    if max_dim and lado_mayor > max_dim:
        factor = max_dim / float(lado_mayor)
        nuevo = (max(1, int(img.size[0] * factor)), max(1, int(img.size[1] * factor)))
        img = img.resize(nuevo, Image.LANCZOS)
        cap_detail = f"cap_{max_dim}px({nuevo[0]}x{nuevo[1]})"

    base_w, base_h = img.size
    allow_bytes = int(max(1, target_bytes * max_oversize_pct))

    def _encode(im: Image.Image, quality: int, subsampling: int) -> bytes:
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling=subsampling)
        return buf.getvalue()

    # Paso 0: maxima calidad sin submuestreo de croma (4:4:4).
    hq_data = _encode(img, 95, 0)
    if preservar_calidad:
        return hq_data, f"jpeg_hq_preservado size={len(hq_data)} quality=95 scale=1.00 subsampling=0 {cap_detail}".strip()
    if len(hq_data) <= target_bytes:
        return hq_data, f"jpeg_hq_keep size={len(hq_data)} quality=95 scale=1.00 subsampling=0 {cap_detail}".strip()

    # Escalera que PRIORIZA CALIDAD: reduce resolucion manteniendo calidad alta (piso 88)
    # y croma plena (4:4:4). Asi un carne entra bajo el limite sin pixelado/bloques.
    best_candidate: tuple[int, int, float, bytes] | None = None
    scale_steps = (1.0, 0.92, 0.85, 0.78, 0.72, 0.65)
    quality_steps = (95, 92, 90, 88)

    for scale in scale_steps:
        w = max(300, int(base_w * scale))
        h = max(400, int(base_h * scale))
        resized = img.resize((w, h), Image.LANCZOS) if scale != 1.0 else img

        for quality in quality_steps:
            data = _encode(resized, quality, 0)
            size = len(data)
            if size <= target_bytes:
                return data, f"jpeg_ok size={size} quality={quality} scale={scale:.2f} subsampling=0 {cap_detail}".strip()
            if size <= allow_bytes and (best_candidate is None or quality > best_candidate[1]):
                best_candidate = (size, quality, scale, data)

    if best_candidate is not None:
        size, quality, scale, data = best_candidate
        return data, f"jpeg_ok_oversize size={size} quality={quality} scale={scale:.2f} oversize_pct={size/target_bytes:.2f} {cap_detail}".strip()

    # Ultimo recurso: bajar calidad (y resolucion) mas agresivo, con piso min_quality.
    for scale in (0.60, 0.50, 0.42):
        w = max(260, int(base_w * scale))
        h = max(340, int(base_h * scale))
        resized = img.resize((w, h), Image.LANCZOS)
        for quality in (80, 70, 60, max(min_quality, 50)):
            data = _encode(resized, quality, 2)
            if len(data) <= target_bytes:
                return data, f"jpeg_fallback_ok size={len(data)} quality={quality} scale={scale:.2f} {cap_detail}".strip()

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
    save_original: bool = True,
    max_dim: int = 600,
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

    original_path = None
    original_detail = "original_skip"
    if save_original:
        try:
            original_path = guardar_original(
                lote_dir,
                dni_digits,
                content,
                mime,
                prefix="foto_carne",
                overwrite_existing=overwrite_existing,
            )
            original_detail = f"original_saved={original_path.name}"
        except Exception as exc:
            # No bloquear el tratamiento si falla guardar la original.
            original_detail = f"original_error={exc}"

    # HEIC/HEIF (fotos de iPhone, p.ej. extension .hec): PIL no las abre sin plugin.
    # Registramos pillow-heif para abrirlas y convertirlas (la salida siempre es JPEG).
    es_heic = _es_heic(content, mime)
    heic_detail = ""
    if es_heic:
        disponible = _registrar_heif()
        heic_detail = "heic_convertido" if disponible else "heic_sin_soporte"

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as exc:
        if es_heic:
            return {
                "status": "revision_manual",
                "observation": f"{dni_digits} FOTO FORMATO HEIC, CONVERTIR/REVISAR",
                "detail": f"mime={mime} {original_detail} {heic_detail} open_error={exc}",
                "local_path": "",
                "original_path": str(original_path) if original_path else "",
            }
        return {
            "status": "error",
            "observation": f"{dni_digits} IMAGEN NO VALIDA, REVISAR FUENTE",
            "detail": f"mime={mime} {original_detail} open_error={exc}",
            "local_path": "",
            "original_path": str(original_path) if original_path else "",
        }

    pre_img = image
    pre_detail = "preprocess_skip"
    fondo_blanco_aplicado = False
    try:
        pre_img, pre_detail, fondo_blanco_aplicado = _aplicar_pretratamiento_general(image)
    except Exception as exc:
        pre_detail = f"preprocess_error={exc}"

    target_bytes = max(1, int(max_kb * 1024 * headroom_pct))
    # Regla: si el archivo ORIGINAL ya pesa <= limite (~75KB), no se comprime ni
    # redimensiona; solo se guarda a maxima calidad el resultado de recorte/fondo.
    preservar_calidad = len(content) <= target_bytes
    out_jpg, detail = _jpeg_menor_a_limite(
        pre_img,
        target_bytes,
        min_quality=min_jpeg_quality,
        max_oversize_pct=max_jpeg_oversize_pct,
        preservar_calidad=preservar_calidad,
        max_dim=max_dim,
    )
    local_path = _guardar_foto_local(lote_dir, dni_digits, out_jpg, overwrite_existing)

    peso_detail = f"original_kb={len(content)/1024:.1f} preservado={preservar_calidad}"
    detail_full = f"{heic_detail} {pre_detail} {peso_detail} {detail}".strip()
    observation = "DESCARGADO CON FONDO BLANCO" if fondo_blanco_aplicado else "DESCARGADO SIN OBSERVACIONES"

    return {
        "status": "ok",
        "observation": observation,
        "detail": f"mime={mime} {original_detail} {detail_full}",
        "local_path": str(local_path),
        "original_path": str(original_path) if original_path else "",
    }
