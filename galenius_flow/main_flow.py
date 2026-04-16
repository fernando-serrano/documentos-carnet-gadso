from datetime import datetime
import shutil

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import GaleniusConfig
from .documents import (
    abrir_vista_certificados,
    buscar_dni,
    descargar_pdf_resultado,
    elegir_resultado_mas_cercano,
    detectar_sin_registros,
    leer_resultados_certificados,
)
from .scraping_utils import esperar_hasta, recolectar_textos_ui
from .sheets import read_google_sheet_rows, resolve_sheet_columns, update_sheet_row
from .selectors import LOGIN_ERROR_SELECTORS, SEL


class GaleniusFlowError(Exception):
    pass


class LoginGaleniusError(GaleniusFlowError):
    pass


def _validar_config_login(cfg: GaleniusConfig) -> None:
    faltantes = []
    if not cfg.url_login or "example.com" in cfg.url_login:
        faltantes.append("GALENIUS_URL_LOGIN")
    if not cfg.certificados_url:
        faltantes.append("GALENIUS_CERTIFICADOS_URL")
    if not cfg.queue_sheet_url:
        faltantes.append("GALENIUS_QUEUE_SHEET_URL")
    if not cfg.usuario:
        faltantes.append("GALENIUS_USERNAME")
    if not cfg.contrasena:
        faltantes.append("GALENIUS_PASSWORD")
    if faltantes:
        raise LoginGaleniusError(
            f"Configuracion incompleta de login Galenius: {faltantes}"
        )


def _detectar_error_login(page) -> str:
    mensajes = recolectar_textos_ui(page, LOGIN_ERROR_SELECTORS, max_por_selector=5)
    patrones = [
        "usuario",
        "contrasena",
        "incorrect",
        "inval",
        "credencial",
        "autentic",
        "intente nuevamente",
    ]
    for msg in mensajes:
        low = msg.lower()
        if any(p in low for p in patrones):
            return msg
    return ""


def _login_confirmado(page, cfg: GaleniusConfig) -> bool:
    url_actual = page.url.lower()
    for token in cfg.success_url_contains:
        if token.lower() in url_actual:
            return True

    try:
        if page.locator(SEL["certificados_dni_input"]).first.is_visible(timeout=350):
            return True
    except Exception:
        pass

    for sel in cfg.success_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=350):
                return True
        except Exception:
            continue

    try:
        if page.locator(SEL["login_form"]).first.is_visible(timeout=350):
            return False
    except Exception:
        return True
    return False


def _ejecutar_login(page, cfg: GaleniusConfig, logger, event_logger) -> str:
    page.goto(cfg.url_login, wait_until="domcontentloaded", timeout=cfg.timeout_ms)
    page.locator(SEL["login_form"]).wait_for(state="visible", timeout=cfg.timeout_ms)

    page.locator(SEL["username"]).fill(cfg.usuario)
    page.locator(SEL["password"]).fill(cfg.contrasena)

    with page.expect_navigation(wait_until="domcontentloaded", timeout=cfg.timeout_ms):
        page.locator(SEL["submit"]).click(timeout=cfg.timeout_ms)

    error = esperar_hasta(lambda: _detectar_error_login(page), timeout_ms=2200, sleep_ms=150)
    if error:
        event_logger.event("login_error", reason="invalid_credentials_or_ui", detail=error)
        raise LoginGaleniusError(f"Login rechazado por plataforma: {error}")

    ok = esperar_hasta(lambda: _login_confirmado(page, cfg), timeout_ms=cfg.timeout_ms, sleep_ms=180)
    if not ok:
        body_excerpt = ""
        try:
            body_excerpt = (page.locator("body").inner_text(timeout=900) or "")[:1200]
        except Exception:
            body_excerpt = ""

        msg = f"No se pudo confirmar login exitoso. URL actual: {page.url}"
        event_logger.event(
            "login_error",
            reason="not_confirmed",
            url=page.url,
            body_excerpt=body_excerpt,
        )
        raise LoginGaleniusError(msg)

    logger.info("[GALENIUS] Login exitoso | URL=%s", page.url)
    event_logger.event("login_ok", url=page.url)
    return page.url


def _normalizar_dni(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _estado_normalizado(value: str) -> str:
    return str(value or "").strip().upper()


def _crear_directorio_lote(cfg: GaleniusConfig) -> tuple[str, object]:
    fecha_lote = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
    lote_nombre = f"lote-{fecha_lote}"
    lote_dir = cfg.base_dir / "lotes" / lote_nombre
    lote_dir.mkdir(parents=True, exist_ok=True)
    return lote_nombre, lote_dir


def _prune_old_lote_dirs(lotes_root, max_lote_dirs: int) -> None:
    if max_lote_dirs <= 0 or not lotes_root.exists():
        return

    lote_dirs = [path for path in lotes_root.iterdir() if path.is_dir()]
    if len(lote_dirs) <= max_lote_dirs:
        return

    lote_dirs.sort(key=lambda path: path.stat().st_mtime)
    for path in lote_dirs[:-max_lote_dirs]:
        shutil.rmtree(path, ignore_errors=True)


def _marcar_fila(sheet_url: str, row_number: int, fieldnames: list[str], cfg: GaleniusConfig, estado: str, logger, dni: str, observacion: str = "") -> None:
    updates = {
        "ESTADO CERTIFICADO MEDICO": estado,
        "OBSERVACION CERTIFICADO MEDICO": observacion or ("DESCARGADO SIN OBSERVACIONES" if estado == cfg.estado_descargado else ""),
        "RESPONSABLE": cfg.responsable_default,
        "FECHA TRAMITE": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    update_sheet_row(sheet_url, row_number, updates, fieldnames=fieldnames)
    logger.info("[GALENIUS][%s] Hoja actualizada | fila=%s | estado=%s", dni, row_number, estado)


def _cargar_cola_documentos(cfg: GaleniusConfig, logger) -> tuple[list[dict], list[str]]:
    rows, fieldnames = read_google_sheet_rows(cfg.queue_sheet_url)
    columnas = resolve_sheet_columns(fieldnames)
    dni_col = columnas.get("dni") or "DNI"
    estado_col = columnas.get("estado_certificado_medico") or "ESTADO CERTIFICADO MEDICO"

    pendientes = []
    estados_finales = {cfg.estado_descargado.upper(), cfg.estado_error.upper(), cfg.estado_sin_resultados.upper()}
    for row in rows:
        dni = _normalizar_dni(row.get(dni_col, ""))
        estado = _estado_normalizado(row.get(estado_col, ""))
        if not dni:
            continue
        if estado in estados_finales:
            continue
        pendientes.append(row)

    logger.info(
        "[GALENIUS] Cola cargada | total_filas=%s | pendientes=%s | hoja=%s",
        len(rows),
        len(pendientes),
        cfg.queue_sheet_title,
    )
    return pendientes, fieldnames


def _procesar_registro_documental(page, cfg: GaleniusConfig, logger, event_logger, lote_dir, row: dict, fieldnames: list[str]) -> dict:
    columnas = resolve_sheet_columns(fieldnames)
    dni_col = columnas.get("dni") or "DNI"
    estado_col = columnas.get("estado_certificado_medico") or "ESTADO CERTIFICADO MEDICO"
    row_number = int(row.get("__row_number__", 0) or 0)
    dni = _normalizar_dni(row.get(dni_col, ""))
    if not dni:
        raise GaleniusFlowError(f"Fila {row_number} sin DNI valido")

    logger.info("[GALENIUS][%s] Iniciando tratamiento documental | fila=%s", dni, row_number)
    event_logger.event("document_start", dni=dni, row_number=row_number)

    _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_en_proceso, logger, dni)

    buscar_dni(page, dni, cfg)
    if detectar_sin_registros(page):
        observacion = "SIN REGISTROS EXISTENTES"
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_sin_resultados, logger, dni, observacion=observacion)
        event_logger.event("document_finish", dni=dni, row_number=row_number, status="sin_registros", observation=observacion)
        return {"dni": dni, "row_number": row_number, "status": "sin_registros", "descargado": False}

    resultados = leer_resultados_certificados(page)
    if not resultados:
        observacion = "SIN REGISTROS EXISTENTES"
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_sin_resultados, logger, dni, observacion=observacion)
        event_logger.event("document_finish", dni=dni, row_number=row_number, status="sin_registros")
        return {"dni": dni, "row_number": row_number, "status": "sin_registros", "descargado": False}

    seleccionado = elegir_resultado_mas_cercano(resultados)
    if seleccionado is None:
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_error, logger, dni)
        event_logger.event("document_finish", dni=dni, row_number=row_number, status="sin_pdf")
        return {"dni": dni, "row_number": row_number, "status": "sin_pdf", "descargado": False}

    logger.info(
        "[GALENIUS][%s] Resultado seleccionado | orden=%s | fecha=%s | paciente=%s | empresa=%s",
        dni,
        seleccionado.numero_orden,
        seleccionado.fecha_atencion,
        seleccionado.paciente,
        seleccionado.empresa,
    )

    archivo_local, detalle = descargar_pdf_resultado(page, cfg, seleccionado, dni, lote_dir)
    logger.info("[GALENIUS][%s] PDF guardado | local=%s | detalle=%s", dni, archivo_local, detalle)

    _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_descargado, logger, dni)
    event_logger.event(
        "document_finish",
        dni=dni,
        row_number=row_number,
        status="ok",
        local_path=str(archivo_local),
        fecha_atencion=seleccionado.fecha_atencion,
        numero_orden=seleccionado.numero_orden,
    )
    return {
        "dni": dni,
        "row_number": row_number,
        "status": "ok",
        "descargado": True,
        "local_path": str(archivo_local),
        "fecha_atencion": seleccionado.fecha_atencion,
        "numero_orden": seleccionado.numero_orden,
    }


def ejecutar_flujo_galenius(cfg: GaleniusConfig, run_dir, logger, event_logger) -> dict:
    """
    Script unico del flujo Galenius.
    Etapa actual implementada: cola documental BOT DOCUMENTOS + descarga local.
    """
    _validar_config_login(cfg)
    cfg.output_root.mkdir(parents=True, exist_ok=True)
    cfg.download_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=0)
        context = browser.new_context(no_viewport=True, ignore_https_errors=True, accept_downloads=True)
        page = context.new_page()
        try:
            logger.info("[GALENIUS] Inicio flujo unico | login=%s", cfg.url_login)
            event_logger.event("flow_start", stage="documentos", url=cfg.url_login, queue_sheet=cfg.queue_sheet_url)
            final_url = _ejecutar_login(page, cfg, logger, event_logger)
            abrir_vista_certificados(page, cfg)
            lote_nombre, lote_dir = _crear_directorio_lote(cfg)
            logger.info("[GALENIUS] Directorio de lote creado | lote=%s | ruta=%s", lote_nombre, lote_dir)
            event_logger.event("lote_start", lote=lote_nombre, lote_dir=str(lote_dir))
            _prune_old_lote_dirs(cfg.base_dir / "lotes", cfg.max_lote_dirs)

            pendientes, fieldnames = _cargar_cola_documentos(cfg, logger)
            resumen = {
                "descargados": 0,
                "sin_resultados": 0,
                "errores": 0,
                "final_url": final_url,
                "run_dir": str(run_dir),
                "lote_dir": str(lote_dir),
                "procesados": 0,
            }

            for row in pendientes:
                try:
                    resultado = _procesar_registro_documental(page, cfg, logger, event_logger, lote_dir, row, fieldnames)
                    resumen["procesados"] += 1
                    if resultado.get("descargado"):
                        resumen["descargados"] += 1
                    elif resultado.get("status") == "sin_resultados":
                        resumen["sin_resultados"] += 1
                except Exception as exc:
                    resumen["errores"] += 1
                    row_number = int(row.get("__row_number__", 0) or 0)
                    dni = _normalizar_dni(row.get("DNI", row.get("dni", "")))
                    try:
                        if row_number:
                            _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_error, logger, dni)
                    except Exception:
                        pass
                    logger.exception("[GALENIUS][%s] Error procesando fila %s", dni, row_number)
                    event_logger.event("document_error", dni=dni, row_number=row_number, detail=str(exc))

            event_logger.event("flow_finish", status="ok", **resumen)
            return resumen
        except PlaywrightTimeoutError as exc:
            event_logger.event("flow_error", reason="timeout", detail=str(exc))
            raise GaleniusFlowError(f"Timeout en flujo Galenius: {exc}") from exc
        finally:
            context.close()
            browser.close()
