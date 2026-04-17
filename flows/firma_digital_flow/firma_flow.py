import importlib
import io
import os
import re
import threading
import unicodedata
from pathlib import Path

from PIL import Image, UnidentifiedImageError

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

    resultado: dict[str, str] = {}
    duplicados: dict[str, set[str]] = {}
    for row in rows:
        dni = _normalizar_dni(row.get(dni_col, ""))
        raw_url = str(row.get(firma_col, "") or "").strip()
        if not dni or not raw_url:
            continue

        if dni in resultado and resultado[dni] != raw_url:
            prev = resultado[dni]
            if not str(prev).startswith("__MULTIPLE__"):
                duplicados.setdefault(dni, set()).add(prev)
            duplicados.setdefault(dni, set()).add(raw_url)
            resultado[dni] = "__MULTIPLE__"
            continue

        if str(resultado.get(dni, "")).startswith("__MULTIPLE__"):
            duplicados.setdefault(dni, set()).add(raw_url)
            continue

        resultado[dni] = raw_url

    if duplicados and hasattr(logger, "warning"):
        logger.warning(
            "[FIRMA DIGITAL] DNIs con multiples firmas en fuente | cantidad=%s | ejemplo=%s",
            len(duplicados),
            next(iter(duplicados.keys())),
        )

    logger.info("[FIRMA DIGITAL] Fuente cargada | filas=%s | dni_con_firma=%s", len(rows), len(resultado))
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

    # Importante: no rotar en ningun caso.
    return image.convert("RGB")


def _generar_mascara_firma(gray, cv2_mod, np_mod):
    blur = cv2_mod.GaussianBlur(gray, (5, 5), 0)
    _, mask_otsu = cv2_mod.threshold(blur, 0, 255, cv2_mod.THRESH_BINARY_INV + cv2_mod.THRESH_OTSU)
    mask_adapt = cv2_mod.adaptiveThreshold(
        blur,
        255,
        cv2_mod.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2_mod.THRESH_BINARY_INV,
        31,
        12,
    )

    mask = cv2_mod.bitwise_or(mask_otsu, mask_adapt)
    fg_ratio = float(np_mod.count_nonzero(mask)) / float(max(1, mask.size))

    # Si la mascara toma casi toda la imagen, usamos una opcion mas conservadora.
    if fg_ratio > 0.65:
        mask = mask_otsu
        fg_ratio = float(np_mod.count_nonzero(mask)) / float(max(1, mask.size))

    return mask, f"mask_fg_ratio={fg_ratio:.4f}"


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
    # Distancia de enlace mas estricta para evitar unir ruido lejano en bandas.
    link_dist = max(10.0, diag * 0.055)
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


def _engrosar_si_tenue(mask, cv2_mod, np_mod):
    h, w = mask.shape[:2]
    fg_ratio = float(np_mod.count_nonzero(mask)) / float(max(1, h * w))

    # Engrosado aun mas conservador para evitar firmas "infladas".
    if fg_ratio < 0.0012:
        kernel = cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2))
        out = cv2_mod.dilate(mask, kernel, iterations=1)
        fg_before = float(np_mod.count_nonzero(mask))
        fg_after = float(np_mod.count_nonzero(out))
        if fg_before > 0 and (fg_after / fg_before) > 1.08:
            return mask, "stroke_thickness_kept_overgrow_guard", False
        return out, "stroke_thickened_soft", True

    if fg_ratio < 0.0030:
        kernel = cv2_mod.getStructuringElement(cv2_mod.MORPH_ELLIPSE, (2, 2))
        out = cv2_mod.morphologyEx(mask, cv2_mod.MORPH_CLOSE, kernel, iterations=1)
        # Si el cierre añade demasiada masa, se descarta por fidelidad.
        fg_before = float(np_mod.count_nonzero(mask))
        fg_after = float(np_mod.count_nonzero(out))
        if fg_before > 0 and (fg_after / fg_before) > 1.08:
            return mask, "stroke_thickness_kept_overgrow_guard", False
        return out, "stroke_thickened_micro", True

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


def _render_firma_en_fondo_claro(gray_crop, mask_crop, cv2_mod, np_mod):
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

    # Fondo blanco + trazo casi binario para evitar halos y pixeles grises dispersos.
    out = np_mod.full(gray_crop.shape, 255, dtype=np_mod.uint8)
    out[mask_render > 0] = 26

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


def _intentar_rescate_alt(arr_rgb, gray, base_detail: str, cv2_mod, np_mod):
    mask_alt = _mascara_por_tinta_color(arr_rgb, gray, cv2_mod, np_mod)
    alt_ratio = float(np_mod.count_nonzero(mask_alt)) / float(max(1, mask_alt.size))
    if alt_ratio < 0.0005:
        return False, None, f"{base_detail} alt_ink_rescue_too_faint alt_ratio={alt_ratio:.4f}", False

    mask_alt, alt_clean_detail = _limpiar_ruido_conservador(mask_alt, cv2_mod, np_mod)
    mask_alt, alt_border_detail = _suprimir_artefactos_de_borde(mask_alt, cv2_mod, np_mod)
    mask_alt, alt_cluster_detail = _filtrar_cluster_principal_firma(mask_alt, cv2_mod, np_mod)

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

    mask_alt_final, alt_thick_detail, alt_thickened = _engrosar_si_tenue(mask_alt, cv2_mod, np_mod)

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

    out_alt = _render_firma_en_fondo_claro(gray_crop_alt, mask_crop_alt, cv2_mod, np_mod)
    detail_alt = (
        f"{base_detail} alt_ink_rescue alt_ratio={alt_ratio:.4f} "
        f"{alt_clean_detail} {alt_border_detail} {alt_cluster_detail} "
        f"{alt_thick_detail} crop_ratio={crop_ratio_alt:.4f}"
    )
    return True, out_alt, detail_alt.strip(), alt_thickened


def _procesar_firma_imagen(image: Image.Image) -> tuple[Image.Image, str, bool, bool]:
    """
    Retorna (imagen_procesada, detalle, revision_manual, trazo_engrosado).
    """
    cv2_mod, np_mod = _cargar_cv2_numpy()

    arr_rgb = np_mod.array(image.convert("RGB"))
    gray = cv2_mod.cvtColor(arr_rgb, cv2_mod.COLOR_RGB2GRAY)

    mask_raw, mask_detail = _generar_mascara_firma(gray, cv2_mod, np_mod)
    raw_fg = int(np_mod.count_nonzero(mask_raw))
    raw_ratio = float(raw_fg) / float(max(1, mask_raw.size))

    if raw_fg < 80 or raw_ratio < 0.0005:
        return image.convert("L"), f"{mask_detail} signature_too_faint", True, False

    if raw_ratio > 0.55:
        mask_detail = f"{mask_detail} high_fg_ratio={raw_ratio:.4f}"

    mask_clean, clean_detail = _limpiar_ruido_conservador(mask_raw, cv2_mod, np_mod)
    mask_border, border_detail = _suprimir_artefactos_de_borde(mask_clean, cv2_mod, np_mod)
    mask_cluster, cluster_detail = _filtrar_cluster_principal_firma(mask_border, cv2_mod, np_mod)

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
            alt_rescue_cache = _intentar_rescate_alt(arr_rgb, gray, main_base_detail, cv2_mod, np_mod)
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

    mask_final, thick_detail, thickened = _engrosar_si_tenue(mask_cluster, cv2_mod, np_mod)

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

    out = _render_firma_en_fondo_claro(gray_crop, mask_crop, cv2_mod, np_mod)
    detail = (
        f"{mask_detail} {clean_detail} {border_detail} {cluster_detail} "
        f"{thick_detail} crop_ratio={crop_ratio:.4f}"
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
) -> dict:
    dni_digits = _normalizar_dni(dni)
    if not dni_digits:
        return {"status": "error_procesamiento", "observation": "DNI INVALIDO", "detail": "dni vacio"}

    raw = str(firma_source_map.get(dni_digits, "") or "").strip()
    if raw == "__MULTIPLE__":
        return {
            "status": "revision_manual",
            "observation": f"{dni_digits} MULTIPLES FIRMAS EN FUENTE",
            "detail": "dni_con_multiples_urls_en_hoja_base",
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
            "detail": f"mime={mime} {process_detail} {png_detail}",
            "local_path": str(local_path),
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
        "detail": f"mime={mime} {process_detail} {png_detail} {upload_detail}",
        "local_path": str(local_path),
        "temp_path": str(temp_path) if temp_path else "",
    }
