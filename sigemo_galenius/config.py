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


def load_galenius_config() -> GaleniusConfig:
    base_dir = Path(__file__).resolve().parent.parent

    url_login = str(
        os.getenv("GALENIUS_URL_LOGIN", "https://galenius.example.com/login")
    ).strip()
    usuario = str(os.getenv("GALENIUS_USERNAME", "")).strip()
    contrasena = str(os.getenv("GALENIUS_PASSWORD", "")).strip()

    headless = _as_bool(os.getenv("GALENIUS_HEADLESS", "0"), default=False)
    timeout_ms = max(
        3000,
        int(str(os.getenv("GALENIUS_TIMEOUT_MS", "30000") or "30000").strip()),
    )

    success_selectors = _split_csv(
        os.getenv(
            "GALENIUS_LOGIN_SUCCESS_SELECTORS",
            "#dashboard, .dashboard, #menu-principal, nav .logout, a[href*='logout']",
        )
    )
    success_url_contains = _split_csv(
        os.getenv(
            "GALENIUS_LOGIN_SUCCESS_URL_CONTAINS",
            "/dashboard,/inicio,/home",
        )
    )

    logs_root = base_dir / str(os.getenv("GALENIUS_LOG_DIR", "logs/galenius")).strip()
    output_root = base_dir / str(os.getenv("GALENIUS_OUTPUT_DIR", "data/galenius")).strip()
    download_dir = output_root / "downloads"

    max_pdf_kb = max(
        50,
        int(str(os.getenv("GALENIUS_MAX_PDF_KB", "150") or "150").strip()),
    )

    return GaleniusConfig(
        url_login=url_login,
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
    )
