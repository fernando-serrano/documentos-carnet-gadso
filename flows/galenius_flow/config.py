import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _as_bool(value: str, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in str(value or "").split(",") if x.strip()]


@dataclass
class GaleniusConfig:
    url_login: str
    certificados_url: str
    queue_sheet_url: str
    queue_sheet_title: str
    usuario: str
    contrasena: str
    headless: bool
    timeout_ms: int
    success_selectors: list[str]
    success_url_contains: list[str]
    base_dir: Path
    logs_root: Path
    output_root: Path
    download_dir: Path
    max_pdf_kb: int
    overwrite_existing: bool
    responsable_default: str
    estado_en_proceso: str
    estado_descargado: str
    estado_error: str
    estado_sin_resultados: str
    pdf_size_headroom_pct: float
    audit_max_run_dirs: int
    max_lote_dirs: int
    worker_count: int


def load_galenius_config() -> GaleniusConfig:
    base_dir = Path(__file__).resolve().parent.parent.parent

    url_login = str(
        os.getenv("GALENIUS_URL_LOGIN", "https://galenius.example.com/login")
    ).strip()
    certificados_url = str(
        os.getenv(
            "GALENIUS_CERTIFICADOS_URL",
            "https://autovias.galenius.com/7504e858-657f-4de8-89bf-3112dc65117b/armas/armas/certificados",
        )
    ).strip()
    queue_sheet_url = str(
        os.getenv(
            "GALENIUS_QUEUE_SHEET_URL",
            "https://docs.google.com/spreadsheets/d/1C-V6wNGXQEVfncbldOQfhDKT7Qwuk2BV6Y_gnV5-O4U/edit?gid=214579984#gid=214579984",
        )
    ).strip()
    queue_sheet_title = str(os.getenv("GALENIUS_QUEUE_SHEET_TITLE", "BOT DOCUMENTOS")).strip()
    usuario = str(os.getenv("GALENIUS_USERNAME", "")).strip()
    contrasena = str(os.getenv("GALENIUS_PASSWORD", "")).strip()

    headless = _as_bool(os.getenv("GALENIUS_HEADLESS", "1"), default=True)
    timeout_ms = max(
        3000,
        int(str(os.getenv("GALENIUS_TIMEOUT_MS", "30000") or "30000").strip()),
    )

    success_selectors = _split_csv(
        os.getenv(
            "GALENIUS_LOGIN_SUCCESS_SELECTORS",
            "#dashboard,.dashboard,#menu-principal,nav .logout,a[href*='logout'],#id_dni,div.table-responsive table.table-bordered.table-striped",
        )
    )
    success_url_contains = _split_csv(
        os.getenv(
            "GALENIUS_LOGIN_SUCCESS_URL_CONTAINS",
            "/dashboard,/inicio,/home,/armas/armas/certificados",
        )
    )

    logs_root = base_dir / str(os.getenv("GALENIUS_LOG_DIR", "logs/galenius")).strip()
    output_root = base_dir / str(os.getenv("GALENIUS_OUTPUT_DIR", "data/galenius")).strip()
    download_dir = output_root / "downloads"

    max_pdf_kb = max(
        50,
        int(str(os.getenv("GALENIUS_MAX_PDF_KB", "150") or "150").strip()),
    )
    overwrite_existing = _as_bool(os.getenv("GALENIUS_OVERWRITE_EXISTING", "0"), default=False)
    responsable_default = str(os.getenv("GALENIUS_RESPONSABLE_DEFAULT", "BOT DOCUMENTOS SUCAMEC")).strip()
    estado_en_proceso = str(os.getenv("GALENIUS_ESTADO_EN_PROCESO", "EN PROCESO")).strip()
    estado_descargado = str(os.getenv("GALENIUS_ESTADO_DESCARGADO", "DESCARGADO")).strip()
    estado_error = str(os.getenv("GALENIUS_ESTADO_ERROR", "ERROR")).strip()
    estado_sin_resultados = str(os.getenv("GALENIUS_ESTADO_SIN_RESULTADOS", "SIN REGISTROS")).strip()
    pdf_size_headroom_pct = float(str(os.getenv("GALENIUS_MAX_PDF_HEADROOM_PCT", "0.95") or "0.95").strip())
    pdf_size_headroom_pct = max(0.5, min(0.99, pdf_size_headroom_pct))
    audit_max_run_dirs = min(10, max(1, int(str(os.getenv("GALENIUS_AUDIT_MAX_RUN_DIRS", "10") or "10").strip())))
    max_lote_dirs = max(1, int(str(os.getenv("GALENIUS_MAX_LOTE_DIRS", "10") or "10").strip()))
    worker_count = max(1, min(4, int(str(os.getenv("GALENIUS_WORKERS", "4") or "4").strip())))

    return GaleniusConfig(
        url_login=url_login,
        certificados_url=certificados_url,
        queue_sheet_url=queue_sheet_url,
        queue_sheet_title=queue_sheet_title,
        usuario=usuario,
        contrasena=contrasena,
        headless=headless,
        timeout_ms=timeout_ms,
        success_selectors=success_selectors,
        success_url_contains=success_url_contains,
        base_dir=base_dir,
        logs_root=logs_root,
        output_root=output_root,
        download_dir=download_dir,
        max_pdf_kb=max_pdf_kb,
        overwrite_existing=overwrite_existing,
        responsable_default=responsable_default,
        estado_en_proceso=estado_en_proceso,
        estado_descargado=estado_descargado,
        estado_error=estado_error,
        estado_sin_resultados=estado_sin_resultados,
        pdf_size_headroom_pct=pdf_size_headroom_pct,
        audit_max_run_dirs=audit_max_run_dirs,
        max_lote_dirs=max_lote_dirs,
        worker_count=worker_count,
    )
