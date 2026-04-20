import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue

from dotenv import load_dotenv

from flows.lotes_utils import prune_old_lote_dirs_global
from flows.photo_carne_flow.photo_flow import cargar_fuente_foto_por_dni, procesar_foto_carne_por_dni
from flows.photo_carne_flow.sheets import read_google_sheet_rows, resolve_sheet_columns, update_sheet_row


load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "sí", "on"}


@dataclass
class FotoCarneConfig:
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
    min_jpeg_quality: int
    max_jpeg_oversize_pct: float


def load_foto_carne_config() -> FotoCarneConfig:
    base_dir = Path(__file__).resolve().parent
    logs_root = base_dir / str(os.getenv("FOTO_CARNE_LOG_DIR", "logs/foto_carne")).strip()
    lotes_root = base_dir / str(os.getenv("FOTO_CARNE_LOTES_DIR", "lotes")).strip()
    queue_sheet_url = str(os.getenv("FOTO_CARNE_QUEUE_SHEET_URL", os.getenv("GALENIUS_QUEUE_SHEET_URL", ""))).strip()
    source_sheet_url = str(os.getenv("FOTO_CARNE_SOURCE_SHEET_URL", "")).strip()
    drive_credentials_json = str(os.getenv("FOTO_CARNE_DRIVE_CREDENTIALS_JSON", os.getenv("DRIVE_CREDENTIALS_JSON", ""))).strip()
    max_kb = max(20, int(str(os.getenv("FOTO_CARNE_MAX_KB", "80") or "80").strip()))
    headroom_pct = float(str(os.getenv("FOTO_CARNE_HEADROOM_PCT", "0.95") or "0.95").strip())
    headroom_pct = max(0.5, min(0.99, headroom_pct))
    overwrite_existing = _as_bool(os.getenv("FOTO_CARNE_OVERWRITE_EXISTING", "0"), default=False)
    responsable_default = str(os.getenv("FOTO_CARNE_RESPONSABLE_DEFAULT", os.getenv("GALENIUS_RESPONSABLE_DEFAULT", "BOT DOCUMENTOS SUCAMEC"))).strip()
    estado_en_proceso = str(os.getenv("FOTO_CARNE_ESTADO_EN_PROCESO", "EN PROCESO")).strip()
    estado_descargado = str(os.getenv("FOTO_CARNE_ESTADO_DESCARGADO", "DESCARGADO")).strip()
    estado_error = str(os.getenv("FOTO_CARNE_ESTADO_ERROR", "ERROR")).strip()
    estado_sin_registros = str(os.getenv("FOTO_CARNE_ESTADO_SIN_REGISTROS", "SIN REGISTROS")).strip()
    worker_count = max(1, min(4, int(str(os.getenv("FOTO_CARNE_WORKERS", "4") or "4").strip())))
    max_lote_dirs = max(1, int(str(os.getenv("FOTO_CARNE_MAX_LOTE_DIRS", os.getenv("GALENIUS_MAX_LOTE_DIRS", "10")) or "10").strip()))
    min_jpeg_quality = max(30, min(80, int(str(os.getenv("FOTO_CARNE_MIN_JPEG_QUALITY", "50") or "50").strip())))
    max_jpeg_oversize_pct = float(str(os.getenv("FOTO_CARNE_MAX_JPEG_OVERSIZE_PCT", "1.15") or "1.15").strip())
    max_jpeg_oversize_pct = max(1.0, min(1.4, max_jpeg_oversize_pct))

    return FotoCarneConfig(
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
        min_jpeg_quality=min_jpeg_quality,
        max_jpeg_oversize_pct=max_jpeg_oversize_pct,
    )


def _marcar_fila_foto_carne(
    cfg: FotoCarneConfig,
    fieldnames: list[str],
    row_number: int,
    estado: str,
    observacion: str,
    logger,
) -> None:
    updates = {
        "ESTADO FOTO CARNÉ": estado,
        "OBSERVACION FOTO CARNÉ": observacion,
        "RESPONSABLE": cfg.responsable_default,
        "FECHA TRAMITE": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
    }
    update_sheet_row(cfg.queue_sheet_url, row_number, updates, fieldnames=fieldnames)
    logger.info(
        "[FOTO CARNE] Hoja actualizada | fila=%s | estado=%s | observacion=%s",
        row_number,
        estado,
        observacion,
    )


def _setup_logger(logs_root: Path) -> tuple[logging.Logger, Path]:
    logs_root.mkdir(parents=True, exist_ok=True)
    run_dir = logs_root / f"foto_carne_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    prune_old_lote_dirs_global(logs_root, 10)

    logger = logging.getLogger(f"foto_carne_{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if logger.handlers:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", "%Y-%m-%d %H:%M:%S")
    handler = logging.FileHandler(run_dir / "foto_carne.log", encoding="utf-8")
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


def _worker_foto_carne(
    worker_id: int,
    cfg: FotoCarneConfig,
    fieldnames: list[str],
    foto_source_map: dict[str, str],
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
            _marcar_fila_foto_carne(
                cfg,
                fieldnames,
                row_number,
                f"{cfg.estado_en_proceso} W{worker_id}",
                "",
                logger,
            )

            if dni not in foto_source_map:
                resumen["sin_registros"] += 1
                logger.info("[FOTO CARNE][%s][%s] SIN COINCIDENCIA EN FUENTE", worker_tag, dni)
                _marcar_fila_foto_carne(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_sin_registros,
                    f"{dni} SIN CARGAR FOTO EN FUENTE",
                    logger,
                )
                continue

            resultado = procesar_foto_carne_por_dni(
                dni=dni,
                foto_source_map=foto_source_map,
                credentials_path=cfg.drive_credentials_json,
                lote_dir=lote_dir,
                max_kb=cfg.max_kb,
                headroom_pct=cfg.headroom_pct,
                overwrite_existing=cfg.overwrite_existing,
                min_jpeg_quality=cfg.min_jpeg_quality,
                max_jpeg_oversize_pct=cfg.max_jpeg_oversize_pct,
            )
            resumen["procesados"] += 1
            if resultado.get("status") == "ok":
                resumen["descargados"] += 1
                logger.info("[FOTO CARNE][%s][%s] OK | %s", worker_tag, dni, resultado.get("local_path", ""))
                _marcar_fila_foto_carne(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_descargado,
                    str(resultado.get("observation", "DESCARGADO SIN OBSERVACIONES")),
                    logger,
                )
            elif resultado.get("status") == "sin_registros":
                resumen["sin_registros"] += 1
                logger.info("[FOTO CARNE][%s][%s] SIN REGISTROS | %s", worker_tag, dni, resultado.get("observation", ""))
                _marcar_fila_foto_carne(
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
                    "[FOTO CARNE][%s][%s] ERROR | %s | %s",
                    worker_tag,
                    dni,
                    resultado.get("observation", ""),
                    resultado.get("detail", ""),
                )
                _marcar_fila_foto_carne(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_error,
                    str(resultado.get("observation", "ERROR DE TRATAMIENTO")),
                    logger,
                )
        except Exception as exc:
            resumen["errores"] += 1
            logger.exception("[FOTO CARNE][%s][%s] Excepcion procesando foto: %s", worker_tag, dni, exc)
            try:
                _marcar_fila_foto_carne(
                    cfg,
                    fieldnames,
                    row_number,
                    cfg.estado_error,
                    f"{dni} ERROR: {exc}",
                    logger,
                )
            except Exception:
                logger.exception("[FOTO CARNE][%s][%s] No se pudo actualizar estado de error en hoja", worker_tag, dni)

    logger.info("[FOTO CARNE][%s] Worker finalizado | resumen=%s", worker_tag, resumen)
    return resumen


def main() -> int:
    cfg = load_foto_carne_config()
    logger, run_dir = _setup_logger(cfg.logs_root)

    logger.info("[FOTO CARNE] Run dir: %s", run_dir)
    if not cfg.queue_sheet_url:
        logger.error("[FOTO CARNE] Falta FOTO_CARNE_QUEUE_SHEET_URL o GALENIUS_QUEUE_SHEET_URL en .env")
        return 2
    if not cfg.source_sheet_url:
        logger.error("[FOTO CARNE] Falta FOTO_CARNE_SOURCE_SHEET_URL en .env")
        return 2
    if not cfg.drive_credentials_json:
        logger.error("[FOTO CARNE] Falta FOTO_CARNE_DRIVE_CREDENTIALS_JSON o DRIVE_CREDENTIALS_JSON en .env")
        return 2

    cfg.lotes_root.mkdir(parents=True, exist_ok=True)
    lote_dir_compartido = _resolver_lote_dir_compartido(cfg.base_dir)
    if lote_dir_compartido is not None:
        lote_dir = lote_dir_compartido
        lote_dir.mkdir(parents=True, exist_ok=True)
        logger.info("[FOTO CARNE] Usando lote compartido | lote=%s", lote_dir)
    else:
        lote_dir = cfg.lotes_root / f"lote-foto-carne-{datetime.now().strftime('%d-%m-%Y-%H-%M-%S')}"
        lote_dir.mkdir(parents=True, exist_ok=True)
    prune_old_lote_dirs_global(cfg.lotes_root, cfg.max_lote_dirs)

    logger.info("[FOTO CARNE] Cola source=%s | fuente=%s", cfg.queue_sheet_url, cfg.source_sheet_url)
    foto_source_map = cargar_fuente_foto_por_dni(cfg.source_sheet_url, logger)
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

    logger.info("[FOTO CARNE] Cola cargada | filas=%s | filas_validas=%s", len(queue_rows), len(queue_items))

    worker_count = min(cfg.worker_count, len(queue_items)) if queue_items else 0
    tareas: Queue = Queue()
    for item in queue_items:
        tareas.put(item)

    logger.info(
        "[FOTO CARNE] Procesamiento multihilo activado | workers=%s | filas_validas=%s",
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
                    _worker_foto_carne,
                    worker_id,
                    cfg,
                    fieldnames,
                    foto_source_map,
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
        "[FOTO CARNE] Flujo completado | workers=%s | procesados=%s | descargados=%s | sin_registros=%s | errores=%s | lote=%s",
        worker_count,
        procesados,
        descargados,
        sin_registros,
        errores,
        lote_dir,
    )
    return 0 if errores == 0 else 1


if __name__ == "__main__":
    exit_code = main()
    logging.shutdown()
    # Algunas dependencias de imagen/IA pueden dejar hilos nativos vivos en ciertos equipos.
    # Forzamos la salida para que run.bat recupere el control y continue con DJ FUT.
    os._exit(exit_code)
