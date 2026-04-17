from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from pathlib import Path
import os

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from flows.lotes_utils import prune_old_lote_dirs_global
from .config import GaleniusConfig
from .logging_utils import setup_worker_logging
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
    lote_compartido_raw = str(os.getenv("GLOBAL_LOTE_DIR", "")).strip()
    if lote_compartido_raw:
        lote_dir = Path(lote_compartido_raw)
        if not lote_dir.is_absolute():
            lote_dir = cfg.base_dir / lote_dir
        lote_dir.mkdir(parents=True, exist_ok=True)
        return lote_dir.name, lote_dir

    fecha_lote = datetime.now().strftime("%d-%m-%Y-%H-%M-%S")
    lote_nombre = f"lote-{fecha_lote}"
    lote_dir = cfg.base_dir / "lotes" / lote_nombre
    lote_dir.mkdir(parents=True, exist_ok=True)
    return lote_nombre, lote_dir


def _preparar_sesion_autenticada(cfg: GaleniusConfig, logger, event_logger, run_dir) -> Path:
    storage_state_path = run_dir / "galenius_storage_state.json"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=0)
        context = browser.new_context(no_viewport=True, ignore_https_errors=True, accept_downloads=True)
        page = context.new_page()
        try:
            logger.info("[GALENIUS] Preparando sesion autenticada")
            _ejecutar_login(page, cfg, logger, event_logger)
            context.storage_state(path=str(storage_state_path))
            event_logger.event("session_ready", storage_state=str(storage_state_path))
            return storage_state_path
        finally:
            context.close()
            browser.close()


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
        if estado not in estados_finales:
            pendientes.append(row)

    logger.info(
        "[GALENIUS] Cola cargada | total_filas=%s | pendientes=%s | hoja=%s",
        len(rows),
        len(pendientes),
        cfg.queue_sheet_title,
    )
    return pendientes, fieldnames


def _procesar_registro_documental(
    page,
    cfg: GaleniusConfig,
    logger,
    event_logger,
    lote_dir,
    row: dict,
    fieldnames: list[str],
    worker_id: int | None = None,
) -> dict:
    columnas = resolve_sheet_columns(fieldnames)
    dni_col = columnas.get("dni") or "DNI"
    row_number = int(row.get("__row_number__", 0) or 0)
    dni = _normalizar_dni(row.get(dni_col, ""))
    if not dni:
        raise GaleniusFlowError(f"Fila {row_number} sin DNI valido")

    worker_tag = f"W{worker_id}" if worker_id else "W?"
    logger.info("[GALENIUS][%s][%s] Iniciando tratamiento documental | fila=%s", worker_tag, dni, row_number)
    event_logger.event("document_start", worker=worker_id, dni=dni, row_number=row_number)

    estado_en_proceso = f"{cfg.estado_en_proceso} W{worker_id}" if worker_id else cfg.estado_en_proceso
    _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, estado_en_proceso, logger, dni)

    buscar_dni(page, dni, cfg)
    if detectar_sin_registros(page):
        observacion = f"{dni} SIN REGISTRO EXISTENTE"
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_sin_resultados, logger, dni, observacion=observacion)
        event_logger.event("document_finish", worker=worker_id, dni=dni, row_number=row_number, status="sin_registros", observation=observacion)
        return {"dni": dni, "row_number": row_number, "status": "sin_registros", "descargado": False}

    resultados = leer_resultados_certificados(page)
    if not resultados:
        observacion = f"{dni} SIN REGISTRO EXISTENTE"
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_sin_resultados, logger, dni, observacion=observacion)
        event_logger.event("document_finish", worker=worker_id, dni=dni, row_number=row_number, status="sin_registros", observation=observacion)
        return {"dni": dni, "row_number": row_number, "status": "sin_registros", "descargado": False}

    seleccionado = elegir_resultado_mas_cercano(resultados)
    if seleccionado is None:
        _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_error, logger, dni, observacion=f"{dni} PDF NO DISPONIBLE")
        event_logger.event("document_finish", worker=worker_id, dni=dni, row_number=row_number, status="sin_pdf")
        return {"dni": dni, "row_number": row_number, "status": "sin_pdf", "descargado": False}

    logger.info(
        "[GALENIUS][%s][%s] Resultado seleccionado | orden=%s | fecha=%s | paciente=%s | empresa=%s",
        worker_tag,
        dni,
        seleccionado.numero_orden,
        seleccionado.fecha_atencion,
        seleccionado.paciente,
        seleccionado.empresa,
    )

    archivo_local, detalle = descargar_pdf_resultado(page, cfg, seleccionado, dni, lote_dir)
    logger.info("[GALENIUS][%s][%s] PDF guardado | local=%s | detalle=%s", worker_tag, dni, archivo_local, detalle)

    _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_descargado, logger, dni)
    event_logger.event(
        "document_finish",
        worker=worker_id,
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


def _procesar_cola_worker(worker_id: int, cfg: GaleniusConfig, logger, event_logger, lote_dir, run_dir, storage_state_path: Path, tareas: Queue, fieldnames: list[str]) -> dict:
    worker_tag = f"W{worker_id}"
    resumen = {"procesados": 0, "descargados": 0, "sin_resultados": 0, "errores": 0}
    worker_logger, worker_dir = setup_worker_logging(run_dir, worker_id)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=0)
        context = browser.new_context(
            storage_state=str(storage_state_path),
            no_viewport=True,
            ignore_https_errors=True,
            accept_downloads=True,
        )
        page = context.new_page()
        try:
            logger.info("[GALENIUS][%s] Worker iniciado | log_dir=%s", worker_tag, worker_dir)
            worker_logger.info("[GALENIUS][%s] Worker iniciado", worker_tag)
            abrir_vista_certificados(page, cfg)

            while True:
                try:
                    row = tareas.get_nowait()
                except Empty:
                    break

                try:
                    resultado = _procesar_registro_documental(
                        page,
                        cfg,
                        worker_logger,
                        event_logger,
                        lote_dir,
                        row,
                        fieldnames,
                        worker_id=worker_id,
                    )
                    resumen["procesados"] += 1
                    if resultado.get("descargado"):
                        resumen["descargados"] += 1
                    elif resultado.get("status") == "sin_registros":
                        resumen["sin_resultados"] += 1
                except Exception as exc:
                    resumen["errores"] += 1
                    row_number = int(row.get("__row_number__", 0) or 0)
                    dni = _normalizar_dni(row.get("DNI", row.get("dni", "")))
                    try:
                        if row_number:
                            _marcar_fila(cfg.queue_sheet_url, row_number, fieldnames, cfg, cfg.estado_error, worker_logger, dni, observacion=f"{dni} ERROR DE TRATAMIENTO")
                    except Exception:
                        pass
                    worker_logger.exception("[GALENIUS][%s][%s] Error procesando fila %s", worker_tag, dni, row_number)
                    event_logger.event("document_error", worker=worker_id, dni=dni, row_number=row_number, detail=str(exc))

            worker_logger.info("[GALENIUS][%s] Worker finalizado | resumen=%s", worker_tag, resumen)
            event_logger.event("worker_finish", worker=worker_id, **resumen)
            return resumen
        finally:
            context.close()
            browser.close()


def ejecutar_flujo_galenius(cfg: GaleniusConfig, run_dir, logger, event_logger) -> dict:
    """
    Script unico del flujo Galenius.
    Etapa actual implementada: cola documental BOT DOCUMENTOS + descarga local.
    """
    _validar_config_login(cfg)
    cfg.output_root.mkdir(parents=True, exist_ok=True)
    cfg.download_dir.mkdir(parents=True, exist_ok=True)

    logger.info("[GALENIUS] Inicio flujo unico | login=%s", cfg.url_login)
    event_logger.event("flow_start", stage="documentos", url=cfg.url_login, queue_sheet=cfg.queue_sheet_url)

    lote_nombre, lote_dir = _crear_directorio_lote(cfg)
    logger.info("[GALENIUS] Directorio de lote creado | lote=%s | ruta=%s", lote_nombre, lote_dir)
    event_logger.event("lote_start", lote=lote_nombre, lote_dir=str(lote_dir))
    prune_old_lote_dirs_global(cfg.base_dir / "lotes", cfg.max_lote_dirs)

    pendientes, fieldnames = _cargar_cola_documentos(cfg, logger)
    if not pendientes:
        resumen_vacio = {
            "descargados": 0,
            "sin_resultados": 0,
            "errores": 0,
            "final_url": cfg.certificados_url,
            "run_dir": str(run_dir),
            "lote_dir": str(lote_dir),
            "procesados": 0,
            "workers": 0,
        }
        event_logger.event("flow_finish", status="ok", **resumen_vacio)
        return resumen_vacio

    storage_state_path = _preparar_sesion_autenticada(cfg, logger, event_logger, run_dir)

    worker_count = min(cfg.worker_count, len(pendientes))
    tareas = Queue()
    for row in pendientes:
        tareas.put(row)

    resumen = {
        "descargados": 0,
        "sin_resultados": 0,
        "errores": 0,
        "final_url": cfg.certificados_url,
        "run_dir": str(run_dir),
        "lote_dir": str(lote_dir),
        "procesados": 0,
        "workers": worker_count,
    }

    logger.info("[GALENIUS] Procesamiento multihilo activado | workers=%s | pendientes=%s", worker_count, len(pendientes))
    event_logger.event("workers_start", workers=worker_count, pendientes=len(pendientes))

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [
            executor.submit(
                _procesar_cola_worker,
                worker_id,
                cfg,
                logger,
                event_logger,
                lote_dir,
                run_dir,
                storage_state_path,
                tareas,
                fieldnames,
            )
            for worker_id in range(1, worker_count + 1)
        ]
        for future in as_completed(futures):
            worker_resumen = future.result()
            resumen["procesados"] += worker_resumen.get("procesados", 0)
            resumen["descargados"] += worker_resumen.get("descargados", 0)
            resumen["sin_resultados"] += worker_resumen.get("sin_resultados", 0)
            resumen["errores"] += worker_resumen.get("errores", 0)

    try:
        storage_state_path.unlink(missing_ok=True)
    except Exception:
        pass

    event_logger.event("flow_finish", status="ok", **resumen)
    return resumen
