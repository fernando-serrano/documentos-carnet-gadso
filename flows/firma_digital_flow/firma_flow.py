import importlib
import io
import os
import re
import threading
import unicodedata
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from flows.common.utils import guardar_original, ext_desde_mime
from .sheets import read_google_sheet_rows


_thread_local = threading.local()


def _leer_params_firma() -> dict:
    """Parametros del tratamiento de firma, configurables por env (con defaults/clamps)."""
    params = getattr(_thread_local, "firma_params", None)
    if params is not None:
        return params

    def _f(name, d, lo, hi):
        try:
            v = float(str(os.getenv(name, d)).strip())
        except Exception:
            v = float(d)
        return max(lo, min(hi, v))

    def _i(name, d, lo, hi):
        try:
            v = int(str(os.getenv(name, d)).strip())
        except Exception:
            v = int(d)
        return max(lo, min(hi, v))

    def _b(name, d):
        return str(os.getenv(name, d)).strip().lower() in ("1", "true", "si", "sí", "yes", "y", "on")

    def _s(name, d):
        return str(os.getenv(name, d)).strip().lower()

    params = {
        "deskew": _b("FIRMA_DIGITAL_DESKEW", "1"),
        "deskew_min_angle": _f("FIRMA_DIGITAL_DESKEW_MIN_ANGLE", "7", 1.0, 45.0),
        "deskew_max_angle": _f("FIRMA_DIGITAL_DESKEW_MAX_ANGLE", "25", 10.0, 60.0),
        "deskew_min_aspect": _f("FIRMA_DIGITAL_DESKEW_MIN_ASPECT", "1.6", 1.0, 6.0),
        "thicken": _b("FIRMA_DIGITAL_THICKEN", "1"),
        # Intensidad del engrosado de firmas tenues: off|soft|normal. 'soft' = solo cierra
        # micro-cortes sin ensanchar; 'normal' = dilata levemente las muy tenues.
        "thicken_strength": _s("FIRMA_DIGITAL_THICKEN_STRENGTH", "soft"),
        "noise_min_area_ratio": _f("FIRMA_DIGITAL_NOISE_MIN_AREA_RATIO", "0.00006", 0.0, 0.01),
        "cluster_link_pct": _f("FIRMA_DIGITAL_CLUSTER_LINK_PCT", "0.07", 0.02, 0.20),
        "adaptive_constant": _i("FIRMA_DIGITAL_ADAPTIVE_CONSTANT", "10", 2, 30),
        # --- Reconstruccion fiel del trazo (flat-field + relleno de huecos) ---
        # Canal base de tinta: 'red' separa mejor la tinta azul/violeta del papel; 'gray' = luminancia.
        "ink_channel": _s("FIRMA_DIGITAL_INK_CHANNEL", "red"),
        # Correccion de iluminacion (flat-field) para permitir un Otsu global limpio (sin huecos).
        "flatfield": _b("FIRMA_DIGITAL_FLATFIELD", "1"),
        # Divisor del lado menor de la imagen para el kernel del boxFilter (mayor divisor = kernel menor).
        "flatfield_kernel_div": _i("FIRMA_DIGITAL_FLATFIELD_KERNEL_DIV", "7", 3, 30),
        # Sensibilidad del umbral: offset sobre Otsu para captar TINTA TENUE (cierra
        # discontinuidades en el origen). Mayor = mas tinta tenue (mas continuo, mas riesgo de ruido).
        "ink_sensitivity": _i("FIRMA_DIGITAL_INK_SENSITIVITY", "12", 0, 60),
        # Iteraciones de MORPH_CLOSE. 0 (default) = NO fusionar lazos angostos; la continuidad la
        # da el umbral sensible. Subir solo si se necesita puentear gaps grandes (riesgo: fusion).
        "close_iter": _i("FIRMA_DIGITAL_CLOSE_ITER", "0", 0, 4),
        # Tope de area SECUNDARIO de hueco a rellenar (fraccion del AREA DE TINTA).
        "fill_hole_max_ratio": _f("FIRMA_DIGITAL_FILL_HOLE_MAX_RATIO", "0.01", 0.0, 0.30),
        # Discriminante de GROSOR: un hueco se rellena solo si es DELGADO (grosor <= grosor_trazo*factor).
        "fill_thick_factor": _f("FIRMA_DIGITAL_FILL_THICK_FACTOR", "1.3", 0.5, 4.0),
        # Discriminante de ELONGACION: rellenar solo huecos ALARGADOS (estrias: area/grosor^2 >= min).
        # Un lazo redondo tiene elong ~1 -> NUNCA se rellena. Subir = mas conservador.
        "fill_elong_min": _f("FIRMA_DIGITAL_FILL_ELONG_MIN", "3.0", 1.0, 12.0),
        # Lado menor minimo para procesar: si la foto es mas pequena se upscalea (cubica) para que
        # los trazos finos no se fragmenten en la morfologia. 0 = no upscalear.
        "min_process_dim": _i("FIRMA_DIGITAL_MIN_PROCESS_DIM", "600", 0, 2000),
        # Anti-aliasing cosmetico de bordes en el render (suaviza el dentado). 1=on, 0=off.
        "antialias": _b("FIRMA_DIGITAL_ANTIALIAS", "1"),
    }
    _thread_local.firma_params = params
    return params


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


def _parse_marca_temporal(value: str) -> tuple:
    """Convierte la 'Marca temporal' de Google Forms ('D/M/YYYY H:M:S') en una tupla comparable
    (Y, M, D, h, m, s). Si no se puede parsear, devuelve ceros (gana el orden de fila)."""
    s = str(value or "").strip()
    if not s:
        return (0, 0, 0, 0, 0, 0)
    try:
        fecha, _, hora = s.partition(" ")
        d, m, y = (int(x) for x in fecha.split("/")[:3])
        h, mi, se = (int(x) for x in (hora.split(":") + ["0", "0", "0"])[:3])
        return (y, m, d, h, mi, se)
    except Exception:
        return (0, 0, 0, 0, 0, 0)


def cargar_fuente_firma_por_dni(sheet_url: str, logger) -> dict[str, str]:
    rows, fieldnames = read_google_sheet_rows(sheet_url)
    dni_col = _resolver_columna(fieldnames, ["dni"])
    firma_col = _resolver_columna(
        fieldnames,
        [
            "cargar firma digital",
            "firma digital",
            "cargar firma",
            "url 1",
            "url1",
            "link firma digital",
            "link firma",
            "firma",
        ],
    )

    if not dni_col:
        raise RuntimeError("No se encontro columna DNI en hoja base de firma digital")
    if not firma_col:
        raise RuntimeError("No se encontro columna 'Cargar Firma Digital' o equivalente en hoja base")

    ts_col = _resolver_columna(
        fieldnames,
        ["marca temporal", "marca de tiempo", "timestamp", "fecha y hora", "fecha de envio", "fecha"],
    )

    # Si un DNI tiene varias firmas (filas distintas), se queda la de MARCA TEMPORAL mas reciente.
    # Desempate: la fila mas abajo (envio mas nuevo). Asi NO va a revision manual por duplicado.
    mejores: dict[str, tuple] = {}  # dni -> (sort_key, url)
    dnis_multi: set[str] = set()
    for row in rows:
        dni = _normalizar_dni(row.get(dni_col, ""))
        raw_url = str(row.get(firma_col, "") or "").strip()
        if not dni or not raw_url:
            continue
        rn = int(row.get("__row_number__", 0) or 0)
        ts_key = _parse_marca_temporal(row.get(ts_col, "")) if ts_col else (0, 0, 0, 0, 0, 0)
        sort_key = (ts_key, rn)
        actual = mejores.get(dni)
        if actual is None:
            mejores[dni] = (sort_key, raw_url)
        else:
            if actual[1] != raw_url:
                dnis_multi.add(dni)
            if sort_key > actual[0]:
                mejores[dni] = (sort_key, raw_url)

    resultado: dict[str, str] = {dni: v[1] for dni, v in mejores.items()}

    if dnis_multi and hasattr(logger, "warning"):
        logger.warning(
            "[FIRMA DIGITAL] DNIs con multiples firmas | se tomo la mas reciente (Marca temporal) | cantidad=%s | col_ts=%s | ejemplo=%s",
            len(dnis_multi),
            ts_col or "(no encontrada)",
            next(iter(dnis_multi)),
        )

    logger.info(
        "[FIRMA DIGITAL] Fuente cargada | filas=%s | dni_con_firma=%s | col_ts=%s",
        len(rows), len(resultado), ts_col or "(no)",
    )
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
        raise RuntimeError("Drive no devolvio binario para firma digital")
    return bytes(content), mime


def _cargar_cv2_numpy():
    cv2_mod = getattr(_thread_local, "cv2_mod", None)
    np_mod = getattr(_thread_local, "np_mod", None)
    if cv2_mod is not None and np_mod is not None:
        if cv2_mod is False or np_mod is False:
            raise RuntimeError("opencv o numpy no disponibles para procesamiento de firma")
        return cv2_mod, np_mod

    try:
        cv2_mod = importlib.import_module("cv2")
        np_mod = importlib.import_module("numpy")
    except Exception as exc:
        _thread_local.cv2_mod = False
        _thread_local.np_mod = False
        raise RuntimeError(f"No se pudo importar opencv/numpy: {exc}") from exc

    _thread_local.cv2_mod = cv2_mod
    _thread_local.np_mod = np_mod
    return cv2_mod, np_mod


def _abrir_imagen_procesable(content: bytes) -> Image.Image:
    if not content:
        raise RuntimeError("contenido vacio")

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except UnidentifiedImageError as exc:
        raise RuntimeError(f"archivo no es imagen valida: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"no se pudo abrir imagen: {exc}") from exc

    # Respetar la orientacion EXIF del celular: la foto trae grabado como va rotada
    # (p.ej. orientation=6 -> 90 CW). Es la fuente MAS confiable para enderezar fotos de
    # firma tomadas de costado (no hay que adivinar con PCA).
    try:
        image = ImageOps.exif_transpose(image)
    except Exception:
        pass
    return image.convert("RGB")


def _canal_tinta(arr_rgb, gray, np_mod, params):
    """Canal base para separar tinta de papel. El canal ROJO da el mejor contraste
    para tinta azul/violeta (la mas comun en firmas); para tinta negra ~= gris."""
    if str(params.get("ink_channel", "red")) == "gray" or arr_rgb is None:
        return gray
    try:
        # arr_rgb viene en orden RGB (PIL.convert("RGB")) -> canal 0 = rojo.
        canal = np_mod.ascontiguousarray(arr_rgb[:, :, 0])
        return canal
    except Exception:
        return gray


def _generar_mascara_firma(arr_rgb, gray, cv2_mod, np_mod, params=None):
    """Genera la mascara del trazo SIN huecos.

    Antes se usaba ``bitwise_or(Otsu, adaptativo)``: el umbral adaptativo solo detecta
    los BORDES de un trazo mas ancho que su bloque local -> trazo CONTORNEADO (hueco por
    dentro). Ahora: canal de tinta (rojo) -> correccion de iluminacion flat-field -> Otsu
    GLOBAL, que rellena el trazo solido. Basado en tests/manual/firma.py.
    """
    params = params or _leer_params_firma()

    base = _canal_tinta(arr_rgb, gray, np_mod, params)

    if params.get("flatfield", True):
        h, w = base.shape[:2]
        # Kernel impar, proporcional al lado menor: estima la iluminacion del papel
        # (sombras/gradientes) sin "ver" el trazo, para normalizarlo con divide.
        div = int(params.get("flatfield_kernel_div", 7)) or 7
        k = max(31, (min(h, w) // div))
        if k % 2 == 0:
            k += 1
        fondo = cv2_mod.boxFilter(base, -1, (k, k))
        base = cv2_mod.divide(base, fondo, scale=255)
        detalle_ff = f"flatfield_k={k}"
    else:
        base = cv2_mod.GaussianBlur(base, (3, 3), 0)
        detalle_ff = "flatfield_off"

    # Otsu GLOBAL sobre el canal aplanado -> tinta FUERTE (confiable), trazo solido.
    otsu_t, mask = cv2_mod.threshold(base, 0, 255, cv2_mod.THRESH_BINARY_INV + cv2_mod.THRESH_OTSU)

    # Umbral por HISTeRESIS para CONTINUIDAD: el papel aplanado queda ~255 y la tinta mas oscura.
    # Tomamos tinta TENUE (umbral Otsu+offset) pero conservamos SOLO los componentes que tocan
    # tinta fuerte -> extiende/une trazos reales y DESCARTA motas sueltas del papel (sin ruido).
    sens = int(params.get("ink_sensitivity", 22))
    if sens > 0:
        t2 = float(min(245.0, float(otsu_t) + float(sens)))
        _, weak = cv2_mod.threshold(base, t2, 255, cv2_mod.THRESH_BINARY_INV)
        num, lbl, _st, _c = cv2_mod.connectedComponentsWithStats(weak, connectivity=8)
        if num > 1:
            strong_labels = np_mod.unique(lbl[mask > 0])
            keep = np_mod.zeros(num, dtype=bool)
            keep[strong_labels[strong_labels > 0]] = True
            mask = (keep[lbl].astype(np_mod.uint8)) * 255
        detalle_ff = f"{detalle_ff} hyst_sens={sens} t={t2:.0f}"
    fg_ratio = float(np_mod.count_nonzero(mask)) / float(max(1, mask.size))

    return mask, f"mask_fg_ratio={fg_ratio:.4f} {detalle_ff}"


def _rellenar_trazo(mask, cv2_mod, np_mod, params=None):
    """Reconstruccion FIEL del trazo (sin IA generativa):
    1) MORPH_CLOSE puentea trazos intermitentes (gaps pequenos del lapicero).
    2) Rellena SOLO huecos pequenos ENCERRADOS (los pinholes del trazo), dejando los
       lazos legitimos abiertos. El umbral es relativo al AREA DEL TRAZO (no de la foto),
       y una guarda anti-blob revierte el relleno si engordaria demasiado (un lazo grande)."""
    params = params or _leer_params_firma()
    h, w = mask.shape[:2]
    detalles = []

    close_iter = int(params.get("close_iter", 1))
    if close_iter > 0:
        kernel = cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (3, 3))
        mask = cv2_mod.morphologyEx(mask, cv2_mod.MORPH_CLOSE, kernel, iterations=close_iter)
        detalles.append(f"close_iter={close_iter}")

    frac = float(params.get("fill_hole_max_ratio", 0.01))
    factor = float(params.get("fill_thick_factor", 1.3))
    elong_min = float(params.get("fill_elong_min", 3.0))
    fg0 = int(np_mod.count_nonzero(mask))
    if frac > 0 and fg0 > 0:
        # Tope de area SECUNDARIO (auto-escala al tamano de la firma).
        area_thresh = max(8, int(frac * fg0))
        # Grosor del TRAZO: 2x el percentil 75 de la distancia al fondo dentro de la tinta.
        dt_fg = cv2_mod.distanceTransform(mask, cv2_mod.DIST_L2, 3)
        vals = dt_fg[dt_fg > 0]
        w_trazo = 2.0 * float(np_mod.percentile(vals, 75)) if vals.size else 2.0
        max_grosor = max(2.0, w_trazo * factor)
        # Huecos = componentes de FONDO que NO tocan el borde (regiones encerradas por tinta).
        inv = cv2_mod.bitwise_not(mask)
        num, lbl, stats, _c = cv2_mod.connectedComponentsWithStats(inv, connectivity=8)
        # Grosor de cada hueco = 2x la distancia maxima al borde de tinta dentro del hueco.
        dt_hole = cv2_mod.distanceTransform(inv, cv2_mod.DIST_L2, 3)
        relleno = mask.copy()
        llenos = 0
        for i in range(1, num):
            x = int(stats[i, cv2_mod.CC_STAT_LEFT]); y = int(stats[i, cv2_mod.CC_STAT_TOP])
            ww = int(stats[i, cv2_mod.CC_STAT_WIDTH]); hh = int(stats[i, cv2_mod.CC_STAT_HEIGHT])
            area = int(stats[i, cv2_mod.CC_STAT_AREA])
            toca_borde = x <= 0 or y <= 0 or (x + ww) >= w or (y + hh) >= h
            if toca_borde or area > area_thresh:
                continue
            # Grosor del hueco: si supera el del trazo*factor es un LAZO REDONDO -> no rellenar.
            grosor_hueco = 2.0 * float(dt_hole[lbl == i].max())
            if grosor_hueco > max_grosor:
                continue
            # Elongacion (area/grosor^2): una ESTRIA es alargada (>=elong_min); un LAZO redondo ~1.
            elong = float(area) / max(1.0, grosor_hueco * grosor_hueco)
            if elong >= elong_min:
                relleno[lbl == i] = 255
                llenos += area
        # Guarda anti-blob global: si aun asi engorda el trazo > 8%, revertir.
        if llenos > 0 and llenos <= int(0.08 * fg0):
            mask = relleno
            detalles.append(f"holes_filled_px={llenos} w_trazo={w_trazo:.1f} max_grosor={max_grosor:.1f}")
        elif llenos > 0:
            detalles.append(f"fill_revert_blob_guard would_add={llenos}")
        else:
            detalles.append("holes_filled_px=0")

    return mask, " ".join(detalles) if detalles else "fill_none"


def _limpiar_ruido_conservador(mask, cv2_mod, np_mod):
    h, w = mask.shape[:2]
    mask_open = cv2_mod.morphologyEx(
        mask,
        cv2_mod.MORPH_OPEN,
        cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2)),
        iterations=1,
    )

    num_labels, labels, stats, _ = cv2_mod.connectedComponentsWithStats(mask_open, connectivity=8)
    min_area = max(6, int((h * w) * 0.000015))

    cleaned = np_mod.zeros_like(mask_open)
    kept = 0
    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        if area >= min_area or (ww >= 14 and hh >= 2):
            cleaned[labels == label] = 255
            kept += 1

    fg_open = int(np_mod.count_nonzero(mask_open))
    fg_clean = int(np_mod.count_nonzero(cleaned))

    # Priorizamos fidelidad: si limpiar borra demasiado, volvemos a la mascara abierta.
    if fg_clean < max(80, int(fg_open * 0.18)):
        return mask_open, "noise_cleanup_fallback_keep_more_strokes"

    return cleaned, f"noise_cleanup_kept_components={kept}"


def _suprimir_artefactos_de_borde(mask, cv2_mod, np_mod):
    h, w = mask.shape[:2]
    num_labels, labels, stats, _ = cv2_mod.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return mask, "border_artifacts_none"

    cleaned = mask.copy()
    removed = 0
    img_area = float(max(1, h * w))
    min_strip_w = max(3, int(w * 0.02))
    min_strip_h = max(3, int(h * 0.02))

    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        touches_border = x <= 1 or y <= 1 or (x + ww) >= (w - 1) or (y + hh) >= (h - 1)
        if not touches_border:
            continue

        area_ratio = float(area) / img_area
        bbox_area = float(max(1, ww * hh))
        extent = float(area) / bbox_area
        thin_strip = ww <= min_strip_w or hh <= min_strip_h

        if area_ratio > 0.08 or (area_ratio > 0.015 and extent > 0.50) or (thin_strip and area_ratio > 0.003):
            cleaned[labels == label] = 0
            removed += 1

    if removed == 0:
        return mask, "border_artifacts_none"
    return cleaned, f"border_artifacts_removed={removed}"


def _filtrar_cluster_principal_firma(mask, cv2_mod, np_mod):
    h, w = mask.shape[:2]
    num_labels, labels, stats, centroids = cv2_mod.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 2:
        return mask, "cluster_filter_not_needed"

    min_area = max(8, int((h * w) * 0.00001))
    components = []
    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        if area < min_area:
            continue
        cx, cy = centroids[label]
        components.append((label, x, y, ww, hh, area, float(cx), float(cy)))

    if len(components) <= 1:
        return mask, "cluster_filter_not_needed"

    n = len(components)
    parent = list(range(n))

    def _find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _union(a, b):
        ra = _find(a)
        rb = _find(b)
        if ra != rb:
            parent[rb] = ra

    diag = float((w * w + h * h) ** 0.5)
    # Distancia de enlace configurable: mayor (~7%) recupera partes desconectadas legitimas
    # (puntos, cortes de la firma) sin perderlas. La banda de ruido lejano se quita aparte.
    link_pct = float(_leer_params_firma()["cluster_link_pct"])
    link_dist = max(10.0, diag * link_pct)
    margin = max(4.0, float(max(w, h) * 0.015))

    for i in range(n):
        _, x1, y1, w1, h1, _, c1x, c1y = components[i]
        for j in range(i + 1, n):
            _, x2, y2, w2, h2, _, c2x, c2y = components[j]
            dx = c1x - c2x
            dy = c1y - c2y
            dist = float((dx * dx + dy * dy) ** 0.5)

            overlap_with_margin = not (
                x2 > (x1 + w1 + margin)
                or x1 > (x2 + w2 + margin)
                or y2 > (y1 + h1 + margin)
                or y1 > (y2 + h2 + margin)
            )

            if dist <= link_dist or overlap_with_margin:
                _union(i, j)

    clusters = {}
    for idx, comp in enumerate(components):
        root = _find(idx)
        clusters.setdefault(root, []).append(comp)

    best_root = None
    best_score = -1.0
    for root, comps in clusters.items():
        area_sum = sum(c[5] for c in comps)
        xs = [c[1] for c in comps]
        ys = [c[2] for c in comps]
        x2s = [c[1] + c[3] for c in comps]
        y2s = [c[2] + c[4] for c in comps]
        bx0, by0, bx1, by1 = min(xs), min(ys), max(x2s), max(y2s)
        bw = max(1, bx1 - bx0)
        bh = max(1, by1 - by0)

        width_ratio = float(bw) / float(max(1, w))
        height_ratio = float(bh) / float(max(1, h))
        touches_bottom = by1 >= (h - 2)
        touches_top = by0 <= 1

        border_penalty = 0.0
        if touches_bottom and width_ratio > 0.45 and height_ratio < 0.25:
            border_penalty += 0.65
        if touches_top and width_ratio > 0.45 and height_ratio < 0.25:
            border_penalty += 0.45

        score = float(area_sum) * max(0.05, 1.0 - border_penalty)
        if score > best_score:
            best_score = score
            best_root = root

    if best_root is None:
        return mask, "cluster_filter_not_needed"

    keep_labels = {c[0] for c in clusters[best_root]}
    xs = [c[1] for c in clusters[best_root]]
    ys = [c[2] for c in clusters[best_root]]
    x2s = [c[1] + c[3] for c in clusters[best_root]]
    y2s = [c[2] + c[4] for c in clusters[best_root]]
    bx0, by0, bx1, by1 = min(xs), min(ys), max(x2s), max(y2s)

    # Incluye satelites cercanos de area relevante (puntos/cortes validos de la firma).
    near_dist = max(20.0, diag * 0.07)
    for label, x, y, ww, hh, area, cx, cy in components:
        if label in keep_labels:
            continue
        if area < max(14, int(sum(c[5] for c in clusters[best_root]) * 0.04)):
            continue

        dx = 0.0
        if cx < bx0:
            dx = bx0 - cx
        elif cx > bx1:
            dx = cx - bx1

        dy = 0.0
        if cy < by0:
            dy = by0 - cy
        elif cy > by1:
            dy = cy - by1

        dist_to_cluster = float((dx * dx + dy * dy) ** 0.5)
        if dist_to_cluster <= near_dist:
            keep_labels.add(label)

    cleaned = np_mod.zeros_like(mask)
    for label in keep_labels:
        cleaned[labels == label] = 255

    removed = len(components) - len(keep_labels)
    if removed <= 0:
        return mask, "cluster_filter_not_needed"
    return cleaned, f"cluster_filter_kept={len(keep_labels)} removed={removed}"


def _quitar_motas_lejanas(mask, cv2_mod, np_mod, params):
    """Elimina motas pequeñas y AISLADAS (bandas de ruido del papel) lejos del trazo
    principal. Conserva el componente mayor (firma) y todo lo cercano/elongado."""
    h, w = mask.shape[:2]
    num, labels, stats, _cent = cv2_mod.connectedComponentsWithStats(mask, connectivity=8)
    if num <= 2:
        return mask, "motas_none"

    areas = stats[1:, cv2_mod.CC_STAT_AREA]
    main = 1 + int(np_mod.argmax(areas))
    mx0 = int(stats[main, cv2_mod.CC_STAT_LEFT])
    my0 = int(stats[main, cv2_mod.CC_STAT_TOP])
    mx1 = mx0 + int(stats[main, cv2_mod.CC_STAT_WIDTH])
    my1 = my0 + int(stats[main, cv2_mod.CC_STAT_HEIGHT])

    diag = float((w * w + h * h) ** 0.5)
    noise_area = max(8, int(float(params["noise_min_area_ratio"]) * h * w))
    margin = diag * 0.04

    out = mask.copy()
    removed = 0
    for i in range(1, num):
        if i == main:
            continue
        x = int(stats[i, cv2_mod.CC_STAT_LEFT])
        y = int(stats[i, cv2_mod.CC_STAT_TOP])
        ww = int(stats[i, cv2_mod.CC_STAT_WIDTH])
        hh = int(stats[i, cv2_mod.CC_STAT_HEIGHT])
        area = int(stats[i, cv2_mod.CC_STAT_AREA])
        # distancia del bbox del componente al bbox del trazo principal
        dx = max(0, mx0 - (x + ww), x - mx1)
        dy = max(0, my0 - (y + hh), y - my1)
        dist = float((dx * dx + dy * dy) ** 0.5)
        # mota = pequeña Y lejos del trazo principal -> ruido del papel
        if area < noise_area and dist > margin:
            out[labels == i] = 0
            removed += 1

    if removed == 0:
        return mask, "motas_none"
    return out, f"motas_removidas={removed}"


def _enderezar_firma(gray, mask, cv2_mod, np_mod, params):
    """Deskew con guardas: endereza a horizontal SOLO si la firma es claramente elongada
    y el angulo es significativo. Devuelve (gray, mask, detalle, roto, incierto)."""
    if not params.get("deskew", True):
        return gray, mask, "deskew_off", False, False

    ys, xs = np_mod.where(mask > 0)
    if xs.size < 60:
        return gray, mask, "deskew_skip_pocos_puntos", False, False

    # Orientacion actual: una firma normal es apaisada (mas ancha que alta). Si el bbox de la
    # tinta es claramente VERTICAL (portrait), la firma esta rotada ~90 -> hay que enderezar.
    bw0 = int(xs.max() - xs.min() + 1)
    bh0 = int(ys.max() - ys.min() + 1)
    portrait = bh0 > bw0 * 1.15

    pts = np_mod.column_stack([xs.astype(np_mod.float32), ys.astype(np_mod.float32)])
    try:
        _mean, eigvecs, eigvals = cv2_mod.PCACompute2(pts, mean=None)
    except Exception as exc:
        return gray, mask, f"deskew_skip_pca_error={exc}", False, False

    evs = np_mod.asarray(eigvals).ravel()
    ev0 = float(evs[0]); ev1 = float(evs[1])
    elong = float((ev0 / max(1e-6, ev1)) ** 0.5)
    vx = float(eigvecs[0][0]); vy = float(eigvecs[0][1])
    ang = float(np_mod.degrees(np_mod.arctan2(vy, vx)))
    while ang > 90.0:
        ang -= 180.0
    while ang <= -90.0:
        ang += 180.0
    rot = -ang  # rotar para llevar el eje principal a horizontal

    # MODO CONSERVADOR. Solo se corrige la INCLINACION LEVE de firmas apaisadas, donde el
    # angulo de PCA es confiable (datos: rot<=~25). Una firma freeform rotada ~90 (portrait)
    # no se puede enderezar con confianza -> REVISAR MANUAL. Apaisada con angulo grande (PCA
    # poco fiable) -> se deja como esta (no arriesgar).
    # La orientacion REAL ya la resolvio EXIF al abrir. Aqui solo corregimos inclinacion LEVE
    # confiable (slight skew). NO se manda a revision por orientacion: si no se puede enderezar
    # con confianza, se PROCESA como esta (intentar tratar, no descartar). Un angulo grande con
    # EXIF normal suele ser el slant natural de la firma, no un error.
    max_ang = float(params["deskew_max_angle"])
    if abs(rot) < float(params["deskew_min_angle"]):
        return gray, mask, f"deskew_skip_recta ang={rot:.1f}", False, False
    if portrait or abs(rot) > max_ang or elong < float(params["deskew_min_aspect"]):
        # No es una correccion de inclinacion leve confiable -> se deja como esta y se procesa.
        return gray, mask, f"deskew_skip_no_confiable elong={elong:.2f} rot={rot:.0f} portrait={portrait}", False, False

    h, w = gray.shape[:2]
    cx, cy = w / 2.0, h / 2.0
    M = cv2_mod.getRotationMatrix2D((cx, cy), rot, 1.0)
    cos = abs(float(M[0, 0])); sin = abs(float(M[0, 1]))
    nw = int(h * sin + w * cos)
    nh = int(h * cos + w * sin)
    M[0, 2] += nw / 2.0 - cx
    M[1, 2] += nh / 2.0 - cy
    gray_r = cv2_mod.warpAffine(gray, M, (nw, nh), flags=cv2_mod.INTER_LINEAR, borderValue=255)
    mask_r = cv2_mod.warpAffine(mask, M, (nw, nh), flags=cv2_mod.INTER_NEAREST, borderValue=0)

    # Sanity ESTRICTO: tras enderezar, la firma debe quedar CLARAMENTE apaisada (linea base
    # horizontal). Si no (p.ej. quedo en diagonal), el angulo no era confiable -> REVISAR MANUAL.
    ys2, xs2 = np_mod.where(mask_r > 0)
    if xs2.size == 0:
        return gray, mask, "deskew_incierto_vacio", False, True
    bw = int(xs2.max() - xs2.min() + 1)
    bh = int(ys2.max() - ys2.min() + 1)
    # Si rotar empeoro (quedo mas alto que ancho), revertir y PROCESAR como estaba (no revision).
    if bw < bh * 0.7:
        return gray, mask, f"deskew_revert bw={bw} bh={bh}", False, False

    return gray_r, mask_r, f"deskew_aplicado ang={rot:.1f} elong={elong:.2f}", True, False


def _detectar_no_firma_morfologica(mask, cv2_mod, np_mod):
    h, w = mask.shape[:2]
    img_area = float(max(1, h * w))
    fg = int(np_mod.count_nonzero(mask))
    fg_ratio = float(fg) / img_area

    if fg_ratio > 0.45:
        return True, f"not_signature_detected fg_ratio_high={fg_ratio:.4f}"

    num_labels, _, stats, _ = cv2_mod.connectedComponentsWithStats(mask, connectivity=8)
    if num_labels <= 1:
        return False, "not_signature_check_empty"

    largest_area = 0
    largest_extent = 0.0
    largest_ratio = 0.0
    largest_cov = 0.0
    largest_aspect = 0.0
    dense_big = 0
    component_count = max(0, num_labels - 1)

    for label in range(1, num_labels):
        x, y, ww, hh, area = stats[label]
        ratio = float(area) / img_area
        bbox_area = float(max(1, ww * hh))
        extent = float(area) / bbox_area
        cov = (float(ww) / float(max(1, w))) * (float(hh) / float(max(1, h)))

        if ratio > 0.015 and extent > 0.58:
            dense_big += 1

        if area > largest_area:
            largest_area = int(area)
            largest_extent = extent
            largest_ratio = ratio
            largest_cov = cov
            largest_aspect = float(ww) / float(max(1, hh))

    if largest_ratio > 0.18 and largest_extent > 0.58 and largest_cov > 0.25:
        return (
            True,
            (
                "not_signature_detected dense_blob "
                f"largest_ratio={largest_ratio:.4f} extent={largest_extent:.4f} cov={largest_cov:.4f}"
            ),
        )

    if dense_big >= 2 and fg_ratio > 0.16:
        return True, f"not_signature_detected dense_regions={dense_big} fg_ratio={fg_ratio:.4f}"

    if component_count >= 25 and largest_ratio < 0.02:
        ys, xs = np_mod.where(mask > 0)
        if len(xs) > 0 and len(ys) > 0:
            x0 = int(xs.min())
            x1 = int(xs.max())
            y0 = int(ys.min())
            y1 = int(ys.max())
            bw = max(1, x1 - x0 + 1)
            bh = max(1, y1 - y0 + 1)
            width_ratio = float(bw) / float(max(1, w))
            height_ratio = float(bh) / float(max(1, h))
            box_cov = width_ratio * height_ratio
            box_fill = float(fg) / float(max(1, bw * bh))

            row_counts = np_mod.count_nonzero(mask, axis=1)
            top_cut = max(1, int(h * 0.2))
            bot_cut = max(1, int(h * 0.8))
            top_mass = float(row_counts[:top_cut].sum()) / float(max(1, fg))
            bot_mass = float(row_counts[bot_cut:].sum()) / float(max(1, fg))

            if (
                component_count >= 60
                and fg_ratio < 0.06
                and width_ratio > 0.5
                and height_ratio < 0.4
                and (top_mass > 0.85 or bot_mass > 0.85)
            ):
                return (
                    True,
                    (
                        "not_signature_detected horizontal_noise_band "
                        f"components={component_count} top_mass={top_mass:.4f} "
                        f"bot_mass={bot_mass:.4f} box_fill={box_fill:.4f}"
                    ),
                )

            if width_ratio > 0.75 and height_ratio > 0.45 and box_fill < 0.03:
                return (
                    True,
                    (
                        "not_signature_detected sparse_perimeter_pattern "
                        f"components={component_count} box_fill={box_fill:.4f} cov={box_cov:.4f}"
                    ),
                )

            if box_cov > 0.40 and box_fill < 0.02:
                return (
                    True,
                    (
                        "not_signature_detected sparse_scatter_pattern "
                        f"components={component_count} box_fill={box_fill:.4f} cov={box_cov:.4f}"
                    ),
                )

            if component_count >= 50 and width_ratio > 0.75 and height_ratio > 0.18 and box_fill < 0.015:
                return (
                    True,
                    (
                        "not_signature_detected sparse_arc_pattern "
                        f"components={component_count} box_fill={box_fill:.4f} cov={box_cov:.4f}"
                    ),
                )

    if 0.55 <= largest_aspect <= 1.45 and largest_ratio > 0.22 and largest_extent > 0.52:
        return (
            True,
            (
                "not_signature_detected portrait_like_blob "
                f"largest_ratio={largest_ratio:.4f} aspect={largest_aspect:.4f}"
            ),
        )

    return False, "not_signature_check_ok"


def _mascara_por_tinta_color(arr_rgb, gray, cv2_mod, np_mod):
    """
    Mascara alternativa para firmas en lapicero azul/oscuro.
    Se usa como rescate cuando el umbral general detecta demasiado fondo.
    """
    hsv = cv2_mod.cvtColor(arr_rgb, cv2_mod.COLOR_RGB2HSV)

    # Azules/violetas comunes en firmas.
    blue_ink = cv2_mod.inRange(hsv, (85, 20, 20), (150, 255, 255))

    # Tinta oscura con saturacion moderada.
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    dark_colored = ((sat > 35) & (val < 185)).astype(np_mod.uint8) * 255

    # Refuerzo para tinta negra/azul oscura.
    dark_gray = cv2_mod.inRange(gray, 0, 120)

    mask = cv2_mod.bitwise_or(blue_ink, dark_colored)
    mask = cv2_mod.bitwise_or(mask, dark_gray)
    mask = cv2_mod.morphologyEx(
        mask,
        cv2_mod.MORPH_OPEN,
        cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2)),
        iterations=1,
    )
    return mask


def _engrosar_si_tenue(mask, cv2_mod, np_mod, params=None):
    params = params or _leer_params_firma()
    if not params.get("thicken", True):
        return mask, "stroke_thickness_disabled", False

    strength = str(params.get("thicken_strength", "soft")).lower()
    if strength == "off":
        return mask, "stroke_thickness_off", False

    h, w = mask.shape[:2]
    fg_ratio = float(np_mod.count_nonzero(mask)) / float(max(1, h * w))
    kernel = cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2))
    # Guarda de sobre-crecimiento mas estricta (sutil): 1.04 en vez de 1.08.
    overgrow = 1.04

    def _aplicar(out, etiqueta):
        fg_before = float(np_mod.count_nonzero(mask))
        fg_after = float(np_mod.count_nonzero(out))
        if fg_before > 0 and (fg_after / fg_before) > overgrow:
            return mask, "stroke_thickness_kept_overgrow_guard", False
        return out, etiqueta, True

    # 'normal': dilata levemente las MUY tenues; 'soft' (default): nunca dilata, solo cierra.
    if fg_ratio < 0.0012 and strength == "normal":
        return _aplicar(cv2_mod.dilate(mask, kernel, iterations=1), "stroke_thickened_soft")

    # Cierre de micro-cortes (net-neutro en ancho del trazo) para firmas tenues.
    if fg_ratio < 0.0030:
        return _aplicar(cv2_mod.morphologyEx(mask, cv2_mod.MORPH_CLOSE, kernel, iterations=1), "stroke_thickened_micro")

    return mask, "stroke_thickness_kept", False


def _recortar_firma(gray, mask, cv2_mod, np_mod):
    ys, xs = np_mod.where(mask > 0)
    if len(xs) == 0 or len(ys) == 0:
        raise RuntimeError("no se detectaron trazos de firma")

    h, w = mask.shape[:2]
    x0 = int(xs.min())
    x1 = int(xs.max())
    y0 = int(ys.min())
    y1 = int(ys.max())

    bw = max(1, x1 - x0 + 1)
    bh = max(1, y1 - y0 + 1)
    if bw < 20 or bh < 8:
        raise RuntimeError("firma demasiado pequena para procesar")

    pad_x = max(8, int(w * 0.03))
    pad_y = max(6, int(h * 0.04))

    rx0 = max(0, x0 - pad_x)
    rx1 = min(w - 1, x1 + pad_x)
    ry0 = max(0, y0 - pad_y)
    ry1 = min(h - 1, y1 + pad_y)

    gray_crop = gray[ry0 : ry1 + 1, rx0 : rx1 + 1]
    mask_crop = mask[ry0 : ry1 + 1, rx0 : rx1 + 1]

    return gray_crop, mask_crop


def _render_firma_en_fondo_claro(gray_crop, mask_crop, cv2_mod, np_mod, params=None):
    params = params or _leer_params_firma()
    # Quita motas de un pixel que suelen aparecer como ruido suelto.
    num_labels, labels, stats, _ = cv2_mod.connectedComponentsWithStats(mask_crop, connectivity=8)
    mask_render = mask_crop.copy()
    if num_labels > 1:
        for label in range(1, num_labels):
            area = int(stats[label, cv2_mod.CC_STAT_AREA])
            if area <= 1:
                mask_render[labels == label] = 0

    fg_count = int(np_mod.count_nonzero(mask_render))
    fg_ratio = float(fg_count) / float(max(1, mask_render.size))

    # Adelgazado ligero y seguro solo cuando la firma queda densa.
    if fg_ratio > 0.055 and fg_count > 300:
        thin = cv2_mod.erode(mask_render, cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2)), iterations=1)
        thin_count = int(np_mod.count_nonzero(thin))
        keep_ratio = float(thin_count) / float(max(1, fg_count))
        if 0.82 <= keep_ratio <= 0.98:
            mask_render = thin

    # Trazo sobre fondo blanco. Con anti-aliasing: el borde de la mascara se difumina y se compone
    # tinta(26) sobre blanco(255) via alpha -> contorno suave (sin dentado), sin inventar tinta.
    ink = 26
    if params.get("antialias", True):
        alpha = cv2_mod.GaussianBlur(mask_render, (3, 3), 0).astype(np_mod.float32) / 255.0
        out = (255.0 - alpha * (255.0 - ink)).clip(0, 255).astype(np_mod.uint8)
    else:
        out = np_mod.full(gray_crop.shape, 255, dtype=np_mod.uint8)
        out[mask_render > 0] = ink

    frame = max(8, int(max(out.shape[0], out.shape[1]) * 0.05))
    out = cv2_mod.copyMakeBorder(out, frame, frame, frame, frame, cv2_mod.BORDER_CONSTANT, value=255)

    return Image.fromarray(out, mode="L")


def _detectar_firma_fragmentada(mask_crop, cv2_mod, np_mod):
    h, w = mask_crop.shape[:2]
    area = float(max(1, h * w))
    fg = int(np_mod.count_nonzero(mask_crop))
    fg_ratio = float(fg) / area

    num_labels, _, stats, _ = cv2_mod.connectedComponentsWithStats(mask_crop, connectivity=8)
    comps = max(0, num_labels - 1)
    if comps < 15:
        return False, "fragmentation_ok"

    largest = 0
    for label in range(1, num_labels):
        _, _, _, _, c_area = stats[label]
        if c_area > largest:
            largest = int(c_area)

    largest_ratio = float(largest) / area

    if (comps >= 25 and fg_ratio < 0.04 and largest_ratio < 0.012) or (
        comps >= 15 and fg_ratio < 0.03 and largest_ratio < 0.008
    ):
        return (
            True,
            (
                "fragmented_signature_pattern "
                f"components={comps} fg_ratio={fg_ratio:.4f} largest_ratio={largest_ratio:.4f}"
            ),
        )

    return False, "fragmentation_ok"


def _intentar_rescate_alt(arr_rgb, gray, base_detail: str, cv2_mod, np_mod, params=None):
    params = params or _leer_params_firma()
    # Recomputar gray del original para asegurar orientacion no-rotada (alinea con arr_rgb).
    gray = cv2_mod.cvtColor(arr_rgb, cv2_mod.COLOR_RGB2GRAY)

    mask_alt = _mascara_por_tinta_color(arr_rgb, gray, cv2_mod, np_mod)
    alt_ratio = float(np_mod.count_nonzero(mask_alt)) / float(max(1, mask_alt.size))
    if alt_ratio < 0.0005:
        return False, None, f"{base_detail} alt_ink_rescue_too_faint alt_ratio={alt_ratio:.4f}", False

    mask_alt, alt_clean_detail = _limpiar_ruido_conservador(mask_alt, cv2_mod, np_mod)
    mask_alt, alt_border_detail = _suprimir_artefactos_de_borde(mask_alt, cv2_mod, np_mod)
    mask_alt, alt_cluster_detail = _filtrar_cluster_principal_firma(mask_alt, cv2_mod, np_mod)
    # Mismas mejoras que el path principal: quitar motas de ruido lejanas + reconstruir trazo.
    mask_alt, alt_motas_detail = _quitar_motas_lejanas(mask_alt, cv2_mod, np_mod, params)
    mask_alt, alt_fill_detail = _rellenar_trazo(mask_alt, cv2_mod, np_mod, params)
    alt_cluster_detail = f"{alt_cluster_detail} {alt_motas_detail} {alt_fill_detail}"

    non_signature_alt, non_sig_alt_detail = _detectar_no_firma_morfologica(mask_alt, cv2_mod, np_mod)
    if non_signature_alt:
        return (
            False,
            None,
            (
                f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
                f"{alt_clean_detail} {alt_border_detail} {alt_cluster_detail} {non_sig_alt_detail}"
            ),
            False,
        )

    # Deskew tambien en el rescate; si queda incierto -> caller manda a REVISAR MANUAL.
    gray, mask_alt, alt_deskew_detail, _alt_roto, alt_deskew_incierto = _enderezar_firma(
        gray, mask_alt, cv2_mod, np_mod, params
    )
    if alt_deskew_incierto:
        return (
            False,
            None,
            f"{base_detail} alt_ink_rescue {alt_deskew_detail} deskew_revision_manual",
            False,
        )

    mask_alt_final, alt_thick_detail, alt_thickened = _engrosar_si_tenue(mask_alt, cv2_mod, np_mod, params)

    try:
        gray_crop_alt, mask_crop_alt = _recortar_firma(gray, mask_alt_final, cv2_mod, np_mod)
    except Exception as exc:
        return (
            False,
            None,
            (
                f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
                f"crop_error={exc}"
            ),
            alt_thickened,
        )

    fg_crop_alt = int(np_mod.count_nonzero(mask_crop_alt))
    crop_ratio_alt = float(fg_crop_alt) / float(max(1, mask_crop_alt.size))
    if fg_crop_alt < 90 or crop_ratio_alt < 0.0010 or crop_ratio_alt > 0.60:
        return (
            False,
            None,
            (
                f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
                f"invalid_crop_ratio={crop_ratio_alt:.4f}"
            ),
            alt_thickened,
        )

    fragmented_alt, frag_alt_detail = _detectar_firma_fragmentada(mask_crop_alt, cv2_mod, np_mod)
    if fragmented_alt:
        return (
            False,
            None,
            (
                f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
                f"{frag_alt_detail}"
            ),
            alt_thickened,
        )

    # Blob denso (sombra/contaminacion) tambien en el rescate -> falla -> caller a revision.
    ys_b, xs_b = np_mod.where(mask_crop_alt > 0)
    if xs_b.size > 0:
        bw_b = int(xs_b.max() - xs_b.min() + 1)
        bh_b = int(ys_b.max() - ys_b.min() + 1)
        densidad_alt = float(xs_b.size) / float(max(1, bw_b * bh_b))
        if densidad_alt > 0.42:
            return (
                False,
                None,
                f"{base_detail} alt_ink_rescue blob_denso densidad={densidad_alt:.2f}",
                False,
            )

    out_alt = _render_firma_en_fondo_claro(gray_crop_alt, mask_crop_alt, cv2_mod, np_mod, params)
    detail_alt = (
        f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
        f"{alt_clean_detail} {alt_border_detail} {alt_cluster_detail} "
        f"{alt_deskew_detail} {alt_thick_detail} crop_ratio={crop_ratio_alt:.4f}"
    )
    return True, out_alt, detail_alt.strip(), alt_thickened


def _procesar_firma_imagen(image: Image.Image) -> tuple[Image.Image, str, bool, bool]:
    """
    Retorna (imagen_procesada, detalle, revision_manual, trazo_engrosado).
    """
    cv2_mod, np_mod = _cargar_cv2_numpy()
    params = _leer_params_firma()

    arr_rgb = np_mod.array(image.convert("RGB"))

    # Upscale de fotos pequenas (miniaturas): a baja resolucion el trazo fino ocupa ~1px y la
    # morfologia (OPEN 2x2) lo fragmenta en "guiones". Interpolar (cubica) reconstruye el trazo
    # continuo que la foto ya contiene y lo engrosa lo suficiente para sobrevivir. No inventa tinta.
    upscale_detail = ""
    min_dim_cfg = int(params.get("min_process_dim", 600))
    h0, w0 = arr_rgb.shape[:2]
    if min_dim_cfg > 0 and min(h0, w0) < min_dim_cfg:
        scale = min(6.0, float(min_dim_cfg) / float(max(1, min(h0, w0))))
        if scale > 1.01:
            arr_rgb = cv2_mod.resize(arr_rgb, None, fx=scale, fy=scale, interpolation=cv2_mod.INTER_CUBIC)
            upscale_detail = f"upscaled_x{scale:.1f}"

    gray = cv2_mod.cvtColor(arr_rgb, cv2_mod.COLOR_RGB2GRAY)

    mask_raw, mask_detail = _generar_mascara_firma(arr_rgb, gray, cv2_mod, np_mod, params)
    if upscale_detail:
        mask_detail = f"{upscale_detail} {mask_detail}"
    raw_fg = int(np_mod.count_nonzero(mask_raw))
    raw_ratio = float(raw_fg) / float(max(1, mask_raw.size))

    if raw_fg < 80 or raw_ratio < 0.0005:
        return image.convert("L"), f"{mask_detail} signature_too_faint", True, False

    if raw_ratio > 0.55:
        mask_detail = f"{mask_detail} high_fg_ratio={raw_ratio:.4f}"

    mask_clean, clean_detail = _limpiar_ruido_conservador(mask_raw, cv2_mod, np_mod)
    mask_border, border_detail = _suprimir_artefactos_de_borde(mask_clean, cv2_mod, np_mod)
    mask_cluster, cluster_detail = _filtrar_cluster_principal_firma(mask_border, cv2_mod, np_mod)
    mask_cluster, motas_detail = _quitar_motas_lejanas(mask_cluster, cv2_mod, np_mod, params)
    # Reconstruccion fiel: puentear gaps + rellenar huecos pequenos del contorneado.
    mask_cluster, fill_detail = _rellenar_trazo(mask_cluster, cv2_mod, np_mod, params)
    cluster_detail = f"{cluster_detail} {motas_detail} {fill_detail}"

    clean_fg = int(np_mod.count_nonzero(mask_cluster))

    # En fondos muy sucios, exigir 12% de retencion produce falsos REVISAR MANUAL.
    min_keep_ratio = 0.12
    if raw_ratio > 0.45:
        min_keep_ratio = 0.005
    elif raw_ratio > 0.35:
        min_keep_ratio = 0.015
    elif raw_ratio > 0.25:
        min_keep_ratio = 0.05

    min_keep_fg = max(80, int(raw_fg * min_keep_ratio))

    main_base_detail = f"{mask_detail} {clean_detail} {border_detail} {cluster_detail}"
    alt_rescue_cache = None

    def _try_alt_rescue():
        nonlocal alt_rescue_cache
        if alt_rescue_cache is None:
            alt_rescue_cache = _intentar_rescate_alt(arr_rgb, gray, main_base_detail, cv2_mod, np_mod, params)
        return alt_rescue_cache

    collapse_ratio = float(clean_fg) / float(max(1, raw_fg))
    if raw_ratio > 0.35 and clean_fg < 2000 and collapse_ratio < 0.01:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        return (
            image.convert("L"),
            (
                f"{main_base_detail} "
                f"not_signature_detected collapse_after_filter "
                f"raw_fg={raw_fg} clean_fg={clean_fg} collapse_ratio={collapse_ratio:.4f}"
            ),
            True,
            False,
        )

    if clean_fg < min_keep_fg:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        # Para evitar falsos positivos por perdida de trazo, pasamos a revision manual.
        return (
            image.convert("L"),
            (
                f"{main_base_detail} "
                f"lost_too_much_stroke raw_fg={raw_fg} clean_fg={clean_fg} min_keep={min_keep_fg}"
            ),
            True,
            False,
        )

    non_signature, non_sig_detail = _detectar_no_firma_morfologica(mask_cluster, cv2_mod, np_mod)
    if non_signature:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        return (
            image.convert("L"),
            f"{main_base_detail} {non_sig_detail}",
            True,
            False,
        )

    # Deskew (enderezado) con guardas, sobre el trazo limpio. Si intento enderezar pero
    # el resultado quedo dudoso -> REVISAR MANUAL (no subir una firma mal-rotada).
    gray, mask_cluster, deskew_detail, _deskew_roto, deskew_incierto = _enderezar_firma(
        gray, mask_cluster, cv2_mod, np_mod, params
    )
    if deskew_incierto:
        return (
            image.convert("L"),
            f"{main_base_detail} {deskew_detail} deskew_revision_manual",
            True,
            False,
        )

    mask_final, thick_detail, thickened = _engrosar_si_tenue(mask_cluster, cv2_mod, np_mod, params)

    try:
        gray_crop, mask_crop = _recortar_firma(gray, mask_final, cv2_mod, np_mod)
    except Exception as exc:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        return image.convert("L"), f"{mask_detail} {clean_detail} crop_error={exc}", True, thickened

    fg_crop = int(np_mod.count_nonzero(mask_crop))
    crop_ratio = float(fg_crop) / float(max(1, mask_crop.size))
    if fg_crop < 90 or crop_ratio < 0.0010:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        return image.convert("L"), f"{mask_detail} {clean_detail} crop_signature_too_small", True, thickened

    fragmented, frag_detail = _detectar_firma_fragmentada(mask_crop, cv2_mod, np_mod)
    if fragmented:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick
        return (
            image.convert("L"),
            f"{main_base_detail} {frag_detail}",
            True,
            thickened,
        )

    # Blob DENSO (sombra/contaminacion, NO firma): una firma son trazos finos -> densidad baja
    # dentro de su bbox; un blob solido -> densidad alta. Si es solido -> REVISAR MANUAL.
    ys_b, xs_b = np_mod.where(mask_crop > 0)
    if xs_b.size > 0:
        bw_b = int(xs_b.max() - xs_b.min() + 1)
        bh_b = int(ys_b.max() - ys_b.min() + 1)
        densidad = float(xs_b.size) / float(max(1, bw_b * bh_b))
        if densidad > 0.42:
            rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
            if rescue_ok:
                return rescue_img, rescue_detail, False, rescue_thick
            return image.convert("L"), f"{main_base_detail} blob_denso densidad={densidad:.2f}", True, thickened

    # Salvaguarda anti-falso-positivo: si casi todo el recorte es "trazo",
    # intentamos una segunda pasada por tinta antes de mandar a revision manual.
    if crop_ratio > 0.52:
        rescue_ok, rescue_img, rescue_detail, rescue_thick = _try_alt_rescue()
        if rescue_ok:
            return rescue_img, rescue_detail, False, rescue_thick

        return (
            image.convert("L"),
            (
                f"{main_base_detail} "
                f"crop_ratio_too_high={crop_ratio:.4f}"
            ),
            True,
            thickened,
        )

    out = _render_firma_en_fondo_claro(gray_crop, mask_crop, cv2_mod, np_mod, params)
    detail = (
        f"{mask_detail} {clean_detail} {border_detail} {cluster_detail} "
        f"{deskew_detail} {thick_detail} crop_ratio={crop_ratio:.4f}"
    )
    return out, detail.strip(), False, thickened


def _png_menor_a_limite(image: Image.Image, target_bytes: int) -> tuple[bytes, str, bool]:
    if target_bytes <= 0:
        raise RuntimeError("Limite de bytes invalido para firma digital")

    img = image.convert("L")
    min_v, max_v = img.getextrema()
    nonzero_bins = sum(1 for c in img.histogram() if c)
    # En firmas casi binarias priorizamos nitidez para evitar bordes grises.
    near_binary = (nonzero_bins <= 10) and (min_v <= 64) and (max_v >= 240)

    base_w, base_h = img.size
    best_data = b""
    best_size = 10**18
    best_detail = ""

    for scale in (1.0, 0.9, 0.82, 0.74, 0.66, 0.58):
        w = max(120, int(base_w * scale))
        h = max(50, int(base_h * scale))
        resample = Image.NEAREST if near_binary else Image.BICUBIC
        resized = img.resize((w, h), resample)
        # Blanquea fondo cercano a blanco para que no se vea neblina/gris.
        resized = resized.point(lambda p: 255 if p >= 248 else p)

        bw_candidate = resized.point(lambda p: 0 if p < 176 else 255, mode="1").convert("L")
        candidates = [("bw", bw_candidate), ("gray", resized)] if near_binary else [("gray", resized), ("bw", bw_candidate)]

        for mode_name, candidate in candidates:
            buffer = io.BytesIO()
            candidate.save(buffer, format="PNG", optimize=True, compress_level=9)
            data = buffer.getvalue()
            size = len(data)

            if size < best_size:
                best_size = size
                best_data = data
                best_detail = f"png_best mode={mode_name} scale={scale:.2f} size={size}"

            if size <= target_bytes:
                return data, f"png_ok mode={mode_name} scale={scale:.2f} size={size}", True

    return best_data, f"{best_detail} png_above_limit", False


def _guardar_firma_local(
    lote_dir: Path,
    dni: str,
    contenido_png: bytes,
    overwrite_existing: bool,
    keep_temp_files: bool,
) -> tuple[Path, Path | None, bool]:
    destino_dir = lote_dir / dni
    destino_dir.mkdir(parents=True, exist_ok=True)

    final_path = destino_dir / f"firma_digital_{dni}.png"
    temp_path = destino_dir / f"firma_digital_{dni}_tmp.png"

    if final_path.exists() and not overwrite_existing:
        return final_path, None, True

    if final_path.exists() and overwrite_existing:
        final_path.unlink()

    if keep_temp_files:
        temp_path.write_bytes(contenido_png)
    else:
        temp_path = None

    final_path.write_bytes(contenido_png)

    if not final_path.exists() or final_path.stat().st_size <= 0:
        raise RuntimeError("firma digital local quedo vacia tras guardar")

    if not keep_temp_files and temp_path is not None and temp_path.exists():
        temp_path.unlink()
        temp_path = None

    return final_path, temp_path, False


def _resolver_uploader(upload_callable: str):
    spec = str(upload_callable or "").strip()
    if not spec:
        return None

    if ":" not in spec:
        raise RuntimeError("FIRMA_DIGITAL_UPLOAD_CALLABLE debe tener formato modulo:funcion")

    module_name, func_name = spec.split(":", 1)
    module = importlib.import_module(module_name.strip())
    func = getattr(module, func_name.strip(), None)
    if func is None or not callable(func):
        raise RuntimeError(f"No se encontro callable valido en {spec}")
    return func


def _cargar_firma_a_expediente(
    dni: str,
    firma_path: Path,
    upload_callable: str,
) -> tuple[bool, str]:
    uploader = _resolver_uploader(upload_callable)
    if uploader is None:
        return False, "upload_callable_not_configured"

    try:
        try:
            result = uploader(dni=dni, file_path=str(firma_path))
        except TypeError:
            result = uploader(dni, str(firma_path))
    except Exception as exc:
        return False, f"upload_exception={exc}"

    if isinstance(result, tuple) and len(result) >= 2:
        return bool(result[0]), str(result[1])

    if isinstance(result, dict):
        ok = bool(result.get("ok", result.get("status") in {"ok", "cargado", "uploaded"}))
        detail = str(result.get("detail", result.get("message", "")) or "")
        return ok, (detail or "upload_dict_result")

    if isinstance(result, bool):
        return result, ("upload_ok" if result else "upload_failed")

    return bool(result), "upload_generic_result"


def procesar_firma_digital_por_dni(
    dni: str,
    firma_source_map: dict[str, str],
    credentials_path: str,
    lote_dir: Path,
    max_kb: int,
    headroom_pct: float,
    overwrite_existing: bool,
    strict_size_limit: bool,
    upload_enabled: bool,
    upload_callable: str,
    keep_temp_files: bool,
    save_original: bool = True,
) -> dict:
    dni_digits = _normalizar_dni(dni)
    if not dni_digits:
        return {"status": "error_procesamiento", "observation": "DNI INVALIDO", "detail": "dni vacio"}

    raw = str(firma_source_map.get(dni_digits, "") or "").strip()
    if raw.startswith("__MULTIPLE__"):
        # Multiples firmas distintas en la fuente: no se puede elegir -> REVISAR MANUAL, pero
        # SI guardamos TODAS las originales candidatas para que el revisor las vea/escoja.
        urls = [u.strip() for u in raw.split("\t")[1:] if u.strip()]
        guardadas = []
        if save_original:
            destino_dir = lote_dir / dni_digits
            destino_dir.mkdir(parents=True, exist_ok=True)
            for idx, u in enumerate(urls, 1):
                fid = _extraer_drive_file_id(u)
                if not fid:
                    continue
                try:
                    cont, mim = _descargar_drive_bytes(fid, credentials_path)
                    ext = ext_desde_mime(mim, cont)
                    destino = destino_dir / f"firma_digital_{dni_digits}_original_{idx}.{ext}"
                    if not destino.exists() or overwrite_existing:
                        destino.write_bytes(cont)
                    guardadas.append(destino.name)
                except Exception:
                    continue
        obs = f"{dni_digits} MULTIPLES FIRMAS EN FUENTE ({len(urls)})"
        if guardadas:
            obs += " - ORIGINALES GUARDADAS"
        return {
            "status": "revision_manual",
            "observation": obs,
            "detail": f"dni_con_multiples_urls_en_hoja_base candidatos={len(urls)} guardadas={len(guardadas)}",
        }

    if not raw:
        return {
            "status": "sin_registros",
            "observation": f"{dni_digits} SIN FIRMA DIGITAL EN FUENTE",
            "detail": "sin valor en Cargar Firma Digital",
        }

    file_id = _extraer_drive_file_id(raw)
    if not file_id:
        return {
            "status": "error_descarga",
            "observation": f"{dni_digits} URL FIRMA DIGITAL INVALIDA",
            "detail": f"valor_fuente={raw}",
        }

    try:
        content, mime = _descargar_drive_bytes(file_id, credentials_path)
    except Exception as exc:
        return {
            "status": "error_descarga",
            "observation": f"{dni_digits} ERROR DESCARGA FIRMA DIGITAL",
            "detail": str(exc),
        }

    original_path = None
    original_detail = "original_skip"
    if save_original:
        try:
            original_path = guardar_original(
                lote_dir,
                dni_digits,
                content,
                mime,
                prefix="firma_digital",
                overwrite_existing=overwrite_existing,
            )
            original_detail = f"original_saved={original_path.name}"
        except Exception as exc:
            # No bloquear el tratamiento si falla guardar la original.
            original_detail = f"original_error={exc}"

    try:
        image = _abrir_imagen_procesable(content)
    except Exception as exc:
        return {
            "status": "error_procesamiento",
            "observation": f"{dni_digits} FIRMA NO PROCESABLE",
            "detail": f"mime={mime} {exc}",
        }

    try:
        processed_img, process_detail, review_manual, thickened = _procesar_firma_imagen(image)
    except Exception as exc:
        return {
            "status": "error_procesamiento",
            "observation": f"{dni_digits} ERROR PROCESAMIENTO FIRMA",
            "detail": f"mime={mime} process_exception={exc}",
        }

    if review_manual:
        if "not_signature_detected" in process_detail:
            hard_non_signature_markers = (
                "collapse_after_filter",
                "portrait_like_blob",
                "dense_blob",
                "dense_regions",
            )
            if any(marker in process_detail for marker in hard_non_signature_markers):
                return {
                    "status": "error_procesamiento",
                    "observation": f"{dni_digits} NO CORRESPONDE A FIRMA DIGITAL",
                    "detail": f"mime={mime} {process_detail}",
                }
            return {
                "status": "revision_manual",
                "observation": f"{dni_digits} PATRON DE FIRMA NO VALIDO, REVISAR FUENTE",
                "detail": f"mime={mime} {process_detail}",
            }
        return {
            "status": "revision_manual",
            "observation": f"{dni_digits} FIRMA REQUIERE REVISION MANUAL",
            "detail": f"mime={mime} {process_detail}",
        }

    target_bytes = max(1, int(max_kb * 1024 * headroom_pct))
    png_data, png_detail, within_limit = _png_menor_a_limite(processed_img, target_bytes)

    if strict_size_limit and not within_limit:
        return {
            "status": "error_procesamiento",
            "observation": f"{dni_digits} FIRMA NO CUMPLE LIMITE < {max_kb}KB",
            "detail": png_detail,
        }

    try:
        local_path, temp_path, reused_existing = _guardar_firma_local(
            lote_dir=lote_dir,
            dni=dni_digits,
            contenido_png=png_data,
            overwrite_existing=overwrite_existing,
            keep_temp_files=keep_temp_files,
        )
    except Exception as exc:
        return {
            "status": "error_procesamiento",
            "observation": f"{dni_digits} ERROR GUARDADO FIRMA",
            "detail": str(exc),
        }

    if reused_existing:
        return {
            "status": "ok_procesado",
            "observation": "FIRMA EXISTENTE, NO SOBRESCRITA",
            "detail": f"mime={mime} existing_file_kept",
            "local_path": str(local_path),
        }

    if not upload_enabled:
        obs = "DESCARGADO Y PROCESADO"
        if thickened:
            obs = "DESCARGADO, PROCESADO Y TRAZO MEJORADO"
        return {
            "status": "ok_procesado",
            "observation": obs,
            "detail": f"mime={mime} {original_detail} {process_detail} {png_detail}",
            "local_path": str(local_path),
            "original_path": str(original_path) if original_path else "",
            "temp_path": str(temp_path) if temp_path else "",
        }

    ok_upload, upload_detail = _cargar_firma_a_expediente(dni_digits, local_path, upload_callable)
    if not ok_upload:
        return {
            "status": "error_carga",
            "observation": f"{dni_digits} ERROR CARGA FIRMA DIGITAL",
            "detail": f"mime={mime} {process_detail} {upload_detail}",
            "local_path": str(local_path),
            "temp_path": str(temp_path) if temp_path else "",
        }

    obs = "DESCARGADO, PROCESADO Y CARGADO"
    if thickened:
        obs = "DESCARGADO, PROCESADO, TRAZO MEJORADO Y CARGADO"

    return {
        "status": "ok_cargado",
        "observation": obs,
        "detail": f"mime={mime} {original_detail} {process_detail} {png_detail} {upload_detail}",
        "local_path": str(local_path),
        "original_path": str(original_path) if original_path else "",
        "temp_path": str(temp_path) if temp_path else "",
    }
