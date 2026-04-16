import shutil
import subprocess
import importlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import GaleniusConfig
from .selectors import SEL
from .scraping_utils import esperar_hasta


@dataclass
class CertificateRow:
    row_index: int
    numero_orden: str
    fecha_atencion: str
    fecha_dt: datetime | None
    paciente: str
    empresa: str
    pdf_href: str
    cells: list[str]


def _normalizar_texto(texto: str) -> str:
    raw = str(texto or "").strip().lower()
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    return raw


def _parse_fecha(texto: str) -> datetime | None:
    raw = str(texto or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    return None


def abrir_vista_certificados(page, cfg: GaleniusConfig) -> None:
    page.goto(cfg.certificados_url, wait_until="domcontentloaded", timeout=cfg.timeout_ms)
    page.locator(SEL["certificados_dni_input"]).wait_for(state="visible", timeout=cfg.timeout_ms)


def buscar_dni(page, dni: str, cfg: GaleniusConfig) -> None:
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    if not dni_digits:
        raise ValueError("DNI vacío o inválido")

    dni_input = page.locator(SEL["certificados_dni_input"]).first
    dni_input.fill(dni_digits)
    page.locator(SEL["certificados_search_button"]).first.click(timeout=cfg.timeout_ms)
    esperar_hasta(lambda: _resultados_listos(page), timeout_ms=min(cfg.timeout_ms, 8000), sleep_ms=150)


def _resultados_listos(page) -> bool:
    try:
        if page.locator(SEL["certificados_results_rows"]).count() > 0:
            return True
    except Exception:
        pass

    try:
        body_text = page.locator("body").inner_text(timeout=500)
    except Exception:
        body_text = ""
    patrones = ["no se encontraron", "sin resultados", "sin registros", "no hay registros"]
    low = _normalizar_texto(body_text)
    return any(p in low for p in patrones)


def detectar_sin_registros(page) -> bool:
    try:
        if page.locator('td[colspan="11"]:has-text("Sin registros existentes")').count() > 0:
            return True
    except Exception:
        pass

    try:
        tbody_text = page.locator("table.table tbody").inner_text(timeout=500)
    except Exception:
        tbody_text = ""
    return "sin registros existentes" in _normalizar_texto(tbody_text)


def leer_resultados_certificados(page) -> list[CertificateRow]:
    rows = []
    row_locator = page.locator(SEL["certificados_results_rows"])
    total = row_locator.count()
    for index in range(total):
        row = row_locator.nth(index)
        try:
            cells = [str(cell or "").strip() for cell in row.locator("td").all_inner_texts()]
        except Exception:
            cells = []
        if not cells:
            continue

        try:
            pdf_href = row.locator(SEL["certificados_pdf_link"]).first.get_attribute("href") or ""
        except Exception:
            pdf_href = ""

        if not pdf_href:
            continue

        fecha_atencion = cells[2] if len(cells) > 2 else ""
        rows.append(
            CertificateRow(
                row_index=index,
                numero_orden=cells[1] if len(cells) > 1 else "",
                fecha_atencion=fecha_atencion,
                fecha_dt=_parse_fecha(fecha_atencion),
                paciente=cells[3] if len(cells) > 3 else "",
                empresa=cells[4] if len(cells) > 4 else "",
                pdf_href=pdf_href,
                cells=cells,
            )
        )
    return rows


def elegir_resultado_mas_cercano(rows: list[CertificateRow]) -> CertificateRow | None:
    candidatos = [row for row in rows if row.pdf_href]
    if not candidatos:
        return None

    hoy = date.today()

    def _clave(row: CertificateRow) -> tuple[int, int]:
        if row.fecha_dt is None:
            return (10**9, 10**9)
        delta = abs((row.fecha_dt.date() - hoy).days)
        return (delta, -int(row.fecha_dt.timestamp()))

    return sorted(candidatos, key=_clave)[0]


def _optimizar_pdf_pikepdf(src: Path, dst: Path) -> tuple[bool, str]:
    try:
        pikepdf = importlib.import_module("pikepdf")
        with pikepdf.open(src) as pdf:
            pdf.save(dst)
        return True, "optimizacion sin perdida"
    except Exception as exc:
        return False, f"fallo pikepdf: {exc}"


def _optimizar_pdf_ghostscript(src: Path, dst: Path, preset: str) -> tuple[bool, str]:
    gs_bin = None
    for candidate in ("gswin64c", "gswin32c", "gs"):
        found = shutil.which(candidate)
        if found:
            gs_bin = found
            break
    if not gs_bin:
        return False, "ghostscript no disponible"

    cmd = [
        gs_bin,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.4",
        f"-dPDFSETTINGS={preset}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={dst}",
        str(src),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        if proc.returncode != 0:
            stderr_txt = (proc.stderr or proc.stdout or "").strip()
            return False, f"ghostscript rc={proc.returncode} {stderr_txt}"
        return True, f"ghostscript {preset}"
    except Exception as exc:
        return False, f"fallo ghostscript: {exc}"


def _preparar_pdf_para_limite(src: Path, limite_bytes: int) -> tuple[Path, str]:
    if limite_bytes <= 0:
        return src, ""

    try:
        current_size = src.stat().st_size
    except Exception:
        return src, ""
    if current_size <= limite_bytes:
        return src, ""

    out_base = src.with_name(f"{src.stem}_opt{src.suffix}")
    detalles = []

    ok_lossless, detalle_lossless = _optimizar_pdf_pikepdf(src, out_base)
    if ok_lossless and out_base.exists() and out_base.stat().st_size > 0:
        detalles.append(f"{detalle_lossless}: {current_size} -> {out_base.stat().st_size}")
        if out_base.stat().st_size <= limite_bytes:
            return out_base, "; ".join(detalles)
    elif detalle_lossless:
        detalles.append(detalle_lossless)

    for preset in ("/printer", "/ebook", "/screen"):
        ok_gs, detalle_gs = _optimizar_pdf_ghostscript(src, out_base, preset)
        if ok_gs and out_base.exists() and out_base.stat().st_size > 0:
            detalles.append(f"{detalle_gs}: {current_size} -> {out_base.stat().st_size}")
            if out_base.stat().st_size <= limite_bytes:
                return out_base, "; ".join(detalles)
        elif detalle_gs:
            detalles.append(detalle_gs)

    return src, "; ".join(detalles)


def descargar_pdf_resultado(page, cfg: GaleniusConfig, row: CertificateRow, dni: str, lote_dir: Path) -> tuple[Path, str]:
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    if not dni_digits:
        raise ValueError("DNI vacío o inválido")

    expediente_dir = lote_dir / dni_digits
    expediente_dir.mkdir(parents=True, exist_ok=True)

    final_path = expediente_dir / f"certificado_medico_{dni_digits}.pdf"
    if final_path.exists() and not cfg.overwrite_existing:
        return final_path, "archivo existente conservado"

    temp_path = expediente_dir / f".{final_path.name}.tmp"
    opt_path = expediente_dir / f".{final_path.stem}_opt{final_path.suffix}"
    href_abs = urljoin(page.url, row.pdf_href)

    if temp_path.exists():
        temp_path.unlink()
    if opt_path.exists():
        opt_path.unlink()

    try:
        try:
            with page.expect_download(timeout=min(cfg.timeout_ms, 8000)) as download_info:
                page.locator(SEL["certificados_results_rows"]).nth(row.row_index).locator(SEL["certificados_pdf_link"]).first.click(timeout=cfg.timeout_ms)
            download = download_info.value
            download.save_as(temp_path)
        except Exception:
            request_ctx = getattr(page.context, "request", None)
            if request_ctx is None:
                raise RuntimeError(f"No se pudo descargar el PDF desde {href_abs}")
            response = request_ctx.get(href_abs, timeout=cfg.timeout_ms)
            if not response.ok:
                raise RuntimeError(f"La descarga HTTP fallo con status {response.status} para {href_abs}")
            temp_path.write_bytes(response.body())

        if not temp_path.exists() or temp_path.stat().st_size <= 0:
            raise RuntimeError("El archivo descargado quedó vacío")

        limite_bytes = max(1, int(cfg.max_pdf_kb * 1024 * cfg.pdf_size_headroom_pct))
        optimizado, detalle = _preparar_pdf_para_limite(temp_path, limite_bytes)
        fuente_final = optimizado if optimizado.exists() else temp_path

        if final_path.exists():
            if cfg.overwrite_existing:
                final_path.unlink()
            else:
                return final_path, "archivo existente conservado"

        if fuente_final != final_path:
            shutil.move(str(fuente_final), str(final_path))
        else:
            final_path = fuente_final

        if temp_path.exists() and temp_path != final_path:
            temp_path.unlink(missing_ok=True)
        if opt_path.exists() and opt_path != final_path:
            opt_path.unlink(missing_ok=True)

        return final_path, detalle
    finally:
        if temp_path.exists() and temp_path != final_path:
            temp_path.unlink(missing_ok=True)
        if opt_path.exists() and opt_path != final_path:
            opt_path.unlink(missing_ok=True)
