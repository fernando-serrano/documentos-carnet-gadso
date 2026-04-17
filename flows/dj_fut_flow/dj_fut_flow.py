import importlib
import io
import random
import re
import shutil
import threading
import unicodedata
from datetime import datetime
from pathlib import Path

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


def cargar_fuente_dj_fut_por_dni(sheet_url: str, logger) -> dict[str, str]:
    rows, fieldnames = read_google_sheet_rows(sheet_url)
    dni_col = _resolver_columna(fieldnames, ["dni"])
    url_col = _resolver_columna(
        fieldnames,
        [
            "Merged Doc URL - DJ FUT",
            "merged doc url dj fut",
            "merged doc url - dj fut",
            "dj fut",
        ],
    )

    if not dni_col:
        raise RuntimeError("No se encontro columna DNI en hoja base DJ FUT")
    if not url_col:
        raise RuntimeError("No se encontro columna 'Merged Doc URL - DJ FUT' en hoja base")

    resultado: dict[str, str] = {}
    for row in rows:
        dni = _normalizar_dni(row.get(dni_col, ""))
        raw_url = str(row.get(url_col, "") or "").strip()
        if not dni or not raw_url:
            continue
        resultado[dni] = raw_url

    logger.info("[DJ FUT] Fuente cargada | filas=%s | dni_con_documento=%s", len(rows), len(resultado))
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
        raise RuntimeError("Drive no devolvio binario para DJ FUT")
    return bytes(content), mime


def _optimizar_pdf_pikepdf(src: Path, dst: Path) -> tuple[bool, str]:
    try:
        pikepdf = importlib.import_module("pikepdf")
        with pikepdf.open(src) as pdf:
            pdf.remove_unreferenced_resources()

            # Optimizacion sin perdida: recomprime streams Flate y empaqueta objetos.
            save_kwargs = {
                "compress_streams": True,
                "recompress_flate": True,
                "object_stream_mode": pikepdf.ObjectStreamMode.generate,
            }
            try:
                pdf.save(dst, **save_kwargs)
                return True, "pikepdf_ok_advanced"
            except TypeError:
                # Fallback para versiones antiguas de pikepdf.
                pdf.save(dst, compress_streams=True, recompress_flate=True)
                return True, "pikepdf_ok_legacy"
    except Exception as exc:
        return False, f"pikepdf_error={exc}"


def _optimizar_pdf_ghostscript(
    src: Path,
    dst: Path,
    preset: str,
    image_dpi: int,
    jpeg_quality: int,
) -> tuple[bool, str]:
    subprocess = importlib.import_module("subprocess")
    shutil_mod = importlib.import_module("shutil")

    gs_cmd = shutil_mod.which("gswin64c") or shutil_mod.which("gswin32c") or shutil_mod.which("gs")
    if not gs_cmd:
        return False, "ghostscript_no_instalado"

    cmd = [
        gs_cmd,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={preset}",
        "-dDetectDuplicateImages=true",
        "-dCompressFonts=true",
        "-dSubsetFonts=true",
        "-dAutoFilterColorImages=false",
        "-dAutoFilterGrayImages=false",
        "-dColorImageFilter=/DCTEncode",
        "-dGrayImageFilter=/DCTEncode",
        "-dDownsampleColorImages=true",
        "-dDownsampleGrayImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        "-dGrayImageDownsampleType=/Bicubic",
        f"-dColorImageResolution={image_dpi}",
        f"-dGrayImageResolution={image_dpi}",
        f"-dJPEGQ={jpeg_quality}",
        "-dNOPAUSE",
        "-dBATCH",
        "-dQUIET",
        f"-sOutputFile={dst}",
        str(src),
    ]
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except Exception as exc:
        return False, f"ghostscript_error={exc}"

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[:180]
        return False, f"ghostscript_fail code={completed.returncode} err={stderr}"
    return True, f"ghostscript_ok preset={preset} dpi={image_dpi} q={jpeg_quality}"


def _es_numero_corto(texto: str) -> bool:
    return bool(re.fullmatch(r"\d{1,4}", str(texto or "").strip()))


def _extraer_fecha_tabla_inferior(page, fitz_mod) -> tuple[dict[str, object] | None, str]:
    anchors = []
    for label in ("FECHA DE LA DECLARACION", "FECHA DE LA DECLARACIÓN", "FECHA DE LA DECLARACION "):
        anchors = page.search_for(label)
        if anchors:
            break

    if anchors:
        anchor = anchors[0]
        area = fitz_mod.Rect(anchor.x0 - 40, anchor.y0 - 10, anchor.x1 + 180, anchor.y1 + 110)

        words = page.get_text("words") or []
        numericos = []
        for word in words:
            x0, y0, x1, y1, texto = word[:5]
            if not _es_numero_corto(texto):
                continue
            cx = (float(x0) + float(x1)) / 2.0
            cy = (float(y0) + float(y1)) / 2.0
            if not area.contains(fitz_mod.Point(cx, cy)):
                continue
            numericos.append({
                "rect": fitz_mod.Rect(float(x0), float(y0), float(x1), float(y1)),
                "text": str(texto).strip(),
            })

        if len(numericos) >= 3:
            filas: dict[float, list[dict[str, object]]] = {}
            for item in numericos:
                y_key = round(float(item["rect"].y0) / 3.0) * 3.0
                filas.setdefault(y_key, []).append(item)

            fila_objetivo = None
            for y_key, fila in sorted(filas.items(), key=lambda kv: kv[0], reverse=True):
                if len(fila) >= 3:
                    fila_objetivo = sorted(fila, key=lambda it: float(it["rect"].x0))
                    break

            if fila_objetivo:
                dd_item = fila_objetivo[0]
                mm_item = fila_objetivo[1]
                yyyy_item = fila_objetivo[2]

                try:
                    old_day = int(str(dd_item["text"]))
                except Exception:
                    old_day = None

                try:
                    old_month = int(str(mm_item["text"]))
                except Exception:
                    old_month = None

                try:
                    old_year = int(str(yyyy_item["text"]))
                except Exception:
                    old_year = None

                return {
                    "dd_rect": dd_item["rect"],
                    "mm_rect": mm_item["rect"],
                    "aaaa_rect": yyyy_item["rect"],
                    "old_day": old_day,
                    "old_month": old_month,
                    "old_year": old_year,
                }, "date_row_found"

    # Fallback cuando la etiqueta no es detectable: buscamos la fila numerica DD/MM/AAAA
    # SOLO en la tabla FECHA DE LA DECLARACIÓN (últimas coordenadas: y > 90% de altura)
    words = page.get_text("words") or []
    page_height = page.rect.height
    # Buscar en rango muy específico: 90-92% de altura (donde está la tabla)
    lower_bound = page_height * 0.90
    upper_bound = page_height * 0.92
    
    numericos_todos = []
    for word in words:
        x0, y0, x1, y1, texto = word[:5]
        if not _es_numero_corto(texto):
            continue
        # Solo números DENTRO del rango específico de la tabla
        if not (lower_bound <= float(y0) <= upper_bound):
            continue
        numericos_todos.append({
            "rect": fitz_mod.Rect(float(x0), float(y0), float(x1), float(y1)),
            "text": str(texto).strip(),
        })

    if len(numericos_todos) < 3:
        return None, "date_numeric_row_not_found"

    # Ordenar por x (izquierda a derecha) ya que están todos en la misma fila
    numericos_todos = sorted(numericos_todos, key=lambda it: float(it["rect"].x0))
    
    # Tomar los primeros 3: DD, MM, AAAA
    fila_objetivo = numericos_todos[:3]
    
    try:
        d = int(str(fila_objetivo[0]["text"]))
        m = int(str(fila_objetivo[1]["text"]))
        y = int(str(fila_objetivo[2]["text"]))
    except Exception:
        return None, "date_row_parsing_error"
    
    if not (1 <= d <= 31 and 1 <= m <= 12 and 2000 <= y <= 2100):
        return None, "date_row_validation_failed"

    dd_item = fila_objetivo[0]
    mm_item = fila_objetivo[1]
    yyyy_item = fila_objetivo[2]

    try:
        old_day = int(str(dd_item["text"]))
    except Exception:
        old_day = None

    try:
        old_month = int(str(mm_item["text"]))
    except Exception:
        old_month = None

    try:
        old_year = int(str(yyyy_item["text"]))
    except Exception:
        old_year = None

    return {
        "dd_rect": dd_item["rect"],
        "mm_rect": mm_item["rect"],
        "aaaa_rect": yyyy_item["rect"],
        "old_day": old_day,
        "old_month": old_month,
        "old_year": old_year,
    }, "date_row_found_fallback"


def _tapar_y_escribir(page, rect, value: str, fitz_mod) -> None:
    """
    Tapa correctamente el número antiguo y reinserta el nuevo con la misma fuente/tamaño.
    Implementa la lógica robusta del script firma.py adaptada para lotes.
    """
    # Extraer fuente y tamaño original del contexto del PDF
    fontname_original = "Helvetica"
    fontsize_original = 11.0
    
    try:
        text_dict = page.get_text("dict")
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    span_rect = fitz_mod.Rect(span["bbox"])
                    if span_rect.intersects(rect):
                        fontname_original = span.get("font", fontname_original)
                        fontsize_original = span.get("size", fontsize_original)
                        break
    except Exception:
        pass
    
    # Aplicar reducción ligera para que quepa perfectamente
    fontsize_final = fontsize_original * 0.92
    
    # Normalizar fuente: usa Helvetica-Bold si está disponible
    if "bold" in fontname_original.lower() or "b" in fontname_original.lower():
        fontname_final = "Helvetica-Bold"
    else:
        fontname_final = "Helvetica"
    
    # Tapar con rectángulo blanco
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(fill=(1, 1, 1), color=(1, 1, 1))
    shape.commit()
    
    # Calcular posición centrada
    text_width = fitz_mod.get_text_length(value, fontname=fontname_final, fontsize=fontsize_final)
    x_centrado = rect.x0 + (rect.width - text_width) / 2.0
    y_centrado = rect.y0 + (rect.height / 2.0) + (fontsize_final * 0.35)
    
    # Insertar el nuevo número
    page.insert_text(
        fitz_mod.Point(x_centrado, y_centrado),
        value,
        fontsize=fontsize_final,
        fontname=fontname_final,
        color=(0, 0, 0)
    )


def _extraer_celdas_fecha_por_geometria(page) -> tuple[dict[str, object] | None, str]:
    try:
        fitz_mod = importlib.import_module("fitz")
        cv2_mod = importlib.import_module("cv2")
        np_mod = importlib.import_module("numpy")
    except Exception as exc:
        return None, f"date_geom_missing_deps={exc}"

    scale = 2.0
    pix = page.get_pixmap(matrix=fitz_mod.Matrix(scale, scale), alpha=False)
    img = np_mod.frombuffer(pix.samples, dtype=np_mod.uint8).reshape(pix.height, pix.width, 3)
    gray = cv2_mod.cvtColor(img, cv2_mod.COLOR_BGR2GRAY)
    th = cv2_mod.adaptiveThreshold(
        gray,
        255,
        cv2_mod.ADAPTIVE_THRESH_MEAN_C,
        cv2_mod.THRESH_BINARY_INV,
        31,
        15,
    )

    contours, _ = cv2_mod.findContours(th, cv2_mod.RETR_LIST, cv2_mod.CHAIN_APPROX_SIMPLE)
    h_img, w_img = gray.shape[:2]

    candidates = []
    for contour in contours:
        x, y, w, h = cv2_mod.boundingRect(contour)
        area = w * h
        if area < 25000:
            continue
        if not (w_img * 0.08 <= x <= w_img * 0.55):
            continue
        if y < h_img * 0.45:
            continue
        ratio = w / float(max(1, h))
        if ratio < 1.1 or ratio > 2.2:
            continue
        candidates.append((area, x, y, w, h))

    if not candidates:
        return None, "date_geom_table_not_found"

    _, x, y, w, h = sorted(candidates, reverse=True)[0]

    row_y0 = y + int(h * 0.60)
    row_y1 = y + int(h * 0.97)
    col_w = w / 3.0

    dd_rect_img = fitz_mod.Rect(x + col_w * 0.05, row_y0, x + col_w * 0.95, row_y1)
    mm_rect_img = fitz_mod.Rect(x + col_w * 1.05, row_y0, x + col_w * 1.95, row_y1)

    inv = 1.0 / scale
    dd_rect = fitz_mod.Rect(dd_rect_img.x0 * inv, dd_rect_img.y0 * inv, dd_rect_img.x1 * inv, dd_rect_img.y1 * inv)
    mm_rect = fitz_mod.Rect(mm_rect_img.x0 * inv, mm_rect_img.y0 * inv, mm_rect_img.x1 * inv, mm_rect_img.y1 * inv)

    return {
        "dd_rect": dd_rect,
        "mm_rect": mm_rect,
        "old_day": None,
        "old_month": None,
        "old_year": None,
    }, "date_geom_found"


def _actualizar_fecha_declaracion_archivo(pdf_path: Path) -> tuple[str, bool]:
    """
    Edita la fecha del PDF guardado en DISCO - COPIA EXACTA de la lógica de firma.py.
    Modifica el archivo IN-PLACE con toda la robustez de firma.py.
    Retorna (detalle, éxito).
    """
    try:
        fitz_mod = importlib.import_module("fitz")
    except Exception as exc:
        return f"date_edit_disk_no_pymupdf={exc}", False

    if not pdf_path.exists():
        return "date_edit_disk_file_not_found", False

    try:
        doc = fitz_mod.open(pdf_path)
    except Exception as exc:
        return f"date_edit_disk_open_error={exc}", False

    try:
        page = doc[0]

        # PASO 1: Extraer la fecha DESDE LA TABLA INFERIOR IZQUIERDA.
        # Esto evita tomar la fecha de aprobacion u otras fechas del documento.
        info, detail = _extraer_fecha_tabla_inferior(page, fitz_mod)
        if not info:
            return f"date_edit_disk_table_not_found={detail}", False

        old_day = info.get("old_day")
        old_month = info.get("old_month")
        old_year = info.get("old_year")
        if not isinstance(old_day, int) or not isinstance(old_month, int) or not isinstance(old_year, int):
            return "date_edit_disk_invalid_old_values", False

        old_dd_str = f"{old_day:02d}"
        old_mm_str = f"{old_month:02d}"
        old_aaaa_str = f"{old_year}"

        # PASO 2: Zona inferior izquierda (tabla de fecha)
        def es_zona_fecha(rect):
            return rect.y0 > page.rect.height * 0.78 and rect.x0 < page.rect.width * 0.55

        rect_dd = info.get("dd_rect")
        rect_mm = info.get("mm_rect")
        rect_aaaa = info.get("aaaa_rect")

        if rect_aaaa is None:
            rect_aaaa = next((r for r in page.search_for(old_aaaa_str)
                              if r.y0 > page.rect.height * 0.78 and r.x0 > page.rect.width * 0.40), None)
        
        fontname_aaaa = "Helvetica"
        fontsize_aaaa = 7.5
        
        # Extraer fuente y tamaño REALES del AAAA - COPIA LITERAL de firma.py
        if rect_aaaa:
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        if old_aaaa_str in span["text"].strip():
                            fontname_aaaa = span.get("font", "Helvetica")
                            fontsize_aaaa = span.get("size", 11.0)
                            break
        
        # Reducimos ligeramente para que quede perfecto - COPIA LITERAL de firma.py
        fontsize_final = fontsize_aaaa * 0.92
        
        # Aplicar negrita - COPIA LITERAL de firma.py (se usa True por defecto en lotes)
        USAR_NEGRITA = True
        if USAR_NEGRITA:
            fontname_final = "Helvetica-Bold"
        else:
            fontname_final = fontname_aaaa.replace("-Bold", "").replace("Bold", "")
        
        # Fallback robusto por si algun rectangulo no se detecto.
        if not rect_dd or not rect_mm or not rect_aaaa:
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        texto = span["text"].strip()
                        if re.match(r'^\d{1,2}$', texto) or re.match(r'^\d{4}$', texto):
                            r = fitz_mod.Rect(span["bbox"])
                            if es_zona_fecha(r):
                                if not rect_dd and len(texto) == 2 and int(texto) <= 31:
                                    rect_dd = r
                                elif not rect_mm and len(texto) == 2 and int(texto) <= 12:
                                    rect_mm = r
                                elif not rect_aaaa and len(texto) == 4:
                                    rect_aaaa = r
        
        if not rect_dd or not rect_mm or not rect_aaaa:
            return "date_edit_disk_rects_not_found", False
        
        # PASO 3: Calcular nueva fecha con la regla requerida.
        # - MM: si mes viejo < mes actual, actualizar; en caso contrario, conservar.
        # - DD: si dia > dia actual, random 1..dia_actual; si no, conservar.
        # - AAAA: siempre anio actual.
        now = datetime.now()
        target_month = now.month
        target_day_limit = max(1, now.day)

        if old_month < target_month:
            new_month = target_month
            month_rule = "month_updated"
        else:
            new_month = old_month
            month_rule = "month_kept"
        
        if isinstance(old_day, int) and 1 <= old_day <= target_day_limit:
            new_day = old_day
            day_rule = "day_kept"
        else:
            new_day = random.randint(1, target_day_limit)
            day_rule = "day_randomized"

        new_day_str = f"{int(new_day):02d}"
        new_month_str = f"{int(new_month):02d}"
        new_aaaa_str = f"{now.year}"
        
        # PASO 4: Cubrir con blanco los 3 campos - COPIA LITERAL de firma.py
        for rect in [rect_dd, rect_mm, rect_aaaa]:
            shape = page.new_shape()
            shape.draw_rect(rect)
            shape.finish(fill=(1, 1, 1), color=(1, 1, 1))
            shape.commit()
        
        # PASO 5: Insertar los 3 campos con la misma fuente, tamaño y negrita - COPIA LITERAL de firma.py
        for rect, texto in [(rect_dd, new_day_str), (rect_mm, new_month_str), (rect_aaaa, new_aaaa_str)]:
            text_width = fitz_mod.get_text_length(texto, fontname=fontname_final, fontsize=fontsize_final)
            x_centrado = rect.x0 + (rect.width - text_width) / 2
            y_centrado = rect.y0 + (rect.height / 2) + (fontsize_final * 0.35)
            
            page.insert_text(
                fitz_mod.Point(x_centrado, y_centrado),
                texto,
                fontsize=fontsize_final,
                fontname=fontname_final,
                color=(0, 0, 0)
            )
        
        # PASO 6: Guardar evitando escribir sobre el mismo archivo abierto.
        # En PyMuPDF, guardar directo al mismo path puede fallar y dejar la edición sin efecto.
        edited_bytes = doc.tobytes(garbage=4, deflate=1, clean=1)
        pdf_path.write_bytes(edited_bytes)
        
        return (
            f"date_edit_disk_ok old={old_dd_str}/{old_mm_str}/{old_aaaa_str} "
            f"new={new_day_str}/{new_month_str}/{new_aaaa_str} {month_rule} {day_rule}"
        ), True
        
    except Exception as exc:
        return f"date_edit_disk_error={exc}", False
    finally:
        doc.close()


def _pdf_menor_a_limite(pdf_bytes: bytes, target_bytes: int, allow_lossy: bool) -> tuple[bytes, str, bool]:
    if target_bytes <= 0:
        raise RuntimeError("Limite de bytes invalido para DJ FUT")
    if len(pdf_bytes) <= target_bytes:
        return pdf_bytes, f"pdf_ok_direct size={len(pdf_bytes)}", True

    tempfile = importlib.import_module("tempfile")

    with tempfile.TemporaryDirectory(prefix="djfut_pdf_") as temp_dir:
        temp_path = Path(temp_dir)
        src = temp_path / "src.pdf"
        out = temp_path / "out.pdf"

        src.write_bytes(pdf_bytes)
        detalles: list[str] = [f"src_size={len(pdf_bytes)}"]
        best_data = pdf_bytes
        best_size = len(pdf_bytes)

        ok, detalle = _optimizar_pdf_pikepdf(src, out)
        detalles.append(detalle)
        if ok and out.exists() and out.stat().st_size > 0:
            data = out.read_bytes()
            if len(data) < best_size:
                best_data = data
                best_size = len(data)
            if len(data) <= target_bytes:
                return data, ";".join(detalles + [f"size={len(data)}"]), True

        if allow_lossy:
            profiles = [
                ("/printer", 150, 85),
                ("/ebook", 130, 80),
                ("/ebook", 115, 75),
                ("/screen", 100, 70),
                ("/screen", 85, 62),
            ]

            for preset, image_dpi, jpeg_quality in profiles:
                ok, detalle = _optimizar_pdf_ghostscript(src, out, preset, image_dpi, jpeg_quality)
                detalles.append(detalle)
                if ok and out.exists() and out.stat().st_size > 0:
                    data = out.read_bytes()
                    if len(data) < best_size:
                        best_data = data
                        best_size = len(data)
                    if len(data) <= target_bytes:
                        return data, ";".join(detalles + [f"size={len(data)}"]), True
        else:
            detalles.append("lossy_disabled_for_audit")

        return best_data, ";".join(detalles + [f"size={best_size}", "pdf_above_limit"]), False


def _guardar_pdf_local(lote_dir: Path, dni: str, contenido_pdf: bytes, overwrite_existing: bool) -> Path:
    destino_dir = lote_dir / dni
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"djfut_{dni}.pdf"

    if destino.exists() and not overwrite_existing:
        return destino

    if destino.exists() and overwrite_existing:
        destino.unlink()

    destino.write_bytes(contenido_pdf)
    if not destino.exists() or destino.stat().st_size <= 0:
        raise RuntimeError("PDF local quedo vacio tras guardar")
    return destino


def procesar_dj_fut_por_dni(
    dni: str,
    dj_fut_source_map: dict[str, str],
    credentials_path: str,
    lote_dir: Path,
    max_kb: int,
    headroom_pct: float,
    overwrite_existing: bool,
    strict_size_limit: bool,
    allow_lossy: bool,
    date_edit_required: bool,
) -> dict:
    dni_digits = _normalizar_dni(dni)
    if not dni_digits:
        return {"status": "error", "observation": "DNI INVALIDO", "detail": "dni vacio"}

    raw = str(dj_fut_source_map.get(dni_digits, "") or "").strip()
    if not raw:
        return {
            "status": "sin_registros",
            "observation": f"{dni_digits} SIN DJ FUT EN FUENTE",
            "detail": "sin valor en Merged Doc URL - DJ FUT",
        }

    file_id = _extraer_drive_file_id(raw)
    if not file_id:
        return {
            "status": "error",
            "observation": f"{dni_digits} URL DJ FUT INVALIDA",
            "detail": f"valor_fuente={raw}",
        }

    content, mime = _descargar_drive_bytes(file_id, credentials_path)
    if not content:
        return {
            "status": "error",
            "observation": f"{dni_digits} DJ FUT VACIO",
            "detail": "Drive devolvio contenido vacio",
        }

    if "pdf" not in mime.lower() and not content.startswith(b"%PDF"):
        return {
            "status": "error",
            "observation": f"{dni_digits} DJ FUT NO ES PDF",
            "detail": f"mime={mime}",
        }

    # PASO 1: GUARDAR PDF DESCARGADO PRIMERO (sin editar fecha aún)
    local_path = _guardar_pdf_local(lote_dir, dni_digits, content, overwrite_existing)

    # PASO 2: EDITAR FECHA SOBRE EL ARCHIVO GUARDADO EN DISCO (como firma.py - más robusto)
    date_detail, date_ok = _actualizar_fecha_declaracion_archivo(local_path)
    if not date_ok and date_edit_required:
        return {
            "status": "error",
            "observation": f"{dni_digits} FECHA DECLARACION NO EDITABLE",
            "detail": date_detail,
        }
    date_warning = ""
    if not date_ok:
        date_warning = f" | FECHA NO EDITADA ({date_detail})"

    # PASO 3: LEER EL ARCHIVO EDITADO Y COMPRIMIR SI ES NECESARIO
    edited_pdf = local_path.read_bytes()
    target_bytes = max(1, int(max_kb * 1024 * headroom_pct))
    out_pdf, compress_detail, within_limit = _pdf_menor_a_limite(edited_pdf, target_bytes, allow_lossy=allow_lossy)

    # Si se comprimió, actualizar el archivo guardado
    if out_pdf != edited_pdf:
        local_path.write_bytes(out_pdf)

    if strict_size_limit and not within_limit:
        return {
            "status": "error",
            "observation": f"{dni_digits} DJ FUT NO COMPRIMIBLE < {max_kb}KB",
            "detail": compress_detail,
        }

    if within_limit:
        observation = "DESCARGADO SIN OBSERVACIONES"
    else:
        size_kb = len(out_pdf) / 1024.0
        observation = f"DESCARGADO > {max_kb}KB ({size_kb:.1f}KB)"

    if date_warning:
        observation = f"{observation}{date_warning}"

    return {
        "status": "ok",
        "observation": observation,
        "detail": f"mime={mime} {date_detail} {compress_detail}",
        "local_path": str(local_path),
    }
