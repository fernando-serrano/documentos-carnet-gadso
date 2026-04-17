import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from dotenv import load_dotenv

from flows.dj_fut_flow.dj_fut_flow import cargar_fuente_dj_fut_por_dni, procesar_dj_fut_por_dni
from flows.dj_fut_flow.sheets import read_google_sheet_rows, resolve_sheet_columns, update_sheet_row
from flows.lotes_utils import prune_old_lote_dirs_global


load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "sí", "on"}


@dataclass
class DjFutConfig:
    base_dir: Path
    logs_root: Path
    lotes_root: Path
    queue_sheet_url: str
    source_sheet_url: str
    drive_credentials_json: str
    max_kb: int
    headroom_pct: float
    overwrite_existing: bool
    responsable_default: str
    estado_en_proceso: str
    estado_descargado: str
    estado_error: str
    estado_sin_registros: str
    worker_count: int
    max_lote_dirs: int
    strict_size_limit: bool
    allow_lossy: bool
    date_edit_required: bool


def load_dj_fut_config() -> DjFutConfig:
    base_dir = Path(__file__).resolve().parent
    logs_root = base_dir / str(os.getenv("DJ_FUT_LOG_DIR", "logs/dj_fut")).strip()
    lotes_root = base_dir / str(os.getenv("DJ_FUT_LOTES_DIR", "lotes")).strip()
    queue_sheet_url = str(os.getenv("DJ_FUT_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
    source_sheet_url = str(os.getenv("DJ_FUT_SOURCE_SHEET_URL", "")).strip()
    drive_credentials_json = str(os.getenv("DJ_FUT_DRIVE_CREDENTIALS_JSON", os.getenv("DRIVE_CREDENTIALS_JSON", ""))).strip()
    max_kb = max(20, int(str(os.getenv("DJ_FUT_MAX_KB", "80") or "80").strip()))
    headroom_pct = float(str(os.getenv("DJ_FUT_HEADROOM_PCT", "0.95") or "0.95").strip())
    headroom_pct = max(0.5, min(0.99, headroom_pct))
    overwrite_existing = _as_bool(os.getenv("DJ_FUT_OVERWRITE_EXISTING", "0"), default=False)
    responsable_default = str(os.getenv("DJ_FUT_RESPONSABLE_DEFAULT", os.getenv("GALENIUS_RESPONSABLE_DEFAULT", "BOT DOCUMENTOS SUCAMEC"))).strip()
    estado_en_proceso = str(os.getenv("DJ_FUT_ESTADO_EN_PROCESO", "EN PROCESO")).strip()
    estado_descargado = str(os.getenv("DJ_FUT_ESTADO_DESCARGADO", "DESCARGADO")).strip()
    estado_error = str(os.getenv("DJ_FUT_ESTADO_ERROR", "ERROR")).strip()
    estado_sin_registros = str(os.getenv("DJ_FUT_ESTADO_SIN_REGISTROS", "SIN REGISTROS")).strip()
    worker_count = max(1, min(4, int(str(os.getenv("DJ_FUT_WORKERS", "4") or "4").strip())))
    max_lote_dirs = max(1, int(str(os.getenv("DJ_FUT_MAX_LOTE_DIRS", os.getenv("GALENIUS_MAX_LOTE_DIRS", "10")) or "10").strip()))
    strict_size_limit = _as_bool(os.getenv("DJ_FUT_STRICT_SIZE_LIMIT", "0"), default=False)
    allow_lossy = _as_bool(os.getenv("DJ_FUT_ALLOW_LOSSY", "0"), default=False)
    date_edit_required = _as_bool(os.getenv("DJ_FUT_DATE_EDIT_REQUIRED", "1"), default=True)

    return DjFutConfig(
        base_dir=base_dir,
        logs_root=logs_root,
        lotes_root=lotes_root,
        queue_sheet_url=queue_sheet_url,
        source_sheet_url=source_sheet_url,
        drive_credentials_json=drive_credentials_json,
        max_kb=max_kb,
        headroom_pct=headroom_pct,
        overwrite_existing=overwrite_existing,
        responsable_default=responsable_default,
        estado_en_proceso=estado_en_proceso,
        estado_descargado=estado_descargado,
        estado_error=estado_error,
        estado_sin_registros=estado_sin_registros,
        worker_count=worker_count,
        max_lote_dirs=max_lote_dirs,
        strict_size_limit=strict_size_limit,
        allow_lossy=allow_lossy,
        date_edit_required=date_edit_required,
    )


def _marcar_fila_dj_fut(
    cfg: DjFutConfig,
    fieldnames: list[str],
    row_number: int,
    estado: str,
    observacion: str,
    logger,
) -> None:
    updates = {
        "ESTADO DJ FUT": estado,
        "OBSERVACION DJ FUT": observacion,
        "RESPONSABLE": cfg.responsable_default,
        "FECHA TRAMITE": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    update_sheet_row(cfg.queue_sheet_url, row_number, updates, fieldnames=fieldnames)
    logger.info(
        "[DJ FUT] Hoja actualizada | fila=%s | estado=%s | observacion=%s",
        row_number,
        estado,
        observacion,
    )


def _setup_logger(logs_root: Path) -> tuple[logging.Logger, Path]:
    logs_root.mkdir(parents=True, exist_ok=True)
    run_dir = logs_root / f"dj_fut_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prune_old_lote_dirs_global(logs_root, 10)

    logger = logging.getLogger(f"dj_fut_{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    handler = logging.FileHandler(run_dir / "dj_fut.log", encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    return logger, run_dir


def _resolver_lote_dir_compartido(base_dir: Path) -> Path | None:
    raw = str(os.getenv("GLOBAL_LOTE_DIR", "")).strip()
    if not raw:
        return None

    lote_dir = Path(raw)
    if not lote_dir.is_absolute():
        lote_dir = base_dir / lote_dir
    return lote_dir


def _worker_dj_fut(
    worker_id: int,
    cfg: DjFutConfig,
    fieldnames: list[str],
    dj_fut_source_map: dict[str, str],
    lote_dir: Path,
    tareas: Queue,
    logger,
) -> dict[str, int]:
    worker_tag = f"W{worker_id}"
    resumen = {"procesados": 0, "descargados": 0, "sin_registros": 0, "errores": 0}

    while True:
        try:
            dni, row_number = tareas.get_nowait()
        except Empty:
            break

        try:
            _marcar_fila_dj_fut(
                cfg,
                fieldnames,
                row_number,
                f"{cfg.estado_en_proceso} W{worker_id}",
                "",
                logger,
            )

            if dni not in dj_fut_source_map:
                resumen["sin_registros"] += 1
                logger.info("[DJ FUT][%s][%s] SIN COINCIDENCIA EN FUENTE", worker_tag, dni)
                _marcar_fila_dj_fut(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_sin_registros,
                    f"{dni} SIN DJ FUT EN FUENTE",
                    logger,
                )
                continue

            resultado = procesar_dj_fut_por_dni(
                dni=dni,
                dj_fut_source_map=dj_fut_source_map,
                credentials_path=cfg.drive_credentials_json,
                lote_dir=lote_dir,
                max_kb=cfg.max_kb,
                headroom_pct=cfg.headroom_pct,
                overwrite_existing=cfg.overwrite_existing,
                strict_size_limit=cfg.strict_size_limit,
                allow_lossy=cfg.allow_lossy,
                date_edit_required=cfg.date_edit_required,
            )
            resumen["procesados"] += 1
            if resultado.get("status") == "ok":
                resumen["descargados"] += 1
                logger.info("[DJ FUT][%s][%s] OK | %s", worker_tag, dni, resultado.get("local_path", ""))
                _marcar_fila_dj_fut(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_descargado,
                    str(resultado.get("observation", "DESCARGADO SIN OBSERVACIONES")),
                    logger,
                )
            elif resultado.get("status") == "sin_registros":
                resumen["sin_registros"] += 1
                logger.info("[DJ FUT][%s][%s] SIN REGISTROS | %s", worker_tag, dni, resultado.get("observation", ""))
                _marcar_fila_dj_fut(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_sin_registros,
                    str(resultado.get("observation", "")),
                    logger,
                )
            else:
                resumen["errores"] += 1
                logger.warning(
                    "[DJ FUT][%s][%s] ERROR | %s | %s",
                    worker_tag,
                    dni,
                    resultado.get("observation", ""),
                    resultado.get("detail", ""),
                )
                _marcar_fila_dj_fut(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_error,
                    str(resultado.get("observation", "ERROR DE DJ FUT")),
                    logger,
                )
        except Exception as exc:
            resumen["errores"] += 1
            logger.exception("[DJ FUT][%s][%s] Excepcion procesando DJ FUT: %s", worker_tag, dni, exc)
            try:
                _marcar_fila_dj_fut(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_error,
                    f"{dni} ERROR: {exc}",
                    logger,
                )
            except Exception:
                logger.exception("[DJ FUT][%s][%s] No se pudo actualizar estado de error en hoja", worker_tag, dni)

    logger.info("[DJ FUT][%s] Worker finalizado | resumen=%s", worker_tag, resumen)
    return resumen


def main() -> int:
    cfg = load_dj_fut_config()
    logger, run_dir = _setup_logger(cfg.logs_root)

    logger.info("[DJ FUT] Run dir: %s", run_dir)
    if not cfg.queue_sheet_url:
        logger.error("[DJ FUT] Falta DJ_FUT_QUEUE_SHEET_URL o GALENIUS_QUEUE_SHEET_URL en .env")
        return 2
    if not cfg.source_sheet_url:
        logger.error("[DJ FUT] Falta DJ_FUT_SOURCE_SHEET_URL en .env")
        return 2
    if not cfg.drive_credentials_json:
        logger.error("[DJ FUT] Falta DJ_FUT_DRIVE_CREDENTIALS_JSON o DRIVE_CREDENTIALS_JSON en .env")
        return 2

    cfg.lotes_root.mkdir(parents=True, exist_ok=True)
    lote_dir_compartido = _resolver_lote_dir_compartido(cfg.base_dir)
    if lote_dir_compartido is not None:
        lote_dir = lote_dir_compartido
        lote_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[DJ FUT] Usando lote compartido | lote=%s", lote_dir)
    else:
        lote_dir = cfg.lotes_root / f"lote-dj-fut-{datetime.now().strftime('%d-%m-%Y-%H-%M-%S')}"
        lote_dir.mkdir(parents=True, exist_ok=True)
    prune_old_lote_dirs_global(cfg.lotes_root, cfg.max_lote_dirs)

    logger.info("[DJ FUT] Cola source=%s | fuente=%s", cfg.queue_sheet_url, cfg.source_sheet_url)
    dj_fut_source_map = cargar_fuente_dj_fut_por_dni(cfg.source_sheet_url, logger)
    queue_rows, fieldnames = read_google_sheet_rows(cfg.queue_sheet_url)
    columnas = resolve_sheet_columns(fieldnames)
    dni_col = columnas.get("dni") or "DNI"

    queue_items: list[tuple[str, int]] = []
    for row in queue_rows:
        dni = str(row.get(dni_col, "") or "").strip()
        dni_digits = "".join(ch for ch in dni if ch.isdigit())
        row_number = int(row.get("__row_number__", 0) or 0)
        if dni_digits and row_number:
            queue_items.append((dni_digits, row_number))

    logger.info("[DJ FUT] Cola cargada | filas=%s | filas_validas=%s", len(queue_rows), len(queue_items))

    worker_count = min(cfg.worker_count, len(queue_items)) if queue_items else 0
    tareas: Queue = Queue()
    for item in queue_items:
        tareas.put(item)

    logger.info(
        "[DJ FUT] Procesamiento multihilo activado | workers=%s | filas_validas=%s",
        worker_count,
        len(queue_items),
    )

    procesados = 0
    descargados = 0
    sin_registros = 0
    errores = 0

    if worker_count > 0:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    _worker_dj_fut,
                    worker_id,
                    cfg,
                    fieldnames,
                    dj_fut_source_map,
                    lote_dir,
                    tareas,
                    logger,
                )
                for worker_id in range(1, worker_count + 1)
            ]
            for future in as_completed(futures):
                result = future.result()
                procesados += result.get("procesados", 0)
                descargados += result.get("descargados", 0)
                sin_registros += result.get("sin_registros", 0)
                errores += result.get("errores", 0)

    logger.info(
        "[DJ FUT] Flujo completado | workers=%s | procesados=%s | descargados=%s | sin_registros=%s | errores=%s | lote=%s",
        worker_count,
        procesados,
        descargados,
        sin_registros,
        errores,
        lote_dir,
    )

    return 0 if errores == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
