import os
import queue
import re
import shutil
import json
import subprocess
import sys
import time
import csv
import io
import mimetypes
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import logging
from http.client import IncompleteRead
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import unicodedata
import importlib

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def _resolve_dir_from_env(env_name: str, default_dir: Path) -> Path:
    raw = str(os.getenv(env_name, "") or "").strip()
    if not raw:
        return default_dir
    p = Path(raw)
    return p if p.is_absolute() else (BASE_DIR / p)


LOGS_DIR = _resolve_dir_from_env("LOG_DIR", BASE_DIR / "logs")
DATA_DIR = BASE_DIR / "data"
TEST_DIR = BASE_DIR / "test"
CACHE_DIR = BASE_DIR / "__pycache__"

URL_LOGIN = os.getenv(
    "CARNET_URL_LOGIN",
    "https://www.sucamec.gob.pe/sel/faces/login.xhtml?faces-redirect=true",
).strip()

DEFAULT_GSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/1I5CmLAxZxA5gIlHz_m5kDPTaHIs1yOEWvy7tN4Ia2hs/"
    "edit?pli=1&gid=999619533#gid=999619533"
)

DEFAULT_GSHEET_COMPARE_URL = ""
DEFAULT_GSHEET_THIRD_URL = ""
DEFAULT_DRIVE_ROOT_FOLDER_ID = ""
DEFAULT_DRIVE_CREDENTIALS_JSON = ""
DEFAULT_DRIVE_VALIDATE_ON_START = "0"

CREDENCIALES_JV = {
    "tipo_documento_valor": os.getenv("CARNET_TIPO_DOC", os.getenv("TIPO_DOC", "RUC")).strip(),
    "numero_documento": os.getenv("CARNET_NUMERO_DOCUMENTO", os.getenv("NUMERO_DOCUMENTO", "")).strip(),
    "usuario": os.getenv("CARNET_USUARIO_SEL", os.getenv("USUARIO_SEL", "")).strip(),
    "contrasena": os.getenv("CARNET_CLAVE_SEL", os.getenv("CLAVE_SEL", "")).strip(),
}

CREDENCIALES_SELVA = {
    "tipo_documento_valor": os.getenv("CARNET_SELVA_TIPO_DOC", os.getenv("SELVA_TIPO_DOC", "RUC")).strip(),
    "numero_documento": os.getenv("CARNET_SELVA_NUMERO_DOCUMENTO", os.getenv("SELVA_NUMERO_DOCUMENTO", "")).strip(),
    "usuario": os.getenv("CARNET_SELVA_USUARIO_SEL", os.getenv("SELVA_USUARIO_SEL", "")).strip(),
    "contrasena": os.getenv("CARNET_SELVA_CLAVE_SEL", os.getenv("SELVA_CLAVE_SEL", "")).strip(),
}

SEL = {
    "tab_tradicional": '#tabViewLogin a[href^="#tabViewLogin:j_idt"]:has-text("Autenticación Tradicional"), #tabViewLogin a:has-text("Autenticación Tradicional"), #tabViewLogin a:has-text("Autenticacion Tradicional")',
    "tipo_doc_select": "#tabViewLogin\\:tradicionalForm\\:tipoDoc_input",
    "numero_documento": "#tabViewLogin\\:tradicionalForm\\:documento",
    "usuario": "#tabViewLogin\\:tradicionalForm\\:usuario",
    "clave": "#tabViewLogin\\:tradicionalForm\\:clave",
    "captcha_img": "#tabViewLogin\\:tradicionalForm\\:imgCaptcha",
    "captcha_input": "#tabViewLogin\\:tradicionalForm\\:textoCaptcha",
    "boton_refresh": "#tabViewLogin\\:tradicionalForm\\:botonCaptcha",
    "ingresar": "#tabViewLogin\\:tradicionalForm\\:ingresar",
    "menu_root": "#j_idt11\\:menuPrincipal, #j_idt11\\:menuprincipal",
    "menu_header_dssp": '.ui-panelmenu-header:has(a:text-is("DSSP")), .ui-panelmenu-header:has(a:has-text("DSSP"))',
    "menu_item_carne": '.ui-menuitem-link:has(span.ui-menuitem-text:text-is("CARNÉ")), .ui-menuitem-link:has(span.ui-menuitem-text:text-is("CARNE")), .ui-menuitem-link:has(span.ui-menuitem-text:has-text("CARN"))',
    "menu_item_crear_solicitud": '.ui-menuitem-link:has(span.ui-menuitem-text:text-is("CREAR SOLICITUD")), .ui-menuitem-link:has(span.ui-menuitem-text:has-text("CREAR SOLICITUD"))',
    "menu_item_crear_solicitud_onclick": 'a[onclick*="addSubmitParam"][onclick*="j_idt11:menuprincipal"]:has(span.ui-menuitem-text:text-is("CREAR SOLICITUD")), a[onclick*="addSubmitParam"][onclick*="j_idt11:menuPrincipal"]:has(span.ui-menuitem-text:text-is("CREAR SOLICITUD"))',
    "menu_item_bandeja_emision": '.ui-menuitem-link:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISIÓN")), .ui-menuitem-link:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISION")), .ui-menuitem-link:has(span.ui-menuitem-text:has-text("BANDEJA DE EMIS"))',
    "menu_item_bandeja_emision_onclick": 'a[onclick*="addSubmitParam"][onclick*="j_idt11:menuprincipal"]:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISIÓN")), a[onclick*="addSubmitParam"][onclick*="j_idt11:menuprincipal"]:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISION")), a[onclick*="addSubmitParam"][onclick*="j_idt11:menuPrincipal"]:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISIÓN")), a[onclick*="addSubmitParam"][onclick*="j_idt11:menuPrincipal"]:has(span.ui-menuitem-text:text-is("BANDEJA DE EMISION"))',
    "crear_solicitud_sede_trigger": '#createForm\\:dondeRecoger .ui-selectonemenu-trigger',
    "crear_solicitud_sede_label": '#createForm\\:dondeRecoger_label',
    "crear_solicitud_sede_panel": '#createForm\\:dondeRecoger_panel',
    "crear_solicitud_modalidad_trigger": '#createForm\\:modalidad .ui-selectonemenu-trigger',
    "crear_solicitud_modalidad_label": '#createForm\\:modalidad_label',
    "crear_solicitud_modalidad_panel": '#createForm\\:modalidad_panel',
    "crear_solicitud_tipo_registro_trigger": '#createForm\\:tipoRegistro .ui-selectonemenu-trigger',
    "crear_solicitud_tipo_registro_label": '#createForm\\:tipoRegistro_label',
    "crear_solicitud_tipo_registro_panel": '#createForm\\:tipoRegistro_panel',
    "crear_solicitud_tipo_doc_trigger": '#createForm\\:tipoDoc .ui-selectonemenu-trigger',
    "crear_solicitud_tipo_doc_label": '#createForm\\:tipoDoc_label',
    "crear_solicitud_tipo_doc_panel": '#createForm\\:tipoDoc_panel',
    "crear_solicitud_nombres_input": "#createForm\\:nombres",
    "crear_solicitud_ape_pat_input": "#createForm\\:apePat",
    "crear_solicitud_ape_mat_input": "#createForm\\:apeMat",
    "crear_solicitud_nro_secuencia_input": '#createForm\\:nroSecuencia',
    "crear_solicitud_verificar_recibo_button": '#createForm\\:btnBuscarRecibo',
    "crear_solicitud_documento_input": "#createForm\\:numDoc",
    "crear_solicitud_buscar_button": "#createForm\\:btnBuscarVigilante",
    "crear_solicitud_foto_input": "#createForm\\:idFoto_input",
    "crear_solicitud_djfut_input": "#createForm\\:archivoDJ_input",
    "crear_solicitud_djfut_label": "#createForm\\:archivoDJ_label",
    "crear_solicitud_djfut_container": "#createForm\\:archivoDJ",
    "crear_solicitud_certificado_medico_input": "#createForm\\:certificadoMedico_input",
    "crear_solicitud_certificado_medico_label": "#createForm\\:certificadoMedico_label",
    "crear_solicitud_certificado_medico_container": "#createForm\\:certificadoMedico",
    "crear_solicitud_guardar_button": "#createForm\\:botonGuardar",
    "bandeja_estado_trigger": '#listForm\\:tipoFormacion .ui-selectonemenu-trigger',
    "bandeja_estado_label": "#listForm\\:tipoFormacion_label",
    "bandeja_estado_panel": "#listForm\\:tipoFormacion_panel",
    "bandeja_buscar_button": '#listForm\\:j_idt56, #listForm\\:btnBuscar, #listForm\\:botonBuscar, #listForm\\:buscar, button[id*="listForm"][id*="Buscar"]',
    "bandeja_resultados_tbody": "#listForm\\:dtResultados_data",
    "bandeja_select_all_checkbox": "#listForm\\:dtResultados .ui-chkbox-all .ui-chkbox-box, #listForm\\:dtResultados_head .ui-chkbox-all .ui-chkbox-box",
    "bandeja_transmitir_button": '#listForm\\:j_idt67, button[id*="listForm"][id*="j_idt67"], button:has-text("Transmitir")',
    "bandeja_transmitir_confirm_dialog": "#dlgCompletarProceso",
    "bandeja_transmitir_confirm_button": '#frmCompletarProceso\\:j_idt418, #dlgCompletarProceso button#frmCompletarProceso\\:j_idt418, #dlgCompletarProceso button:has(span.ui-button-text:text-is("Transmitir"))',
}

SUCCESS_SELECTORS = [
    "#j_idt11\\:menuPrincipal",
    "#j_idt11\\:j_idt18",
    "form#gestionCitasForm",
]

ERROR_SELECTORS = [
    ".ui-messages-error",
    ".ui-message-error",
    ".ui-growl-message-error",
    ".mensajeError",
    "[class*='error']",
    "[class*='Error']",
]


SEDES_SUCAMEC_DISPONIBLES = [
    "LA LIBERTAD",
    "CAJAMARCA",
    "CHICLAYO",
    "ANCASH",
    "AREQUIPA",
    "TACNA",
    "CUSCO",
    "LIMA",
    "JUNIN",
    "PIURA",
    "ICA",
    "PUNO",
    "LORETO",
]

DEPARTAMENTO_A_SEDE = {
    "LIMA": "LIMA",
    "CALLAO": "LIMA",
    "ICA": "ICA",
    "PIURA": "PIURA",
    "TUMBES": "PIURA",
    "LA LIBERTAD": "LA LIBERTAD",
    "LALIBERTAD": "LA LIBERTAD",
    "TRUJILLO": "LA LIBERTAD",
    "CAJAMARCA": "CAJAMARCA",
    "CHICLAYO": "CHICLAYO",
    "LAMBAYEQUE": "CHICLAYO",
    "ANCASH": "ANCASH",
    "AREQUIPA": "AREQUIPA",
    "MOQUEGUA": "TACNA",
    "TACNA": "TACNA",
    "CUSCO": "CUSCO",
    "MADRE DE DIOS": "CUSCO",
    "JUNIN": "JUNIN",
    "HUANCAVELICA": "JUNIN",
    "PASCO": "JUNIN",
    "AYACUCHO": "JUNIN",
    "PUNO": "PUNO",
    "LORETO": "LORETO",
    "AMAZONAS": "LORETO",
    "SAN MARTIN": "LORETO",
    "UCAYALI": "LORETO",
}

DEPARTAMENTO_FALLBACK_POR_REGION = {
    "AYACUCHO": "JUNIN",
    "APURIMAC": "CUSCO",
    "HUANCAVELICA": "JUNIN",
    "HUANUCO": "JUNIN",
    "AMAZONAS": "CAJAMARCA",
    "SAN MARTIN": "LORETO",
    "UCAYALI": "LORETO",
    "MADRE DE DIOS": "CUSCO",
    "PASCO": "JUNIN",
}

SEDE_COORDS = {
    "LIMA": (-12.0464, -77.0428),
    "LA LIBERTAD": (-8.1118, -79.0287),
    "CAJAMARCA": (-7.1617, -78.5128),
    "CHICLAYO": (-6.7714, -79.8409),
    "ANCASH": (-9.5278, -77.5278),
    "AREQUIPA": (-16.4090, -71.5375),
    "TACNA": (-18.0146, -70.2536),
    "CUSCO": (-13.5319, -71.9675),
    "JUNIN": (-12.0651, -75.2049),
    "PIURA": (-5.1945, -80.6328),
    "ICA": (-14.0678, -75.7286),
    "PUNO": (-15.8402, -70.0219),
    "LORETO": (-3.7437, -73.2516),
}

DEPARTAMENTO_COORDS = {
    "AMAZONAS": (-6.2317, -77.8690),
    "ANCASH": (-9.5278, -77.5278),
    "APURIMAC": (-13.6339, -72.8814),
    "AREQUIPA": (-16.4090, -71.5375),
    "AYACUCHO": (-13.1631, -74.2236),
    "CAJAMARCA": (-7.1617, -78.5128),
    "CALLAO": (-12.0566, -77.1181),
    "CHICLAYO": (-6.7714, -79.8409),
    "CUSCO": (-13.5319, -71.9675),
    "HUANCAVELICA": (-12.7864, -74.9767),
    "HUANUCO": (-9.9306, -76.2422),
    "ICA": (-14.0678, -75.7286),
    "JUNIN": (-12.0651, -75.2049),
    "LA LIBERTAD": (-8.1118, -79.0287),
    "LAMBAYEQUE": (-6.7011, -79.9061),
    "LIMA": (-12.0464, -77.0428),
    "LORETO": (-3.7437, -73.2516),
    "MADRE DE DIOS": (-12.5942, -69.1891),
    "MOQUEGUA": (-17.1925, -70.9328),
    "PASCO": (-10.6864, -76.2627),
    "PIURA": (-5.1945, -80.6328),
    "PUNO": (-15.8402, -70.0219),
    "SAN MARTIN": (-6.4859, -76.3732),
    "TACNA": (-18.0146, -70.2536),
    "TRUJILLO": (-8.1118, -79.0287),
    "TUMBES": (-3.5669, -80.4515),
    "UCAYALI": (-8.3791, -74.5539),
}


OCR_AVAILABLE = False
EASYOCR_READER = None
EASYOCR_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
np = None

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageOps
    from io import BytesIO
    import numpy as np
    import easyocr

    easyocr_use_gpu = str(os.getenv("EASYOCR_USE_GPU", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "si",
        "sí",
    }
    langs_env = str(os.getenv("EASYOCR_LANGS", "en") or "en")
    langs = [x.strip() for x in langs_env.split(",") if x.strip()] or ["en"]
    EASYOCR_ALLOWLIST = str(
        os.getenv("EASYOCR_ALLOWLIST", EASYOCR_ALLOWLIST) or EASYOCR_ALLOWLIST
    ).strip() or EASYOCR_ALLOWLIST
    EASYOCR_READER = easyocr.Reader(langs, gpu=easyocr_use_gpu, verbose=False)
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False


def ensure_directories() -> None:
    for path in [LOGS_DIR, DATA_DIR, TEST_DIR, CACHE_DIR]:
        path.mkdir(parents=True, exist_ok=True)


def _truncate_log_if_exceeds_lines(log_file: Path, max_lines: int) -> bool:
    """Limpia el archivo si supera el umbral de líneas. Retorna True si truncó."""
    try:
        if not log_file.exists():
            return False

        total = 0
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            for _ in f:
                total += 1
                if total > max_lines:
                    break

        if total > max_lines:
            log_file.write_text("", encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def _prune_log_files_by_count(log_dir: Path, keep_files: int, pattern: str = "*.log") -> int:
    """Elimina logs más antiguos para mantener solo keep_files (nunca borra el más reciente)."""
    try:
        keep = max(1, int(keep_files or 1))
    except Exception:
        keep = 1

    try:
        archivos = [p for p in log_dir.glob(pattern) if p.is_file()]
    except Exception:
        return 0

    if len(archivos) <= keep:
        return 0

    archivos.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    eliminados = 0
    for viejo in archivos[keep:]:
        try:
            viejo.unlink(missing_ok=True)
            eliminados += 1
        except Exception:
            continue
    return eliminados


def _prune_run_dirs_by_count(runs_dir: Path, keep_dirs: int, current_run_dir: Path | None = None) -> int:
    """Elimina carpetas antiguas de runs preservando la corrida actual si aplica."""
    try:
        keep = max(1, int(keep_dirs or 1))
    except Exception:
        keep = 1

    try:
        dirs = [p for p in runs_dir.iterdir() if p.is_dir()]
    except Exception:
        return 0

    if len(dirs) <= keep:
        return 0

    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    eliminados = 0
    for viejo in dirs[keep:]:
        try:
            if current_run_dir and viejo.resolve() == current_run_dir.resolve():
                continue
        except Exception:
            pass
        try:
            shutil.rmtree(viejo)
            eliminados += 1
        except Exception:
            continue
    return eliminados


def setup_logger(name: str = "carnet_emision", suffix: str = "") -> logging.Logger:
    ensure_directories()
    suffix_clean = f"_{suffix}" if suffix else ""
    pruned_run_dirs = 0

    # En modo scheduled, LOG_DIR apunta a logs/runs/scheduled_*.
    # Aplicamos retención por carpetas para evitar crecimiento indefinido.
    if _as_bool_env("CARNET_LOG_RUNS_PRUNE_ENABLED", default=True):
        try:
            keep_run_dirs = max(1, _safe_int_env("CARNET_LOG_RUNS_KEEP_DIRS", 10))
            current_log_dir = LOGS_DIR.resolve()
            runs_dir = current_log_dir.parent
            if runs_dir.name.lower() == "runs" and current_log_dir.is_dir():
                pruned_run_dirs = _prune_run_dirs_by_count(
                    runs_dir,
                    keep_dirs=keep_run_dirs,
                    current_run_dir=current_log_dir,
                )
        except Exception:
            pruned_run_dirs = 0

    # Política recomendada para servidor: archivo único y limpieza al inicio.
    single_file_raw = str(os.getenv("CARNET_LOG_SINGLE_FILE", "1") or "1").strip().lower()
    single_file = single_file_raw in {"1", "true", "yes", "si", "sí", "on"}

    try:
        max_lines = int(str(os.getenv("CARNET_LOG_MAX_LINES", "10000") or "10000").strip())
    except Exception:
        max_lines = 10000
    max_lines = max(1000, max_lines)

    if single_file:
        log_file_name = str(os.getenv("CARNET_LOG_FILE_NAME", "carnet_emision.log") or "carnet_emision.log").strip()
        if not log_file_name:
            log_file_name = "carnet_emision.log"
        log_file = LOGS_DIR / log_file_name
        was_truncated = _truncate_log_if_exceeds_lines(log_file, max_lines=max_lines)
        pruned_logs = 0
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = LOGS_DIR / f"{name}{suffix_clean}_{stamp}.log"
        was_truncated = False
        try:
            retention = int(str(os.getenv("CARNET_LOG_ROTATING_KEEP_FILES", "120") or "120").strip())
        except Exception:
            retention = 120
        pruned_logs = _prune_log_files_by_count(LOGS_DIR, keep_files=max(1, retention), pattern=f"{name}{suffix_clean}_*.log")

    logger = logging.getLogger(f"{name}{suffix_clean}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    if was_truncated:
        logger.info("Log único truncado por umbral de líneas: %s (max=%s)", log_file, max_lines)
    if pruned_logs > 0:
        logger.info("Retención de logs aplicada: %s archivos antiguos eliminados", pruned_logs)
    if pruned_run_dirs > 0:
        logger.info("Retención de runs aplicada: %s carpetas antiguas eliminadas", pruned_run_dirs)
    logger.info("Log inicializado en %s", log_file)
    return logger


def limpiar_cache_upload_tmp_por_dni(logger: logging.Logger, dni: str) -> None:
    """Elimina cache temporal del DNI en data/cache/upload_tmp luego de éxito final."""
    if not _as_bool_env("CARNET_CACHE_CLEAN_ON_SUCCESS", default=True):
        return

    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    if not dni_digits:
        return

    target = DATA_DIR / "cache" / "upload_tmp" / dni_digits
    if not target.exists() or not target.is_dir():
        return

    try:
        shutil.rmtree(target)
        logger.info("[CACHE] Limpiado upload_tmp para DNI=%s | path=%s", dni_digits, target)
    except Exception as exc:
        logger.warning("[CACHE] No se pudo limpiar upload_tmp para DNI=%s: %s", dni_digits, exc)


def _as_bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or ("1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        return default


def _detect_windows_screen_size(default_w: int = 1920, default_h: int = 1080):
    """Retorna resolución efectiva (espacio lógico) en Windows."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        w = int(user32.GetSystemMetrics(0))
        h = int(user32.GetSystemMetrics(1))
        if w >= 800 and h >= 600:
            return w, h
    except Exception:
        pass
    return default_w, default_h


def _build_launch_args_for_window() -> list:
    tile_enabled = _as_bool_env("BROWSER_TILE_ENABLE", default=False)
    if not tile_enabled:
        return ["--disable-infobars", "--start-maximized", "--window-size=1920,1080", "--window-position=0,0"]

    tile_total = max(1, _safe_int_env("BROWSER_TILE_TOTAL", 1))
    tile_index = _safe_int_env("BROWSER_TILE_INDEX", 0)
    if tile_index < 0:
        tile_index = 0
    if tile_index >= tile_total:
        tile_index = tile_total - 1

    tile_screen_w = _safe_int_env("BROWSER_TILE_SCREEN_W", 1920)
    tile_screen_h = _safe_int_env("BROWSER_TILE_SCREEN_H", 1080)
    tile_top_offset = max(0, _safe_int_env("BROWSER_TILE_TOP_OFFSET", 0))
    tile_gap = max(0, _safe_int_env("BROWSER_TILE_GAP", 6))
    tile_frame_pad = max(0, _safe_int_env("BROWSER_TILE_FRAME_PAD", 2))

    cols = 2 if tile_total == 2 else (1 if tile_total == 1 else 2)
    rows = (tile_total + cols - 1) // cols
    usable_h = max(480, tile_screen_h - tile_top_offset)
    cell_w = max(360, tile_screen_w // cols)
    cell_h = max(320, usable_h // rows)

    tile_w = max(320, cell_w - (tile_gap * 2) - tile_frame_pad)
    tile_h = max(260, cell_h - (tile_gap * 2))
    col = tile_index % cols
    row = tile_index // cols
    tile_x = col * cell_w + tile_gap + (tile_frame_pad if col > 0 else 0)
    tile_y = tile_top_offset + row * cell_h + tile_gap

    return [
        "--disable-infobars",
        f"--window-size={tile_w},{tile_h}",
        f"--window-position={tile_x},{tile_y}",
    ]


def _is_scheduled_mode() -> bool:
    return os.getenv("RUN_MODE", "manual").strip().lower() == "scheduled"


def _multiworker_habilitado() -> bool:
    if not _is_scheduled_mode():
        return False
    if _as_bool_env("MULTIWORKER_CHILD", default=False):
        return False
    return _as_bool_env("SCHEDULED_MULTIWORKER", default=True)


def escribir_input_rapido(page, selector: str, valor: str) -> None:
    campo = page.locator(selector)
    campo.wait_for(state="visible", timeout=12000)
    campo.click()
    campo.fill(valor)
    campo.evaluate(
        'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
    )
    campo.blur()
    if (campo.input_value() or "") != valor:
        campo.click()
        campo.press("Control+A")
        campo.press("Backspace")
        campo.type(valor, delay=12)
        campo.evaluate(
            'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
        )
        campo.blur()


def activar_pestana_autenticacion_tradicional(page) -> None:
    candidatos = [
        SEL["tab_tradicional"],
        '#tabViewLogin a:has-text("Autenticación Tradicional")',
        '#tabViewLogin a:has-text("Autenticacion Tradicional")',
    ]

    ultimo_error = None
    for selector in candidatos:
        try:
            tab = page.locator(selector)
            tab.first.wait_for(state="visible", timeout=3500)
            tab.first.click(timeout=3500)
            return
        except Exception as exc:
            ultimo_error = exc

    raise Exception(
        "No se pudo activar la pestaña de Autenticación Tradicional. "
        f"Detalle: {ultimo_error}"
    )


def validar_resultado_login_por_ui(page, timeout_ms: int = 12000):
    inicio = time.time()
    while (time.time() - inicio) * 1000 < timeout_ms:
        try:
            url_actual = (page.url or "").lower()
            if "/faces/aplicacion/inicio.xhtml" in url_actual:
                return True, None, time.time() - inicio
        except Exception:
            pass

        for sel in SUCCESS_SELECTORS:
            try:
                if page.locator(sel).first.is_visible(timeout=120):
                    return True, None, time.time() - inicio
            except Exception:
                pass

        for sel in ERROR_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    txt = (loc.first.inner_text() or "").strip()
                    if txt:
                        return False, txt, time.time() - inicio
            except Exception:
                pass
        page.wait_for_timeout(140)

    return False, "No se confirmó sesión autenticada en el tiempo esperado", time.time() - inicio


def pagina_muestra_servicio_no_disponible(page) -> bool:
    selectores_ok = [
        SEL["tab_tradicional"],
        SEL["numero_documento"],
        "#j_idt11\\:menuPrincipal",
        "form#gestionCitasForm",
    ]
    for sel in selectores_ok:
        try:
            if page.locator(sel).first.is_visible(timeout=150):
                return False
        except Exception:
            pass

    try:
        t = (page.title() or "").lower()
        if "service unavailable" in t:
            return True
    except Exception:
        pass

    try:
        h1 = (page.locator("h1").first.inner_text() or "").strip().lower()
        if "service unavailable" in h1:
            return True
    except Exception:
        pass

    try:
        body = (page.locator("body").inner_text(timeout=350) or "").lower()
        if "service unavailable" in body and "sucamec" in body:
            return True
    except Exception:
        pass

    return False


def esperar_hasta_servicio_disponible(page, url_objetivo: str, espera_segundos: int = 8):
    intento = 0
    while pagina_muestra_servicio_no_disponible(page):
        intento += 1
        page.wait_for_timeout(max(1, int(espera_segundos)) * 1000)
        page.goto(url_objetivo, wait_until="domcontentloaded", timeout=45000)


def corregir_captcha_ocr(texto_raw: str) -> str:
    if not texto_raw:
        return ""
    texto = str(texto_raw).strip().upper().replace(" ", "").replace("\n", "").replace("\r", "")
    texto = "".join(c for c in texto if c.isalnum())
    return texto


def validar_captcha_texto(texto: str) -> bool:
    return bool(texto) and len(texto) == 5 and texto.isalnum()


def preprocesar_imagen_captcha(img_bytes: bytes, variante: int = 0):
    img = Image.open(BytesIO(img_bytes)).convert("L")
    if variante == 0:
        img = ImageEnhance.Contrast(img).enhance(3.5)
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = ImageOps.invert(img)
        img = img.point(lambda p: 255 if p > 130 else 0)
    elif variante == 1:
        img = ImageEnhance.Contrast(img).enhance(2.8)
        w, h = img.size
        img = img.resize((w * 2, h * 2), Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(size=5))
        img = img.point(lambda p: 255 if p > 160 else 0)
    else:
        img = ImageEnhance.Contrast(img).enhance(4.0)
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        img = ImageOps.invert(img)
        img = img.point(lambda p: 255 if p > 110 else 0)
    return img


def _leer_texto_easyocr_desde_imagen(img, decoder: str = "greedy") -> str:
    if EASYOCR_READER is None or np is None:
        return ""

    try:
        arr = np.array(img)
    except Exception:
        return ""

    try:
        resultados = EASYOCR_READER.readtext(
            arr,
            detail=0,
            paragraph=False,
            allowlist=EASYOCR_ALLOWLIST,
            decoder=decoder,
        )
    except TypeError:
        resultados = EASYOCR_READER.readtext(
            arr,
            detail=0,
            paragraph=False,
            allowlist=EASYOCR_ALLOWLIST,
        )
    except Exception:
        return ""

    if isinstance(resultados, (list, tuple)):
        return " ".join(str(x or "") for x in resultados).strip()
    return str(resultados or "").strip()


def solve_captcha_ocr_base(page, captcha_img_selector: str, boton_refresh_selector: str, logger: logging.Logger, max_intentos: int = 6) -> str:
    if not OCR_AVAILABLE:
        raise Exception("OCR no disponible. Instala easyocr, pillow y numpy para modo automático.")

    # Fast path por defecto: menos combinaciones para bajar latencia por intento.
    usar_beamsearch = _as_bool_env("CARNET_OCR_USE_BEAMSEARCH", default=False)
    decoders_fast = ["greedy"]
    decoders_full = ["greedy", "beamsearch"] if usar_beamsearch else ["greedy"]
    variantes_fast = [0]
    variantes_full = [0, 1, 2]

    for intento in range(1, max(1, max_intentos) + 1):
        t0 = time.time()
        img_locator = page.locator(captcha_img_selector)
        img_locator.wait_for(state="visible", timeout=12000)
        img_bytes = img_locator.screenshot()

        def _buscar_candidato(variantes, decoders):
            for variante in variantes:
                img_proc = preprocesar_imagen_captcha(img_bytes, variante=variante)
                for decoder in decoders:
                    lectura = corregir_captcha_ocr(_leer_texto_easyocr_desde_imagen(img_proc, decoder=decoder))
                    if validar_captcha_texto(lectura):
                        return lectura
            return ""

        # Etapa rápida: 1 variante + decoder greedy.
        candidato = _buscar_candidato(variantes_fast, decoders_fast)

        # Fallback solo si no se pudo resolver en etapa rápida.
        if not candidato:
            candidato = _buscar_candidato(variantes_full, decoders_full)

        if candidato:
            logger.info(
                "Captcha OCR resuelto en intento %s: %s (%.2fs)",
                intento,
                candidato,
                time.time() - t0,
            )
            return candidato

        logger.warning(
            "OCR no encontró captcha válido en intento %s/%s (%.2fs)",
            intento,
            max_intentos,
            time.time() - t0,
        )
        if boton_refresh_selector:
            try:
                page.locator(boton_refresh_selector).click(timeout=4000)
                page.wait_for_timeout(120)
            except Exception:
                pass

    raise Exception(f"No se pudo resolver captcha automáticamente tras {max_intentos} intentos")


def solve_captcha_ocr(page, logger: logging.Logger) -> str:
    return solve_captcha_ocr_base(
        page,
        captcha_img_selector=SEL["captcha_img"],
        boton_refresh_selector=SEL["boton_refresh"],
        logger=logger,
        max_intentos=_safe_int_env("CARNET_OCR_MAX_INTENTOS", 4),
    )


def validar_credenciales_configuradas(credenciales: dict, etiqueta: str):
    faltantes = []
    if not str(credenciales.get("numero_documento", "")).strip():
        faltantes.append("numero_documento")
    if not str(credenciales.get("usuario", "")).strip():
        faltantes.append("usuario")
    if not str(credenciales.get("contrasena", "")).strip():
        faltantes.append("contrasena")
    if faltantes:
        raise Exception(
            f"Faltan credenciales para grupo {etiqueta}: {faltantes}. Configúralas en .env"
        )


def _normalizar_columna(nombre: str) -> str:
    base = str(nombre or "").strip().lower()
    base = unicodedata.normalize("NFKD", base)
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = base.replace("ñ", "n")
    base = re.sub(r"\s+", " ", base)
    return base


def _build_google_sheet_csv_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise Exception("URL de Google Sheets vacía")

    parsed = urlparse(raw)
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path or "")
    if not m:
        raise Exception("No se pudo extraer el ID del Google Sheet desde la URL")
    sheet_id = m.group(1)

    gid = None
    q = parse_qs(parsed.query or "")
    if q.get("gid"):
        gid = q.get("gid")[0]
    if not gid and parsed.fragment:
        frag = parse_qs(parsed.fragment)
        if frag.get("gid"):
            gid = frag.get("gid")[0]
        elif "gid=" in parsed.fragment:
            gid = parsed.fragment.split("gid=", 1)[1].split("&", 1)[0]
    gid = str(gid or "0").strip() or "0"

    # Agregar timestamp para prevenir caché de Google Sheets
    ts = int(time.time() * 1000)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}&t={ts}"


def imprimir_muestra_google_sheet(logger: logging.Logger, max_rows: int = 5) -> None:
    """Lee una hoja de Google Sheets vía CSV y muestra una muestra de registros."""
    gsheet_url = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
    csv_url = _build_google_sheet_csv_url(gsheet_url)

    req = Request(
        csv_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; carnet-emision-bot/1.0)"},
    )
    with urlopen(req, timeout=25) as resp:
        content = resp.read()

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        logger.warning("Google Sheet accesible, pero no devolvió filas")
        return

    col_dni = None
    col_estado = None
    col_observacion = None
    for col in (reader.fieldnames or []):
        ncol = _normalizar_columna(col)
        if col_dni is None and ncol == "dni":
            col_dni = col
        if col_estado is None and ncol == "estado":
            col_estado = col
        if col_observacion is None and (ncol == "observacion" or "observ" in ncol):
            col_observacion = col

    faltantes = []
    if not col_dni:
        faltantes.append("DNI")
    if not col_estado:
        faltantes.append("ESTADO")
    if not col_observacion:
        faltantes.append("OBSERVACIÓN")
    if faltantes:
        raise Exception(f"No se encontraron columnas esperadas en Google Sheet: {faltantes}")

    total = len(rows)
    limite = max(1, int(max_rows or 5))
    logger.info("Google Sheet accesible: %s", csv_url)
    logger.info("Filas totales detectadas: %s", total)
    logger.info("Mostrando %s registros de muestra", min(limite, total))

    mostrados = 0
    for row in rows:
        dni = str(row.get(col_dni, "") or "").strip()
        estado = str(row.get(col_estado, "") or "").strip()
        observacion = str(row.get(col_observacion, "") or "").strip()
        if not dni and not estado and not observacion:
            continue
        mostrados += 1
        logger.info("Muestra %s | DNI=%s | ESTADO=%s | OBSERVACION=%s", mostrados, dni, estado, observacion)
        if mostrados >= limite:
            break

    if mostrados == 0:
        logger.warning("No se encontraron filas con datos en las columnas clave")


def _resolver_columnas_por_esquema(fieldnames: list, esquema: list[tuple[str, list[str]]]) -> dict:
    """Resuelve columnas esperadas por esquema usando coincidencia flexible."""
    resultados = {}
    for nombre_logico, candidatos in esquema:
        resultados[nombre_logico] = _resolver_columna(fieldnames, candidatos)
    return resultados


def imprimir_muestra_google_sheet_desde_url(
    logger: logging.Logger,
    sheet_url: str,
    etiqueta: str,
    max_rows: int = 5,
    esquema_columnas: list[tuple[str, list[str]]] | None = None,
) -> None:
    """Imprime una muestra de una hoja remota específica."""
    csv_url = _build_google_sheet_csv_url(sheet_url)

    req = Request(
        csv_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; carnet-emision-bot/1.0)"},
    )
    with urlopen(req, timeout=25) as resp:
        content = resp.read()

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        logger.warning("[%s] Google Sheet accesible, pero no devolvió filas", etiqueta)
        return

    esquema_default = esquema_columnas or [
        ("dni", ["dni"]),
        ("estado", ["estado"]),
        ("observacion", ["observacion", "observaciones", "observ", "obs"]),
    ]
    columnas = _resolver_columnas_por_esquema(reader.fieldnames or [], esquema_default)

    faltantes = [nombre.upper() for nombre, valor in columnas.items() if not valor]
    if faltantes:
        raise Exception(f"[{etiqueta}] No se encontraron columnas esperadas en Google Sheet: {faltantes}")

    total = len(rows)
    limite = max(1, int(max_rows or 5))
    logger.info("[%s] Google Sheet accesible: %s", etiqueta, csv_url)
    logger.info("[%s] Filas totales detectadas: %s", etiqueta, total)
    logger.info("[%s] Mostrando %s registros de muestra", etiqueta, min(limite, total))

    mostrados = 0
    for row in rows:
        valores = {nombre: str(row.get(columna, "") or "").strip() for nombre, columna in columnas.items()}
        if not any(valores.values()):
            continue
        mostrados += 1
        partes = " | ".join(f"{nombre.upper()}={valor}" for nombre, valor in valores.items())
        logger.info("[%s] Muestra %s | %s", etiqueta, mostrados, partes)
        if mostrados >= limite:
            break

    if mostrados == 0:
        logger.warning("[%s] No se encontraron filas con datos en las columnas clave", etiqueta)


def _leer_google_sheet_rows(sheet_url: str, logger: logging.Logger) -> tuple[list, list]:
    """Lee una hoja de Google Sheets por CSV y devuelve (rows, fieldnames)."""
    csv_url = _build_google_sheet_csv_url(sheet_url)
    max_retries = max(1, _safe_int_env("CARNET_GSHEET_READ_RETRIES", 4))
    timeout_sec = max(8, _safe_int_env("CARNET_GSHEET_TIMEOUT_SEC", 25))
    retry_base_ms = max(200, _safe_int_env("CARNET_GSHEET_RETRY_BASE_MS", 600))

    content = b""
    last_exc = None
    for intento in range(1, max_retries + 1):
        try:
            req = Request(
                csv_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; carnet-emision-bot/1.0)"},
            )
            with urlopen(req, timeout=timeout_sec) as resp:
                content = resp.read()
            last_exc = None
            break
        except (IncompleteRead, TimeoutError, OSError) as exc:
            last_exc = exc
            if intento >= max_retries:
                break
            wait_ms = min(8000, retry_base_ms * (2 ** (intento - 1)))
            logger.warning(
                "[GSHEET] Lectura incompleta/interrumpida (intento %s/%s): %s. Reintento en %.1fs",
                intento,
                max_retries,
                exc,
                wait_ms / 1000.0,
            )
            time.sleep(wait_ms / 1000.0)

    if last_exc is not None:
        raise last_exc

    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return rows, list(reader.fieldnames or [])


def confirmar_acceso_google_sheet(logger: logging.Logger, sheet_url: str, etiqueta: str) -> tuple[list, list]:
    """Valida acceso a una hoja remota y registra solo una línea de confirmación."""
    rows, fields = _leer_google_sheet_rows(sheet_url, logger)
    logger.info("[%s] Acceso OK | filas=%s | columnas=%s", etiqueta, len(rows), len(fields))
    return rows, fields


def _extract_sheet_id_from_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise Exception("URL de Google Sheet vacía")
    parsed = urlparse(raw)
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path or "")
    if not m:
        raise Exception("No se pudo extraer el ID del Google Sheet desde la URL")
    return m.group(1)


def _extract_gid_from_url(sheet_url: str) -> str:
    parsed = urlparse(str(sheet_url or "").strip())
    gid = None
    q = parse_qs(parsed.query or "")
    if q.get("gid"):
        gid = q.get("gid")[0]
    if not gid and parsed.fragment:
        frag = parse_qs(parsed.fragment)
        if frag.get("gid"):
            gid = frag.get("gid")[0]
        elif "gid=" in parsed.fragment:
            gid = parsed.fragment.split("gid=", 1)[1].split("&", 1)[0]
    return str(gid or "0").strip() or "0"


def _google_sheets_service():
    try:
        service_account = importlib.import_module("google.oauth2.service_account")
        google_build = importlib.import_module("googleapiclient.discovery").build
    except Exception as exc:
        raise Exception("Faltan dependencias de Google Sheets API. Instala google-api-python-client y google-auth") from exc

    credentials_path = str(os.getenv("DRIVE_CREDENTIALS_JSON", DEFAULT_DRIVE_CREDENTIALS_JSON) or "").strip()
    if not credentials_path:
        raise Exception("Falta DRIVE_CREDENTIALS_JSON en .env")

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    return google_build("sheets", "v4", credentials=creds, cache_discovery=False)


def _sheet_col_to_a1(index_zero_based: int) -> str:
    index = int(index_zero_based)
    if index < 0:
        raise ValueError("index_zero_based no puede ser negativo")
    letters = ""
    while True:
        index, remainder = divmod(index, 26)
        letters = chr(65 + remainder) + letters
        if index == 0:
            break
        index -= 1
    return letters


def _sheet_title_from_gid(service, spreadsheet_id: str, gid: str) -> str:
    response = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    target_gid = int(str(gid or "0").strip() or "0")
    for sheet in response.get("sheets", []) or []:
        props = sheet.get("properties", {}) or {}
        if int(props.get("sheetId", -1)) == target_gid:
            return str(props.get("title", "")).strip()
    raise Exception(f"No se encontró pestaña con gid={gid} en el spreadsheet")


def _update_sheet_cells_by_row(service, spreadsheet_id: str, sheet_title: str, row_number: int, updates: dict[str, str], fieldnames: list[str]) -> None:
    data = []
    for field_name, value in updates.items():
        column_index = None
        for idx, candidate in enumerate(fieldnames):
            if _normalizar_columna(candidate) == _normalizar_columna(field_name):
                column_index = idx
                break
        if column_index is None:
            continue
        column_a1 = _sheet_col_to_a1(column_index)
        data.append(
            {
                "range": f"{sheet_title}!{column_a1}{row_number}",
                "values": [[str(value or "")]],
            }
        )

    if not data:
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": data,
        },
    ).execute()


def _actualizar_fila_comparacion_por_row(logger: logging.Logger, compare_url: str, row_number: int, updates: dict[str, str], fieldnames: list[str] | None = None) -> None:
    service = _google_sheets_service()
    spreadsheet_id = _extract_sheet_id_from_url(compare_url)
    gid = _extract_gid_from_url(compare_url)
    sheet_title = _sheet_title_from_gid(service, spreadsheet_id, gid)

    if fieldnames is None:
        _, fieldnames = _leer_google_sheet_rows(compare_url, logger)

    _update_sheet_cells_by_row(service, spreadsheet_id, sheet_title, row_number, updates, fieldnames)


def _actualizar_fila_tercera_hoja_por_row(logger: logging.Logger, tercera_url: str, row_number: int, updates: dict[str, str], fieldnames: list[str] | None = None) -> None:
    """Actualiza una fila en la tercera hoja (Secuencias de Pago)."""
    service = _google_sheets_service()
    spreadsheet_id = _extract_sheet_id_from_url(tercera_url)
    gid = _extract_gid_from_url(tercera_url)
    sheet_title = _sheet_title_from_gid(service, spreadsheet_id, gid)

    if fieldnames is None:
        _, fieldnames = _leer_google_sheet_rows(tercera_url, logger)

    _update_sheet_cells_by_row(service, spreadsheet_id, sheet_title, row_number, updates, fieldnames)


def _estado_comparacion_es_objetivo(estado: str, estados_objetivo: set[str], permitir_vacio: bool) -> bool:
    valor = _normalizar_columna(estado)
    if not valor:
        return bool(permitir_vacio)
    return valor in estados_objetivo


def _extraer_timestamp_desde_estado_reserva(estado_raw: str) -> int | None:
    texto = str(estado_raw or "").strip()
    if not texto:
        return None

    m = re.search(r"(?:TS=)(\d{10,13})", texto, flags=re.IGNORECASE)
    if not m:
        m = re.search(r"\b(\d{10,13})\b", texto)
    if not m:
        return None

    try:
        ts_val = int(m.group(1))
    except Exception:
        return None

    if ts_val > 10**12:
        ts_val = ts_val // 1000
    return ts_val if ts_val > 0 else None


def _estado_reserva_expirada(estado_raw: str, lease_minutes: int = 120) -> bool:
    estado_norm = _normalizar_columna(estado_raw)
    if not estado_norm:
        return False

    prefijos = (
        "en_proceso|",
        "en proceso|",
        "reservado|",
        "reserva|",
    )
    if not any(estado_norm.startswith(p) for p in prefijos):
        return False

    ts_estado = _extraer_timestamp_desde_estado_reserva(estado_raw)
    if ts_estado is None:
        return True

    lease_seg = max(60, int(lease_minutes or 120) * 60)
    return int(time.time()) - ts_estado > lease_seg


def _worker_identity() -> tuple[str, str, str]:
    worker_id = str(os.getenv("WORKER_ID", "main") or "main").strip() or "main"
    run_id = str(os.getenv("WORKER_RUN_ID", "") or "").strip()
    if not run_id:
        run_id = datetime.now().strftime("%Y%m%d%H%M%S")
    worker_tag = f"W{worker_id}"
    return worker_id, run_id, worker_tag


def _token_estado_en_proceso(dni: str) -> str:
    worker_id, run_id, _ = _worker_identity()
    ts = int(time.time())
    dni_txt = str(dni or "").strip()
    return f"EN_PROCESO|RUN={run_id}|W={worker_id}|DNI={dni_txt}|TS={ts}"


def _token_estado_secuencia_reservada(dni: str) -> str:
    worker_id, run_id, _ = _worker_identity()
    ts = int(time.time())
    dni_txt = str(dni or "").strip()
    return f"RESERVADO|RUN={run_id}|W={worker_id}|DNI={dni_txt}|TS={ts}"


def _parse_fecha_texto(valor: str) -> datetime | None:
    texto = str(valor or "").strip()
    if not texto:
        return None

    candidatos = [
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%y",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%d.%m.%Y",
        "%d %m %Y",
    ]
    for formato in candidatos:
        try:
            return datetime.strptime(texto, formato)
        except Exception:
            pass

    match = re.search(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})", texto)
    if match:
        dia, mes, anio = match.groups()
        anio_int = int(anio)
        if anio_int < 100:
            anio_int += 2000 if anio_int < 70 else 1900
        try:
            return datetime(int(anio_int), int(mes), int(dia))
        except Exception:
            return None

    return None


def _resolver_fecha_cercana_en_fila(row: dict, fieldnames: list[str]) -> datetime | None:
    candidatos_fecha = [
        "fecha",
        "fecha_programacion",
        "fecha programacion",
        "fecha_programada",
        "fecha programada",
        "fecha_tramite",
        "fecha tramite",
        "fecha trámite",
        "fecha_registro",
        "fecha registro",
        "fecha_cita",
        "fecha cita",
        "fecha_emision",
        "fecha emision",
        "fecha de registro",
        "fecha de programacion",
    ]

    for col in fieldnames:
        col_norm = _normalizar_columna(col)
        if "fecha" not in col_norm:
            continue
        for candidato in candidatos_fecha:
            if _normalizar_columna(candidato) == col_norm or _normalizar_columna(candidato) in col_norm:
                fecha = _parse_fecha_texto(str(row.get(col, "") or ""))
                if fecha:
                    return fecha

    for col in fieldnames:
        if "fecha" in _normalizar_columna(col):
            fecha = _parse_fecha_texto(str(row.get(col, "") or ""))
            if fecha:
                return fecha

    return None


def _seleccionar_fila_base_por_dni(rows_base: list[dict], fields_base: list[str], col_base_dni: str, dni: str, logger: logging.Logger) -> dict | None:
    candidatos = []
    for base_idx, row in enumerate(rows_base, start=2):
        dni_base = str(row.get(col_base_dni, "") or "").strip()
        if dni_base != dni:
            continue
        candidatos.append((base_idx, row))

    if not candidatos:
        return None

    if len(candidatos) == 1:
        base_idx, row = candidatos[0]
        return {
            "row": row,
            "row_number": base_idx,
            "fecha_cercana": None,
            "criterio_seleccion": "unica_coincidencia",
        }

    hoy = datetime.now()
    mejor = None
    mejor_delta = None
    for base_idx, row in candidatos:
        fecha_candidata = _resolver_fecha_cercana_en_fila(row, fields_base)
        delta = abs((hoy - fecha_candidata).total_seconds()) if fecha_candidata else float("inf")
        if mejor is None or delta < mejor_delta or (delta == mejor_delta and base_idx < int(mejor.get("row_number", base_idx))):
            mejor = {
                "row": row,
                "row_number": base_idx,
                "fecha_cercana": fecha_candidata,
                "criterio_seleccion": "fecha_mas_cercana",
            }
            mejor_delta = delta

    if mejor is None:
        return None

    logger.info(
        "[CRUCE][BASE] DNI=%s con %s coincidencias | seleccionada_fila=%s | fecha=%s",
        dni,
        len(candidatos),
        mejor.get("row_number", 0),
        mejor.get("fecha_cercana").strftime("%d/%m/%Y") if mejor.get("fecha_cercana") else "N/D",
    )
    return mejor


def _cargar_cruce_pendiente_desde_hojas(
    logger: logging.Logger,
    max_rows: int = 1,
    preasignar_secuencias: bool = True,
    permitir_en_proceso_expirado: bool = False,
) -> list[dict]:
    url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
    url_compare = str(os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()
    url_third = str(os.getenv("CARNET_GSHEET_THIRD_URL", DEFAULT_GSHEET_THIRD_URL) or "").strip()
    if not url_compare:
        raise Exception("Falta s" \
        "CARNET_GSHEET_COMPARE_URL para procesar el cruce")

    rows_base, fields_base = _leer_google_sheet_rows(url_base, logger)
    rows_compare, fields_compare = _leer_google_sheet_rows(url_compare, logger)
    rows_third, fields_third = ([], [])
    if url_third:
        rows_third, fields_third = _leer_google_sheet_rows(url_third, logger)

    col_base_dni = _resolver_columna(fields_base, ["dni"])
    col_base_departamento = _resolver_columna(
        fields_base,
        [
            "indicar el departamento donde labora o donde postuló",
            "indicar el departamento donde labora o donde postulo",
            "departamento",
        ],
    )
    col_base_puesto = _resolver_columna(fields_base, ["puesto"])
    col_cmp_dni = _resolver_columna(fields_compare, ["dni"])
    col_cmp_estado = _resolver_columna(fields_compare, ["estado_tramite"])
    col_cmp_compania = _resolver_columna(fields_compare, ["compania", "compañia", "empresa"])
    col_cmp_obs = _resolver_columna(fields_compare, ["observacion"])
    col_cmp_fecha = _resolver_columna(fields_compare, ["fecha tramite"])
    col_third_dni = _resolver_columna(fields_third, ["dni"])
    col_third_copia = _resolver_columna(fields_third, ["copia de secuencia de pago", "copia secuencia de pago", "secuencia de pago"])
    col_third_estado_sec = _resolver_columna(
        fields_third,
        ["estado secuencia de pago", "estado secuencia pago", "estado_secuencia_pago", "estado secuencia"],
    )
    col_third_solicitado_por = _resolver_columna(
        fields_third,
        ["solicitado por", "solicitado_por", "solicitadopor"],
    )
    col_third_apellidos_nombre = _resolver_columna(
        fields_third,
        ["apellidos y nombre", "apellido y nombre", "apellidos nombres", "apellidos y nombres", "apellidos_nombre"],
    )

    if not col_base_dni or not col_base_departamento:
        raise Exception("La hoja base no contiene DNI y/o departamento")
    if not col_cmp_dni:
        raise Exception("La hoja de comparación no contiene DNI")

    terceros_libres = []
    terceros_omitidos_usado = 0
    terceros_omitidos_estado_no_vacio = 0
    if url_third and col_third_dni:
        for third_idx, row in enumerate(rows_third, start=2):
            dni_third = str(row.get(col_third_dni, "") or "").strip()
            copia_third = str(row.get(col_third_copia, "") or "").strip() if col_third_copia else ""
            estado_sec_raw = str(row.get(col_third_estado_sec, "") or "").strip() if col_third_estado_sec else ""
            estado_sec_norm = _normalizar_columna(estado_sec_raw)

            # Disponibilidad estricta: DNI vacío y Estado Secuencia de Pago vacío.
            if estado_sec_norm == "usado":
                terceros_omitidos_usado += 1
                continue
            if estado_sec_norm:
                terceros_omitidos_estado_no_vacio += 1
                continue

            if not dni_third and copia_third:
                terceros_libres.append({
                    "row": row,
                    "row_number": third_idx,
                    "copia_secuencia_pago_raw": copia_third,
                    "copia_secuencia_pago": normalizar_copia_secuencia_pago(copia_third),
                    "estado_secuencia_pago": estado_sec_raw,
                })

    if url_third:
        logger.info(
            "[CRUCE][TERCERA] Disponibles=%s | OmitidosEstadoUSADO=%s | OmitidosEstadoNoVacio=%s",
            len(terceros_libres),
            terceros_omitidos_usado,
            terceros_omitidos_estado_no_vacio,
        )

    estados_objetivo_env = str(os.getenv("CARNET_COMPARE_ESTADOS_OBJETIVO", "PENDIENTE") or "PENDIENTE")
    estados_objetivo = {
        _normalizar_columna(x)
        for x in estados_objetivo_env.split(",")
        if _normalizar_columna(x)
    }
    if not estados_objetivo:
        estados_objetivo = {"pendiente"}
    permitir_estado_vacio = _as_bool_env("CARNET_COMPARE_ALLOW_EMPTY_ESTADO", default=False)
    lease_min_compare = max(5, _safe_int_env("CARNET_COMPARE_RESERVA_LEASE_MINUTES", 120))
    logger.info(
        "[CRUCE] Criterio ESTADO: objetivos=%s | permitir_vacio=%s | permitir_en_proceso_expirado=%s | lease_min=%s",
        sorted(estados_objetivo),
        permitir_estado_vacio,
        permitir_en_proceso_expirado,
        lease_min_compare,
    )

    pendientes = []
    registros_saltados_sin_dni = 0
    registros_saltados_estado_fuera_criterio = 0
    indice_comprobante_libre = 0
    log_detalle_cruce = _as_bool_env("CARNET_CRUCE_LOG_DETALLE", default=False)
    max_detalle_ok = max(0, _safe_int_env("CARNET_CRUCE_LOG_MAX_OK", 25))
    max_detalle_warn = max(0, _safe_int_env("CARNET_CRUCE_LOG_MAX_WARN", 50))
    detalle_ok_emitidos = 0
    detalle_warn_emitidos = 0
    detalle_ok_suprimidos = 0
    detalle_warn_suprimidos = 0
    
    for idx, row in enumerate(rows_compare, start=2):
        dni = str(row.get(col_cmp_dni, "") or "").strip()
        estado = str(row.get(col_cmp_estado, "") or "").strip() if col_cmp_estado else ""
        compania = str(row.get(col_cmp_compania, "") or "").strip() if col_cmp_compania else ""
        
        if not dni:
            registros_saltados_sin_dni += 1
            continue
        
        estado_objetivo = _estado_comparacion_es_objetivo(estado, estados_objetivo, permitir_estado_vacio)
        if not estado_objetivo and permitir_en_proceso_expirado:
            estado_objetivo = _estado_reserva_expirada(estado, lease_minutes=lease_min_compare)

        if not estado_objetivo:
            registros_saltados_estado_fuera_criterio += 1
            logger.debug("[CRUCE] Fila %s saltada: DNI=%s tiene ESTADO='%s' (fuera de criterio)", idx, dni, estado)
            continue
        
        base_match = _seleccionar_fila_base_por_dni(rows_base, fields_base, col_base_dni, dni, logger)
        base_row = base_match.get("row") if base_match else None
        base_row_number = int(base_match.get("row_number", 0) or 0) if base_match else 0
        base_fecha_cercana = base_match.get("fecha_cercana") if base_match else None
        base_criterio_seleccion = str(base_match.get("criterio_seleccion", "") or "") if base_match else ""
        departamento = str(base_row.get(col_base_departamento, "") or "").strip() if base_row else ""
        puesto = str(base_row.get(col_base_puesto, "") or "").strip() if (base_row and col_base_puesto) else ""
        sede, origen_sede = resolver_sede_atencion_desde_departamento(departamento)
        modalidad_objetivo, modalidad_origen = resolver_modalidad_desde_puesto(puesto)
        tipo_doc_objetivo, dni_normalizado_tipo_doc, tipo_doc_origen = resolver_tipo_documento_desde_dni(dni)
        comprobante_libre = None
        copia_secuencia_pago_raw = ""
        copia_secuencia_pago = ""
        estado_secuencia_pago = ""
        tercera_row_number = 0
        nro_secuencia_objetivo = ""
        nro_secuencia_origen = "tercera_hoja:no_preasignada"
        if preasignar_secuencias:
            comprobante_libre = terceros_libres[indice_comprobante_libre] if indice_comprobante_libre < len(terceros_libres) else None
            copia_secuencia_pago_raw = str(comprobante_libre.get("copia_secuencia_pago_raw", "") or "").strip() if comprobante_libre else ""
            copia_secuencia_pago = str(comprobante_libre.get("copia_secuencia_pago", "") or "").strip() if comprobante_libre else ""
            estado_secuencia_pago = str(comprobante_libre.get("estado_secuencia_pago", "") or "").strip() if comprobante_libre else ""
            tercera_row_number = int(comprobante_libre.get("row_number", 0) or 0) if comprobante_libre else 0
            nro_secuencia_objetivo = copia_secuencia_pago
            nro_secuencia_origen = "tercera_hoja:libre" if copia_secuencia_pago else "tercera_hoja:no_disponible"
            if copia_secuencia_pago:
                indice_comprobante_libre += 1

        if base_row:
            if log_detalle_cruce and (max_detalle_ok == 0 or detalle_ok_emitidos < max_detalle_ok):
                logger.info(
                    "[CRUCE][OK] COMP_FILA=%s | DNI=%s | BASE_FILA=%s | BASE_TOTAL=%s | BASE_FECHA=%s | BASE_CRITERIO=%s | DEPARTAMENTO=%s | PUESTO=%s | MODALIDAD_OBJETIVO=%s | TIPO_DOC_OBJETIVO=%s | DNI_NORMALIZADO=%s | COPIA_SEC_PAGO_RAW=%s | COPIA_SEC_PAGO_APLICADA=%s | ESTADO_SECUENCIA_PAGO=%s | NRO_SEC_ORIGEN=%s | TERCERA_FILA=%s",
                    idx,
                    dni,
                    base_row_number,
                    len(rows_base),
                    base_fecha_cercana.strftime("%d/%m/%Y") if base_fecha_cercana else "N/D",
                    base_criterio_seleccion or "sin_criterio",
                    departamento,
                    puesto,
                    modalidad_objetivo,
                    tipo_doc_objetivo,
                    dni_normalizado_tipo_doc,
                    copia_secuencia_pago_raw,
                    copia_secuencia_pago,
                    estado_secuencia_pago,
                    nro_secuencia_origen,
                    tercera_row_number,
                )
                detalle_ok_emitidos += 1
            else:
                detalle_ok_suprimidos += 1
        else:
            if max_detalle_warn == 0 or detalle_warn_emitidos < max_detalle_warn:
                logger.warning(
                    "[CRUCE][WARN] COMP_FILA=%s | DNI=%s sin cruce en HOJA_BASE (total=%s)",
                    idx,
                    dni,
                    len(rows_base),
                )
                detalle_warn_emitidos += 1
            else:
                detalle_warn_suprimidos += 1

        pendientes.append(
            {
                "row_number": idx,
                "compare_row_number": idx,
                "compare_url": url_compare,
                "dni": dni,
                "estado": estado,
                "compania": compania,
                "base_row": base_row,
                "base_row_number": base_row_number,
                "base_fecha_cercana": base_fecha_cercana.strftime("%d/%m/%Y") if base_fecha_cercana else "",
                "base_criterio_seleccion": base_criterio_seleccion,
                "departamento": departamento,
                "puesto": puesto,
                "sede": sede,
                "origen_sede": origen_sede,
                "modalidad_objetivo": modalidad_objetivo,
                "modalidad_origen": modalidad_origen,
                "tipo_doc_objetivo": tipo_doc_objetivo,
                "dni_normalizado_tipo_doc": dni_normalizado_tipo_doc,
                "tipo_doc_origen": tipo_doc_origen,
                "copia_secuencia_pago_raw": copia_secuencia_pago_raw,
                "copia_secuencia_pago": copia_secuencia_pago,
                "nro_secuencia_objetivo": nro_secuencia_objetivo,
                "nro_secuencia_origen": nro_secuencia_origen,
                "tercera_row_number": tercera_row_number,
                "estado_secuencia_pago": estado_secuencia_pago,
                "terceros_libres": terceros_libres,
                "indice_comprobante_libre_actual": indice_comprobante_libre,
                "fieldnames_compare": fields_compare,
                "fieldnames_third": fields_third,
                "col_third_estado_sec": col_third_estado_sec,
                "col_third_solicitado_por": col_third_solicitado_por,
                "col_third_apellidos_nombre": col_third_apellidos_nombre,
                "col_third_dni": col_third_dni,
                "tercera_url": url_third,
                "col_cmp_obs": col_cmp_obs,
                "col_cmp_fecha": col_cmp_fecha,
            }
        )
        if len(pendientes) >= max(1, int(max_rows or 1)):
            break
    
    logger.info(
        "[CRUCE] Resumen: %s filas procesadas | %s pendientes (criterio ESTADO) | %s saltadas (DNI vacío) | %s saltadas (ESTADO fuera de criterio)",
        len(rows_compare),
        len(pendientes),
        registros_saltados_sin_dni,
        registros_saltados_estado_fuera_criterio,
    )
    if detalle_ok_suprimidos > 0:
        logger.info(
            "[CRUCE] Detalle OK suprimido: %s fila(s). Para ver detalle use CARNET_CRUCE_LOG_DETALLE=1",
            detalle_ok_suprimidos,
        )
    if detalle_warn_suprimidos > 0:
        logger.warning(
            "[CRUCE] Warns de sin cruce suprimidos: %s fila(s). Ajuste CARNET_CRUCE_LOG_MAX_WARN si desea más detalle",
            detalle_warn_suprimidos,
        )

    return pendientes


def _intentar_reservar_registro_compare(logger: logging.Logger, item: dict) -> bool:
    compare_url = str(item.get("compare_url", "") or "").strip()
    compare_row_number = int(item.get("compare_row_number", 0) or item.get("row_number", 0) or 0)
    compare_fieldnames = item.get("fieldnames_compare", []) or []
    dni = str(item.get("dni", "") or "").strip()
    if not compare_url or compare_row_number <= 0:
        return False

    token = _token_estado_en_proceso(dni)
    _, _, worker_tag = _worker_identity()
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")

    try:
        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            compare_row_number,
            {
                "estado_tramite": token,
                "estado tramite": token,
                "responsable": f"BOT CARNÉ SUCAMEC {worker_tag}",
                "fecha tramite": fecha_hoy,
                "fecha_tramite": fecha_hoy,
            },
            fieldnames=compare_fieldnames,
        )
    except Exception as exc:
        logger.warning(
            "[WORKER][RESERVA] No se pudo escribir reserva en comparación fila=%s DNI=%s: %s",
            compare_row_number,
            dni,
            exc,
        )
        return False

    try:
        rows_cmp, fields_cmp = _leer_google_sheet_rows(compare_url, logger)
    except Exception as exc:
        logger.warning("[WORKER][RESERVA] No se pudo verificar reserva en comparación: %s", exc)
        return False

    idx = compare_row_number - 2
    if idx < 0 or idx >= len(rows_cmp):
        return False

    col_estado = _resolver_columna(fields_cmp, ["estado_tramite", "estado tramite"])
    if not col_estado:
        logger.warning("[WORKER][RESERVA] No se resolvió ESTADO_TRAMITE para verificación")
        return False

    estado_actual = str(rows_cmp[idx].get(col_estado, "") or "").strip()
    if estado_actual != token:
        return False

    item["compare_reserva_token"] = token
    item["compare_reservado_por"] = worker_tag
    return True


def _reservar_siguiente_item_para_worker(logger: logging.Logger) -> dict | None:
    max_scan = max(8, _safe_int_env("CARNET_WORKER_SCAN_ROWS", 200))
    permitir_stale = _as_bool_env("CARNET_COMPARE_ALLOW_STALE_IN_PROGRESS", default=True)

    candidatos = _cargar_cruce_pendiente_desde_hojas(
        logger,
        max_rows=max_scan,
        preasignar_secuencias=False,
        permitir_en_proceso_expirado=permitir_stale,
    )
    if not candidatos:
        return None

    for item in candidatos:
        if _intentar_reservar_registro_compare(logger, item):
            return item
    return None


def _reservar_siguiente_secuencia_para_worker(
    logger: logging.Logger,
    item: dict,
    dni: str,
    filas_excluidas: set[int] | None = None,
) -> dict | None:
    tercera_url = str(item.get("tercera_url", "") or os.getenv("CARNET_GSHEET_THIRD_URL", DEFAULT_GSHEET_THIRD_URL) or "").strip()
    if not tercera_url:
        return None

    filas_excluidas = filas_excluidas or set()
    lease_min = max(5, _safe_int_env("CARNET_TERCERA_RESERVA_LEASE_MINUTES", 120))

    rows_third, fields_third = _leer_google_sheet_rows(tercera_url, logger)
    col_third_dni = _resolver_columna(fields_third, ["dni"])
    col_third_copia = _resolver_columna(
        fields_third,
        ["copia de secuencia de pago", "copia secuencia de pago", "secuencia de pago"],
    )
    col_third_estado = _resolver_columna(
        fields_third,
        ["estado secuencia de pago", "estado secuencia pago", "estado_secuencia_pago", "estado secuencia"],
    )

    if not col_third_copia or not col_third_estado:
        logger.warning("[WORKER][SECUENCIA] Columnas de tercera hoja no resueltas para reserva")
        return None

    for row_number, row in enumerate(rows_third, start=2):
        if row_number in filas_excluidas:
            continue

        copia_raw = str(row.get(col_third_copia, "") or "").strip()
        if not copia_raw:
            continue

        estado_raw = str(row.get(col_third_estado, "") or "").strip()
        estado_norm = _normalizar_columna(estado_raw)
        reserva_expirada = _estado_reserva_expirada(estado_raw, lease_minutes=lease_min)
        if estado_norm == "usado":
            continue

        if estado_norm and not reserva_expirada:
            continue

        dni_actual = str(row.get(col_third_dni, "") or "").strip() if col_third_dni else ""
        if dni_actual and (not reserva_expirada) and estado_norm != "no encontrado":
            continue

        token = _token_estado_secuencia_reservada(dni)
        updates = {col_third_estado: token}
        if col_third_dni:
            updates[col_third_dni] = str(dni or "").strip()

        try:
            _actualizar_fila_tercera_hoja_por_row(
                logger,
                tercera_url,
                row_number,
                updates,
                fieldnames=fields_third,
            )
        except Exception as exc:
            logger.warning(
                "[WORKER][SECUENCIA] No se pudo reservar fila=%s secuencia=%s: %s",
                row_number,
                copia_raw,
                exc,
            )
            continue

        try:
            rows_verify, fields_verify = _leer_google_sheet_rows(tercera_url, logger)
        except Exception as exc:
            logger.warning("[WORKER][SECUENCIA] No se pudo verificar reserva de secuencia: %s", exc)
            continue

        idx = row_number - 2
        if idx < 0 or idx >= len(rows_verify):
            continue
        col_estado_verify = _resolver_columna(
            fields_verify,
            ["estado secuencia de pago", "estado secuencia pago", "estado_secuencia_pago", "estado secuencia"],
        )
        if not col_estado_verify:
            continue

        estado_verify = str(rows_verify[idx].get(col_estado_verify, "") or "").strip()
        if estado_verify != token:
            continue

        copia_norm = normalizar_copia_secuencia_pago(copia_raw)
        item["tercera_url"] = tercera_url
        item["fieldnames_third"] = fields_verify
        item["col_third_estado_sec"] = col_estado_verify
        item["col_third_dni"] = _resolver_columna(fields_verify, ["dni"])
        item["col_third_solicitado_por"] = _resolver_columna(fields_verify, ["solicitado por", "solicitado_por", "solicitadopor"])
        item["col_third_apellidos_nombre"] = _resolver_columna(
            fields_verify,
            ["apellidos y nombre", "apellido y nombre", "apellidos nombres", "apellidos y nombres", "apellidos_nombre"],
        )
        item["tercera_row_number"] = row_number
        item["copia_secuencia_pago_raw"] = copia_raw
        item["copia_secuencia_pago"] = copia_norm
        item["nro_secuencia_objetivo"] = copia_norm
        item["nro_secuencia_origen"] = "tercera_hoja:reservada_worker"
        item["tercera_reserva_token"] = token
        return {
            "row_number": row_number,
            "token": token,
            "copia_secuencia_pago_raw": copia_raw,
            "copia_secuencia_pago": copia_norm,
        }

    return None


def _liberar_reserva_secuencia_si_aplica(logger: logging.Logger, item: dict) -> None:
    tercera_url = str(item.get("tercera_url", "") or "").strip()
    tercera_row = int(item.get("tercera_row_number", 0) or 0)
    token = str(item.get("tercera_reserva_token", "") or "").strip()
    third_fieldnames = item.get("fieldnames_third", []) or []
    col_estado = item.get("col_third_estado_sec")
    if not tercera_url or tercera_row <= 0 or not token or not col_estado:
        return

    try:
        rows_third, fields_third = _leer_google_sheet_rows(tercera_url, logger)
        idx = tercera_row - 2
        if idx < 0 or idx >= len(rows_third):
            return
        col_estado_real = _resolver_columna(
            fields_third,
            ["estado secuencia de pago", "estado secuencia pago", "estado_secuencia_pago", "estado secuencia"],
        )
        if not col_estado_real:
            return
        estado_actual = str(rows_third[idx].get(col_estado_real, "") or "").strip()
        if estado_actual != token:
            return

        _actualizar_fila_tercera_hoja_por_row(
            logger,
            tercera_url,
            tercera_row,
            {col_estado: ""},
            fieldnames=third_fieldnames,
        )
        item["tercera_reserva_token"] = ""
        logger.info("[WORKER][SECUENCIA] Reserva liberada en tercera hoja fila=%s", tercera_row)
    except Exception as exc:
        logger.warning("[WORKER][SECUENCIA] No se pudo liberar reserva de secuencia: %s", exc)


def ejecutar_prueba_cruce_y_sede_en_formulario(page, logger: logging.Logger, max_rows: int = 1) -> None:
    """Toma un único registro pendiente y aplica la sede de atención en la vista Crear Solicitud."""
    pendientes = _cargar_cruce_pendiente_desde_hojas(logger, max_rows=max_rows)
    if not pendientes:
        logger.warning("[CRUCE] No hay registros pendientes para la prueba")
        return

    compare_url = str(os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")

    for idx, item in enumerate(pendientes, start=1):
        dni = item["dni"]
        base_row = item.get("base_row")
        if not base_row:
            logger.warning("[CRUCE][%s] DNI=%s no existe en la hoja base", idx, dni)
            _actualizar_fila_comparacion_por_row(
                logger,
                compare_url,
                item["row_number"],
                {
                    "observacion": f"DNI {dni} no encontrado en la hoja base",
                    "fecha tramite": fecha_hoy,
                },
                fieldnames=item.get("fieldnames_compare", []),
            )
            continue

        sede = item["sede"]
        logger.info(
            "[CRUCE][%s] DNI=%s | DEPARTAMENTO=%s | SEDE=%s | ORIGEN=%s",
            idx,
            dni,
            item.get("departamento", ""),
            sede,
            item.get("origen_sede", ""),
        )

        if idx == 1:
            seleccionar_sede_atencion(page, sede)
            logger.info("[FORM] Sede de atención aplicada en la vista: %s", sede)

        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            item["row_number"],
            {
                "observacion": "",
                "fecha tramite": fecha_hoy,
            },
            fieldnames=item.get("fieldnames_compare", []),
        )


def _resolver_columna(fieldnames: list, candidatos: list[str]) -> str | None:
    normalizados = {str(candidato or "").strip().lower() for candidato in candidatos}
    for col in fieldnames:
        col_norm = str(col or "").strip().lower()
        if col_norm in normalizados:
            return col
    return None


def _normalizar_departamento(nombre: str) -> str:
    base = _normalizar_columna(nombre)
    base = base.replace(" ", "")
    return base


def _distancia_km_aprox(coord_a: tuple[float, float], coord_b: tuple[float, float]) -> float:
    """Distancia Haversine aproximada en km entre dos coordenadas (lat, lon)."""
    lat1, lon1 = coord_a
    lat2, lon2 = coord_b

    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2) + math.cos(p1) * math.cos(p2) * (math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371.0 * c


def _sede_canonica_desde_texto(texto_sede: str) -> str:
    valor = _normalizar_columna(texto_sede)
    for sede in SEDES_SUCAMEC_DISPONIBLES:
        sede_norm = _normalizar_columna(sede)
        if valor == sede_norm or valor in sede_norm or sede_norm in valor:
            return sede
    return ""


def _obtener_opciones_sede_atencion(page) -> list[str]:
    """Abre el combo y obtiene las opciones visibles del desplegable Sede de Atención."""
    trigger = page.locator(SEL["crear_solicitud_sede_trigger"])
    panel = page.locator(SEL["crear_solicitud_sede_panel"])

    trigger.wait_for(state="visible", timeout=12000)
    trigger.click()
    panel.wait_for(state="visible", timeout=7000)

    items = panel.locator("li.ui-selectonemenu-item")
    total = items.count()
    opciones = []
    for idx in range(total):
        item = items.nth(idx)
        texto = str(item.get_attribute("data-label") or item.inner_text() or "").strip()
        if texto:
            opciones.append(texto)

    # Cerramos el panel para mantener sincronía del formulario.
    try:
        trigger.click(timeout=2000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    vistos = set()
    salida = []
    for op in opciones:
        op_norm = _normalizar_columna(op)
        if op_norm in vistos:
            continue
        vistos.add(op_norm)
        salida.append(op)
    return salida


def _obtener_opciones_modalidad(page) -> list[str]:
    """Abre el combo y obtiene las opciones visibles del desplegable Modalidad."""
    trigger = page.locator(SEL["crear_solicitud_modalidad_trigger"])
    panel = page.locator(SEL["crear_solicitud_modalidad_panel"])

    trigger.wait_for(state="visible", timeout=12000)
    trigger.click()
    panel.wait_for(state="visible", timeout=7000)

    items = panel.locator("li.ui-selectonemenu-item")
    total = items.count()
    opciones = []
    for idx in range(total):
        item = items.nth(idx)
        texto = str(item.get_attribute("data-label") or item.inner_text() or "").strip()
        if texto:
            opciones.append(texto)

    try:
        trigger.click(timeout=2000)
    except Exception:
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass

    vistos = set()
    salida = []
    for op in opciones:
        op_norm = _normalizar_columna(op)
        if op_norm in vistos:
            continue
        vistos.add(op_norm)
        salida.append(op)
    return salida


def resolver_sede_para_dropdown(page, departamento: str, sede_sugerida: str) -> tuple[str, str]:
    """Resuelve la mejor sede para el dropdown: exacta si existe; si no, la geográficamente más cercana."""
    opciones = _obtener_opciones_sede_atencion(page)
    if not opciones:
        return sede_sugerida, "sin_opciones_dropdown"

    objetivo_norm = _normalizar_columna(sede_sugerida)
    for op in opciones:
        op_norm = _normalizar_columna(op)
        if objetivo_norm == op_norm or objetivo_norm in op_norm or op_norm in objetivo_norm:
            return op, "dropdown:exacta"

    dep_norm = _normalizar_columna(departamento).upper()
    dep_coord = DEPARTAMENTO_COORDS.get(dep_norm)
    if not dep_coord:
        dep_canon = _normalizar_departamento(departamento)
        dep_coord = DEPARTAMENTO_COORDS.get(dep_canon)

    mejor_op = opciones[0]
    mejor_dist = float("inf")
    if dep_coord:
        for op in opciones:
            sede_canon = _sede_canonica_desde_texto(op)
            if not sede_canon:
                continue
            sede_coord = SEDE_COORDS.get(sede_canon)
            if not sede_coord:
                continue
            dist = _distancia_km_aprox(dep_coord, sede_coord)
            if dist < mejor_dist:
                mejor_dist = dist
                mejor_op = op

        if mejor_dist != float("inf"):
            return mejor_op, f"dropdown:geocercana:{mejor_dist:.1f}km"

    # Fallback final: si no hay coordenadas suficientes, usar primera opción disponible.
    return mejor_op, "dropdown:fallback_primera"


def resolver_modalidad_desde_puesto(puesto: str) -> tuple[str, str]:
    """Resuelve modalidad objetivo en base al campo PUESTO de la hoja base."""
    puesto_norm = _normalizar_columna(puesto)
    if "proteccion privada" in puesto_norm:
        return "PROTECCION PRIVADA", "puesto:proteccion_privada"
    return "VIGILANCIA PRIVADA", "puesto:default_vigilancia"


def resolver_modalidad_para_dropdown(page, modalidad_objetivo: str) -> tuple[str, str]:
    """Ajusta modalidad objetivo a una opción real del dropdown (exacta o alias)."""
    opciones = _obtener_opciones_modalidad(page)
    if not opciones:
        return modalidad_objetivo, "modalidad:sin_opciones_dropdown"

    objetivo_norm = _normalizar_columna(modalidad_objetivo)
    for op in opciones:
        op_norm = _normalizar_columna(op)
        if objetivo_norm == op_norm or objetivo_norm in op_norm or op_norm in objetivo_norm:
            return op, "modalidad:dropdown:exacta"

    if objetivo_norm == "proteccion privada":
        for op in opciones:
            op_norm = _normalizar_columna(op)
            if "proteccion" in op_norm and ("privada" in op_norm or "personal" in op_norm):
                return op, "modalidad:dropdown:alias_proteccion"

    for op in opciones:
        op_norm = _normalizar_columna(op)
        if "vigilancia privada" in op_norm:
            return op, "modalidad:dropdown:fallback_vigilancia"

    return opciones[0], "modalidad:dropdown:fallback_primera"


def resolver_tipo_registro_para_flujo() -> tuple[str, str]:
    """Sub-iteración actual: mantener siempre INICIAL."""
    return "INICIAL", "sub_iteracion_1:fijo_inicial"


def resolver_tipo_documento_desde_dni(dni_raw: str) -> tuple[str, str, str]:
    """
    Resuelve tipo de documento para el dropdown desde el campo DNI de hoja comparación.
    Regla: 8 dígitos -> DNI, 9 dígitos -> CE.
    Incluye normalización defensiva por ceros al inicio/fin.
    """
    digits = "".join(ch for ch in str(dni_raw or "") if ch.isdigit())

    candidatos = [
        ("exacto", digits),
        ("trim_ceros_fin", digits.rstrip("0")),
        ("trim_ceros_inicio", digits.lstrip("0")),
        ("trim_ceros_extremos", digits.strip("0")),
    ]
    for origen, candidato in candidatos:
        if len(candidato) == 8:
            return "DNI", candidato, f"tipo_doc:{origen}:len8"
        if len(candidato) == 9:
            return "CE", candidato, f"tipo_doc:{origen}:len9"

    compacto = digits.strip("0")
    if len(compacto) >= 9:
        return "CE", compacto[-9:], "tipo_doc:fallback_ultimos_9"
    if len(compacto) >= 8:
        return "DNI", compacto[-8:], "tipo_doc:fallback_ultimos_8"
    if len(digits) >= 9:
        return "CE", digits[-9:], "tipo_doc:fallback_raw_ultimos_9"
    if len(digits) >= 8:
        return "DNI", digits[-8:], "tipo_doc:fallback_raw_ultimos_8"

    return "DNI", digits, "tipo_doc:default_dni_longitud_invalida"


def normalizar_copia_secuencia_pago(valor: str) -> str:
    """Convierte valores tipo 095253-0 a 095253 conservando ceros a la izquierda."""
    texto = str(valor or "").strip()
    if not texto:
        return ""
    if "-" in texto:
        texto = texto.split("-", 1)[0].strip()
    return texto


def resolver_sede_atencion_desde_departamento(departamento: str) -> tuple[str, str]:
    """Devuelve (sede, origen) a partir del departamento de la hoja base."""
    dep_norm = _normalizar_departamento(departamento)
    if not dep_norm:
        return "LIMA", "default"

    mapa_directo = {_normalizar_departamento(k): v for k, v in DEPARTAMENTO_A_SEDE.items()}
    if dep_norm in mapa_directo:
        return mapa_directo[dep_norm], "directa"

    mapa_fallback = {_normalizar_departamento(k): v for k, v in DEPARTAMENTO_FALLBACK_POR_REGION.items()}
    for candidato_norm, sede in mapa_fallback.items():
        if candidato_norm and candidato_norm in dep_norm:
            return sede, f"fallback:{candidato_norm}"

    return "LIMA", "default"


def seleccionar_opcion_primefaces(page, trigger_selector: str, panel_selector: str, label_selector: str, valor: str, nombre_campo: str) -> None:
    trigger = page.locator(trigger_selector)
    panel = page.locator(panel_selector)

    # Patrón robusto tipo example.py: trigger principal y fallback al label.
    try:
        trigger.wait_for(state="visible", timeout=6000)
        trigger.click()
    except PlaywrightTimeoutError:
        label_fallback = page.locator(label_selector)
        label_fallback.wait_for(state="visible", timeout=6000)
        label_fallback.click()

    panel.wait_for(state="visible", timeout=7000)

    # Intento 1: coincidencia exacta por data-label.
    opcion = panel.locator(f'li.ui-selectonemenu-item[data-label="{valor}"]')
    try:
        opcion.wait_for(state="visible", timeout=1800)
        opcion.first.click()
    except PlaywrightTimeoutError:
        # Intento 2: búsqueda flexible por texto visible.
        items = panel.locator("li.ui-selectonemenu-item")
        total = items.count()
        if total == 0:
            raise Exception(f"No hay opciones visibles en {nombre_campo}")

        objetivo = _normalizar_columna(valor)
        item_match = None
        opciones = []
        for idx in range(total):
            item = items.nth(idx)
            texto = str(item.get_attribute("data-label") or item.inner_text() or "").strip()
            if texto:
                opciones.append(texto)
            texto_norm = _normalizar_columna(texto)
            if objetivo == texto_norm or objetivo in texto_norm or texto_norm in objetivo:
                item_match = item
                break

        if item_match is None:
            raise Exception(f"No se encontró opción para {nombre_campo}: '{valor}'. Opciones: {opciones}")
        item_match.click()

    page.wait_for_timeout(220)
    label = str(page.locator(label_selector).inner_text() or "").strip()
    objetivo = _normalizar_columna(valor)
    label_norm = _normalizar_columna(label)
    if not label_norm or (objetivo != label_norm and objetivo not in label_norm):
        raise Exception(f"No se confirmó {nombre_campo}. Esperado '{valor}' | Actual '{label}'")


def seleccionar_sede_atencion(page, sede: str) -> None:
    seleccionar_opcion_primefaces(
        page,
        trigger_selector=SEL["crear_solicitud_sede_trigger"],
        panel_selector=SEL["crear_solicitud_sede_panel"],
        label_selector=SEL["crear_solicitud_sede_label"],
        valor=sede,
        nombre_campo="Sede de Atención",
    )


def seleccionar_modalidad(page, modalidad: str) -> None:
    seleccionar_opcion_primefaces(
        page,
        trigger_selector=SEL["crear_solicitud_modalidad_trigger"],
        panel_selector=SEL["crear_solicitud_modalidad_panel"],
        label_selector=SEL["crear_solicitud_modalidad_label"],
        valor=modalidad,
        nombre_campo="Modalidad",
    )


def seleccionar_tipo_registro(page, tipo_registro: str) -> None:
    seleccionar_opcion_primefaces(
        page,
        trigger_selector=SEL["crear_solicitud_tipo_registro_trigger"],
        panel_selector=SEL["crear_solicitud_tipo_registro_panel"],
        label_selector=SEL["crear_solicitud_tipo_registro_label"],
        valor=tipo_registro,
        nombre_campo="Tipo de registro",
    )


def seleccionar_tipo_documento(page, tipo_documento: str) -> None:
    seleccionar_opcion_primefaces(
        page,
        trigger_selector=SEL["crear_solicitud_tipo_doc_trigger"],
        panel_selector=SEL["crear_solicitud_tipo_doc_panel"],
        label_selector=SEL["crear_solicitud_tipo_doc_label"],
        valor=tipo_documento,
        nombre_campo="Tipo de Documento",
    )


def ingresar_documento_y_buscar(page, numero_documento: str) -> None:
    valor = "".join(ch for ch in str(numero_documento or "") if ch.isdigit())
    if not valor:
        raise Exception("No se pudo ingresar Documento: DNI vacío o inválido")

    input_doc = page.locator(SEL["crear_solicitud_documento_input"]).first
    input_doc.wait_for(state="visible", timeout=9000)
    input_doc.click(timeout=5000)
    input_doc.fill(valor)
    input_doc.evaluate(
        'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
    )
    input_doc.blur()

    actual = str(input_doc.input_value() or "").strip()
    if actual != valor:
        input_doc.click(timeout=3000)
        input_doc.press("Control+A")
        input_doc.press("Backspace")
        input_doc.type(valor, delay=10)
        input_doc.evaluate(
            'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
        )
        input_doc.blur()
        actual = str(input_doc.input_value() or "").strip()
        if actual != valor:
            raise Exception(f"No se confirmó Documento. Esperado '{valor}' | Actual '{actual}'")

    boton_buscar = page.locator(SEL["crear_solicitud_buscar_button"]).first
    boton_buscar.wait_for(state="visible", timeout=9000)
    boton_buscar.click(timeout=8000)
    esperar_ajax_primefaces(page, timeout_ms=7000)


def limpiar_campo_copia_secuencia_pago(page) -> None:
    """Limpia el campo de Copia de Secuencia de pago."""
    input_sec = page.locator(SEL["crear_solicitud_nro_secuencia_input"]).first
    try:
        input_sec.wait_for(state="visible", timeout=3000)
        input_sec.click(timeout=2000)
        input_sec.press("Control+A")
        input_sec.press("Backspace")
        input_sec.evaluate(
            'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
        )
        input_sec.blur()
    except Exception:
        pass


def ingresar_copia_secuencia_pago(page, valor_secuencia: str) -> None:
    valor = str(valor_secuencia or "").strip()
    if not valor:
        raise Exception("La Copia de Secuencia de pago está vacía")

    input_sec = page.locator(SEL["crear_solicitud_nro_secuencia_input"]).first
    input_sec.wait_for(state="visible", timeout=9000)
    input_sec.click(timeout=5000)
    input_sec.fill(valor)
    input_sec.evaluate(
        'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
    )
    input_sec.blur()

    actual = str(input_sec.input_value() or "").strip()
    if actual != valor:
        input_sec.click(timeout=3000)
        input_sec.press("Control+A")
        input_sec.press("Backspace")
        input_sec.type(valor, delay=10)
        input_sec.evaluate(
            'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }'
        )
        input_sec.blur()
        actual = str(input_sec.input_value() or "").strip()
        if actual != valor:
            raise Exception(f"No se confirmó Copia de Secuencia de pago. Esperado '{valor}' | Actual '{actual}'")

    boton_verificar = page.locator(SEL["crear_solicitud_verificar_recibo_button"]).first
    boton_verificar.wait_for(state="visible", timeout=9000)
    boton_verificar.click(timeout=8000)
    esperar_ajax_primefaces(page, timeout_ms=7000)


def _leer_src_preview_foto(page) -> str:
    """Obtiene el src actual del preview de foto en el formulario, si existe."""
    selectores = [
        '#createForm\\:j_idt77',
        'img[id^="createForm:"][width="150"][height="200"]',
    ]
    for selector in selectores:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            src = str(loc.get_attribute("src", timeout=800) or "").strip()
            if src:
                return src
        except Exception:
            continue
    return ""


def _leer_texto_upload_djfut(page) -> str:
    """Lee el texto visible del bloque de carga DJFUT."""
    selectores = [
        SEL["crear_solicitud_djfut_container"],
        SEL["crear_solicitud_djfut_label"],
    ]
    for selector in selectores:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            texto = str(loc.inner_text() or "").strip()
            if texto:
                return texto
        except Exception:
            continue
    return ""


def _leer_texto_upload_certificado_medico(page) -> str:
    """Lee el texto visible del bloque de carga del certificado médico."""
    selectores = [
        SEL["crear_solicitud_certificado_medico_container"],
        SEL["crear_solicitud_certificado_medico_label"],
    ]
    for selector in selectores:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            texto = str(loc.inner_text() or "").strip()
            if texto:
                return texto
        except Exception:
            continue
    return ""


def _leer_error_upload(page, container_selector: str) -> str:
    """Lee el mensaje de error visible de un componente PrimeFaces fileupload."""
    selectores = [
        f"{container_selector} .ui-messages-error-summary",
        f"{container_selector} .ui-messages-error-detail",
        f"{container_selector} .ui-messages-error",
        f"{container_selector} .ui-messages",
    ]
    partes = []
    for selector in selectores:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue
            texto = str(loc.inner_text() or "").strip()
            if texto and texto not in partes:
                partes.append(texto)
        except Exception:
            continue
    return " | ".join(partes).strip()


def _validar_archivo_adjuntable_previo(
    archivo_local: Path,
    allowed_exts: set[str],
    max_bytes: int,
    etiqueta: str,
) -> tuple[bool, str, Path]:
    """Valida extensión y tamaño antes de intentar la carga al formulario."""
    ruta = Path(archivo_local)
    if not ruta.exists() or not ruta.is_file():
        return False, f"No existe el archivo local para carga de {etiqueta}: {ruta}", ruta

    def _fmt_size(size_bytes: int) -> str:
        kb = float(size_bytes) / 1024.0
        if kb >= 1024:
            return f"{(kb / 1024.0):.1f} MB"
        return f"{kb:.1f} KB"

    def _optimizar_pdf_pikepdf(src: Path, dst: Path) -> tuple[bool, str]:
        try:
            pikepdf = importlib.import_module("pikepdf")
        except Exception:
            return False, "pikepdf no disponible"

        try:
            with pikepdf.open(str(src)) as pdf:
                try:
                    pdf.docinfo.clear()
                except Exception:
                    pass
                try:
                    pdf.save(
                        str(dst),
                        compress_streams=True,
                        object_stream_mode=pikepdf.ObjectStreamMode.generate,
                        linearize=False,
                    )
                except TypeError:
                    pdf.save(str(dst), compress_streams=True)
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
            detalles.append(f"{detalle_lossless}: {_fmt_size(current_size)} -> {_fmt_size(out_base.stat().st_size)}")
            if out_base.stat().st_size <= limite_bytes:
                return out_base, "; ".join(detalles)
        elif detalle_lossless:
            detalles.append(detalle_lossless)

        permitir_lossy = _as_bool_env("CARNET_PDF_ALLOW_LOSSY_OPTIMIZATION", default=False)
        if not permitir_lossy:
            return src, "; ".join(detalles)

        for preset in ("/printer", "/ebook", "/screen"):
            ok_gs, detalle_gs = _optimizar_pdf_ghostscript(src, out_base, preset)
            if ok_gs and out_base.exists() and out_base.stat().st_size > 0:
                detalles.append(f"{detalle_gs}: {_fmt_size(current_size)} -> {_fmt_size(out_base.stat().st_size)}")
                if out_base.stat().st_size <= limite_bytes:
                    return out_base, "; ".join(detalles)
            elif detalle_gs:
                detalles.append(detalle_gs)

        return src, "; ".join(detalles)

    try:
        size_bytes = ruta.stat().st_size
    except Exception as exc:
        return False, f"No se pudo leer el tamaño de {etiqueta}: {exc}"

    detalle_archivo = f"{ruta.name} {_fmt_size(size_bytes)}"

    ext = ruta.suffix.lower()
    if allowed_exts and ext not in allowed_exts:
        permitido = ", ".join(sorted({e.upper().lstrip('.') for e in allowed_exts}))
        return False, f"Solo se permite {etiqueta} con extensión {permitido}. | {detalle_archivo}", ruta

    if ext == ".pdf" and max_bytes > 0 and size_bytes > max_bytes:
        ruta_ajustada, detalle_opt = _preparar_pdf_para_limite(ruta, max_bytes)
        if ruta_ajustada != ruta:
            ruta = ruta_ajustada
            size_bytes = ruta.stat().st_size
            detalle_archivo = f"{ruta.name} {_fmt_size(size_bytes)}"
        if detalle_opt:
            detalle_archivo = f"{detalle_archivo} | {detalle_opt}"

    if max_bytes > 0 and size_bytes > max_bytes:
        limite_mb = max_bytes / (1024 * 1024)
        if limite_mb >= 1:
            limite_txt = f"{limite_mb:.0f} MB"
        else:
            limite_txt = f"{int(max_bytes / 1024)} KB"
        return False, f"El archivo supera el tamaño máximo permitido. tamaño máximo: {limite_txt} | {detalle_archivo}", ruta

    return True, "", ruta


def cargar_archivo_foto_en_formulario(page, logger: logging.Logger, archivo_local: Path) -> tuple[bool, str]:
    """Carga un archivo local en el input file de Foto sin usar el diálogo nativo de Windows."""
    ruta = Path(archivo_local)
    if not ruta.exists() or not ruta.is_file():
        raise Exception(f"No existe el archivo local para carga de foto: {ruta}")

    ok_previo, msg_previo, ruta_validada = _validar_archivo_adjuntable_previo(
        ruta,
        allowed_exts={".jpg", ".jpeg"},
        max_bytes=_safe_int_env("CARNET_MAX_FOTO_BYTES", 80 * 1024),
        etiqueta="foto",
    )
    if not ok_previo:
        logger.warning("[FORM][FOTO] %s", msg_previo)
        return False, msg_previo
    ruta = ruta_validada

    src_antes = _leer_src_preview_foto(page)
    input_foto = page.locator(SEL["crear_solicitud_foto_input"]).first
    input_foto.wait_for(state="attached", timeout=12000)
    input_foto.set_input_files(str(ruta))
    esperar_ajax_primefaces(page, timeout_ms=7000)
    page.wait_for_timeout(250)

    error_visible = _leer_error_upload(page, "#createForm\\:idFoto")
    if error_visible:
        logger.warning("[FORM][FOTO] Error visible del componente upload: %s", error_visible)
        return False, error_visible

    nombre_cargado = str(
        input_foto.evaluate(
            "el => (el && el.files && el.files.length > 0 && el.files[0] && el.files[0].name) ? el.files[0].name : ''"
        )
        or ""
    ).strip()
    if not nombre_cargado:
        inicio = time.time()
        while (time.time() - inicio) * 1000 < 4500:
            src_despues = _leer_src_preview_foto(page)
            if src_despues and src_despues != src_antes:
                logger.info(
                    "[FORM][FOTO] Carga confirmada por cambio de preview src | src_antes=%s | src_despues=%s",
                    src_antes or "N/D",
                    src_despues,
                )
                return True, ""
            page.wait_for_timeout(200)

        logger.warning(
            "[FORM][FOTO] No se confirmó por input.files ni por cambio de preview src; en PrimeFaces puede limpiarse tras upload. Se continúa flujo."
        )
        return False, "No se pudo validar la carga de foto"

    logger.info(
        "[FORM][FOTO] Archivo cargado en input | local=%s | nombre_en_input=%s",
        ruta,
        nombre_cargado,
    )
    return True, ""


def cargar_archivo_djfut_en_formulario(page, logger: logging.Logger, archivo_local: Path) -> tuple[bool, str]:
    """Carga el PDF DJFUT en el input file correspondiente y confirma por texto visible."""
    ruta = Path(archivo_local)
    if not ruta.exists() or not ruta.is_file():
        raise Exception(f"No existe el archivo local para carga de DJFUT: {ruta}")

    ok_previo, msg_previo, ruta_validada = _validar_archivo_adjuntable_previo(
        ruta,
        allowed_exts={".pdf"},
        max_bytes=_safe_int_env("CARNET_MAX_DJFUT_BYTES", 80 * 1024),
        etiqueta="DJFUT",
    )
    if not ok_previo:
        if "tamaño máximo" in (msg_previo or "").lower() or "tamano maximo" in (msg_previo or "").lower():
            detalle = msg_previo.split("|", 1)[1].strip() if "|" in (msg_previo or "") else f"{ruta.name} {(ruta.stat().st_size / 1024.0):.1f} KB"
            msg_previo = f"Solo se permite subir archivos con un máximo de 80 Kb. | {detalle}"
        logger.warning("[FORM][DJFUT] %s", msg_previo)
        return False, msg_previo
    ruta = ruta_validada

    if ruta != Path(archivo_local):
        logger.info("[FORM][DJFUT] Se usará PDF optimizado para upload: %s", ruta)

    texto_antes = _leer_texto_upload_djfut(page)
    input_djfut = page.locator(SEL["crear_solicitud_djfut_input"]).first
    input_djfut.wait_for(state="attached", timeout=12000)
    input_djfut.set_input_files(str(ruta))
    esperar_ajax_primefaces(page, timeout_ms=7000)
    page.wait_for_timeout(300)

    error_visible = _leer_error_upload(page, "#createForm\\:archivoDJ")
    if error_visible:
        logger.warning("[FORM][DJFUT] Error visible del componente upload: %s", error_visible)
        return False, error_visible

    nombre_esperado = ruta.name
    texto_despues = _leer_texto_upload_djfut(page)
    if nombre_esperado and nombre_esperado.lower() in texto_despues.lower():
        logger.info(
            "[FORM][DJFUT] Archivo cargado y confirmado por texto visible | local=%s | texto=%s",
            ruta,
            texto_despues,
        )
        return True, ""

    try:
        nombre_input = str(
            input_djfut.evaluate(
                "el => (el && el.files && el.files.length > 0 && el.files[0] && el.files[0].name) ? el.files[0].name : ''"
            )
            or ""
        ).strip()
    except Exception:
        nombre_input = ""

    if nombre_input and nombre_input.lower() == nombre_esperado.lower():
        logger.info(
            "[FORM][DJFUT] Archivo cargado y confirmado por input.files | local=%s | nombre=%s",
            ruta,
            nombre_input,
        )
        return True, ""

    logger.warning(
        "[FORM][DJFUT] No se pudo confirmar la carga por texto visible ni por input.files | antes=%s | despues=%s",
        texto_antes or "N/D",
        texto_despues or "N/D",
    )
    return False, "No se pudo validar la carga de DJFUT"


def cargar_archivo_certificado_medico_en_formulario(page, logger: logging.Logger, archivo_local: Path) -> tuple[bool, str]:
    """Carga el certificado médico en el input file correspondiente y confirma por nombre visible."""
    ruta = Path(archivo_local)
    if not ruta.exists() or not ruta.is_file():
        raise Exception(f"No existe el archivo local para carga de certificado médico: {ruta}")

    ok_previo, msg_previo, ruta_validada = _validar_archivo_adjuntable_previo(
        ruta,
        allowed_exts={".pdf"},
        max_bytes=_safe_int_env("CARNET_MAX_CERT_MED_BYTES", 160 * 1024),
        etiqueta="certificado médico",
    )
    if not ok_previo:
        if "tamaño máximo" in (msg_previo or "").lower() or "tamano maximo" in (msg_previo or "").lower():
            detalle = msg_previo.split("|", 1)[1].strip() if "|" in (msg_previo or "") else f"{ruta.name} {(ruta.stat().st_size / 1024.0):.1f} KB"
            msg_previo = f"Solo se permite subir archivos con un máximo de 160 Kb. | {detalle}"
        logger.warning("[FORM][CERT_MED] %s", msg_previo)
        return False, msg_previo
    ruta = ruta_validada

    if ruta != Path(archivo_local):
        logger.info("[FORM][CERT_MED] Se usará PDF optimizado para upload: %s", ruta)

    texto_antes = _leer_texto_upload_certificado_medico(page)
    input_cert = page.locator(SEL["crear_solicitud_certificado_medico_input"]).first
    input_cert.wait_for(state="attached", timeout=12000)
    input_cert.set_input_files(str(ruta))
    esperar_ajax_primefaces(page, timeout_ms=7000)
    page.wait_for_timeout(300)

    error_visible = _leer_error_upload(page, "#createForm\\:certificadoMedico")
    if error_visible:
        logger.warning("[FORM][CERT_MED] Error visible del componente upload: %s", error_visible)
        return False, error_visible

    nombre_esperado = ruta.name
    texto_despues = _leer_texto_upload_certificado_medico(page)
    if nombre_esperado and nombre_esperado.lower() in texto_despues.lower():
        logger.info(
            "[FORM][CERT_MED] Archivo cargado y confirmado por texto visible | local=%s | texto=%s",
            ruta,
            texto_despues,
        )
        return True, ""

    try:
        nombre_input = str(
            input_cert.evaluate(
                "el => (el && el.files && el.files.length > 0 && el.files[0] && el.files[0].name) ? el.files[0].name : ''"
            )
            or ""
        ).strip()
    except Exception:
        nombre_input = ""

    if nombre_input and nombre_input.lower() == nombre_esperado.lower():
        logger.info(
            "[FORM][CERT_MED] Archivo cargado y confirmado por input.files | local=%s | nombre=%s",
            ruta,
            nombre_input,
        )
        return True, ""

    logger.warning(
        "[FORM][CERT_MED] No se pudo confirmar la carga por texto visible ni por input.files | antes=%s | despues=%s",
        texto_antes or "N/D",
        texto_despues or "N/D",
    )
    return False, "No se pudo validar la carga del certificado médico"


def _script_monitor_carnet_growl_js() -> str:
    """
    Script JS que instala un MutationObserver para capturar mensajes growl en tiempo real.
    Mantiene un buffer persistente (window.__carnetGrowlBuffer) independiente del DOM.
    Esto permite detectar mensajes aunque desaparezcan y sin necesidad de navegador visible.
    """
    return """
    (() => {
        if (window.__carnetGrowlInstalled) return;
        window.__carnetGrowlInstalled = true;
        window.__carnetGrowlBuffer = window.__carnetGrowlBuffer || [];

        const pushMessage = (text) => {
            if (!text) return;
            const t = String(text).trim();
            if (!t) return;
            window.__carnetGrowlBuffer.push({ text: t, ts: Date.now() });
            if (window.__carnetGrowlBuffer.length > 160) {
                window.__carnetGrowlBuffer = window.__carnetGrowlBuffer.slice(-160);
            }
        };

        const extractFromNode = (node) => {
            if (!node) return;
            const selectors = '#mensajesGrowl_container .ui-growl-title, #mensajesGrowl_container .ui-growl-message, .ui-growl-title, .ui-growl-message';

            if (typeof node.matches === 'function' && node.matches(selectors)) {
                pushMessage(node.textContent || '');
            }

            if (typeof node.querySelectorAll === 'function') {
                const nodes = Array.from(node.querySelectorAll(selectors));
                for (const n of nodes) {
                    pushMessage(n.textContent || '');
                }
            }
        };

        const observer = new MutationObserver((mutations) => {
            for (const m of mutations) {
                for (const n of m.addedNodes || []) {
                    extractFromNode(n);
                }
                if (m.type === 'characterData' && m.target) {
                    pushMessage(m.target.textContent || '');
                }
            }
        });

        if (document && document.body) {
            observer.observe(document.body, { childList: true, subtree: true, characterData: true });
            extractFromNode(document.body);
        }
    })();
    """


def activar_monitor_carnet_growl(page) -> None:
    """
    Instala el monitor de growl en la página.
    Se ejecuta en background, sin necesidad de navegador visible.
    """
    try:
        monitor_script = _script_monitor_carnet_growl_js()
        page.add_init_script(script=monitor_script)
        page.evaluate(monitor_script)
    except Exception:
        pass


def obtener_buffer_carnet_growl(page) -> list:
    """
    Recupera el buffer de mensajes capturados por el monitor.
    Retorna lista de dicts {text, ts} de todos los mensajes vistos.
    """
    try:
        buffer = page.evaluate("() => window.__carnetGrowlBuffer || []")
        return buffer if isinstance(buffer, list) else []
    except Exception:
        return []


def limpiar_buffer_carnet_growl(page) -> None:
    """Limpia el buffer de mensajes growl capturados en memoria JS."""
    try:
        page.evaluate("() => { window.__carnetGrowlBuffer = []; }")
    except Exception:
        pass


def _detectar_etiqueta_recibo_valido(page) -> str:
    """
    Detecta la etiqueta que aparece cuando el recibo es valido:
    "Monto: S/. <valor>, Fecha: <dd/mm/aaaa>".
    """
    patron = re.compile(r"monto\s*:\s*s/\.\s*\d+(?:\.\d+)?\s*,\s*fecha\s*:\s*\d{2}/\d{2}/\d{4}", re.IGNORECASE)

    try:
        labels = page.locator("#createForm label.ui-outputlabel.ui-widget").all()
        for lbl in labels:
            txt = (lbl.inner_text() or "").strip()
            if txt and patron.search(txt):
                return txt
    except Exception:
        pass

    try:
        html = page.content() or ""
        m = patron.search(html)
        if m:
            return m.group(0)
    except Exception:
        pass

    return ""


def detectar_mensaje_carne_cesado(page, max_wait_ms: int = 5000) -> tuple[bool, str]:
    """
    Detecta si hay un mensaje de carné cesado post-Buscar con POLLING ACTIVO.
    
    Estrategia multinivel:
    1. Buffer JS persistente (captura mensajes aunque desaparezcan del DOM)
    2. DOM actual (múltiples selectores)
    3. HTML page.content() (busca texto en HTML oculto)
    
    Retorna (encontrado, texto_mensaje).
    Realiza polling hasta vencer deadline o encontrar el mensaje.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)
    
    def _es_alerta_cambio_empresa(texto: str) -> bool:
        t = _normalizar_columna(texto)
        if not t:
            return False
        if "carne" in t and "cesado" in t:
            return True
        if "personal de seguridad" in t and "ya cuenta con el carne nro" in t:
            return True
        return False

    while True:
        mensajes = []
        
        # 1. Buffer JS (persistente, aunque el DOM esté vacío)
        try:
            buffer = obtener_buffer_carnet_growl(page)
            for msg_obj in buffer:
                texto = str(msg_obj.get("text", "")) if isinstance(msg_obj, dict) else str(msg_obj)
                texto = texto.strip()
                if texto:
                    mensajes.append(texto)
        except Exception:
            pass
        
        # 2. DOM actual: múltiples selectores para cobertura completa
        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            ".ui-growl-message",
            ".ui-growl-message-error",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 6)
                for i in range(total):
                    txt = (loc.nth(i).text_content() or "").strip()
                    if txt:
                        mensajes.append(txt)
            except Exception:
                pass
        
        # 3. Fallback: buscar en HTML del documento (incluye nodos ocultos)
        try:
            html_doc = page.content() or ""
            if _es_alerta_cambio_empresa(html_doc):
                return True, "Mensaje de cambio de empresa detectado en HTML de página"
        except Exception:
            pass
        
        # Evaluar todos los mensajes capturados
        for msg in mensajes:
            if _es_alerta_cambio_empresa(msg):
                return True, msg
        
        # Chequear si venció el deadline
        if time.time() >= deadline:
            return False, ""
        
        # Polling suave: esperar 120ms antes de reintentar
        page.wait_for_timeout(120)
        
def detectar_error_tramite_observado(page, max_wait_ms: int = 4500, min_ts_ms: int | None = None) -> tuple[bool, str]:
    """
    Detecta alerta de SUCAMEC cuando el personal ya tiene registro en
    la misma modalidad en estado OBSERVADO.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)

    def _es_mensaje_objetivo(texto: str) -> bool:
        t = _normalizar_columna(texto)
        if not t:
            return False
        return (
            "misma modalidad" in t
            and "estado observado" in t
            and "personal de seguridad" in t
        )

    while True:
        # 1. Buffer JS
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                texto = str(msg_dict.get("text", "") or "").strip()
                if _es_mensaje_objetivo(texto):
                    return True, texto
        except Exception:
            pass

        # 2. DOM actual
        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 8)
                for i in range(total):
                    texto = (loc.nth(i).text_content() or "").strip()
                    if _es_mensaje_objetivo(texto):
                        return True, texto
            except Exception:
                pass

        # 3. HTML de la pagina
        try:
            html = (page.content() or "")
            if _es_mensaje_objetivo(html):
                return True, "Este personal de seguridad cuenta con un registro en la misma modalidad en estado OBSERVADO"
        except Exception:
            pass

        if time.time() >= deadline:
            return False, ""

        page.wait_for_timeout(120)


def detectar_error_tramite_transmitido(page, max_wait_ms: int = 4500, min_ts_ms: int | None = None) -> tuple[bool, str]:
    """
    Detecta alerta de SUCAMEC cuando el personal ya tiene registro en
    la misma modalidad en estado TRANSMITIDO.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)

    def _es_mensaje_objetivo(texto: str) -> bool:
        t = _normalizar_columna(texto)
        if not t:
            return False
        return (
            "misma modalidad" in t
            and "estado transmitido" in t
            and "personal de seguridad" in t
        )

    while True:
        # 1. Buffer JS
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                texto = str(msg_dict.get("text", "") or "").strip()
                if _es_mensaje_objetivo(texto):
                    return True, texto
        except Exception:
            pass

        # 2. DOM actual
        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 8)
                for i in range(total):
                    texto = (loc.nth(i).text_content() or "").strip()
                    if _es_mensaje_objetivo(texto):
                        return True, texto
            except Exception:
                pass

        # 3. HTML completo
        try:
            html = (page.content() or "")
            if _es_mensaje_objetivo(html):
                return True, "Este personal de seguridad cuenta con un registro en la misma modalidad en estado TRANSMITIDO"
        except Exception:
            pass

        if time.time() >= deadline:
            return False, ""

        page.wait_for_timeout(120)


def detectar_error_curso_no_vigente(page, max_wait_ms: int = 4500, min_ts_ms: int | None = None) -> tuple[bool, str]:
    """
    Detecta alerta de SUCAMEC cuando el prospecto no cuenta con curso vigente.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)

    def _es_mensaje_objetivo(texto: str) -> bool:
        t = _normalizar_columna(texto)
        return "prospecto" in t and "curso vigente" in t and "no cuenta" in t

    while True:
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                texto = str(msg_dict.get("text", "") or "").strip()
                if _es_mensaje_objetivo(texto):
                    return True, texto
        except Exception:
            pass

        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 8)
                for i in range(total):
                    texto = (loc.nth(i).text_content() or "").strip()
                    if _es_mensaje_objetivo(texto):
                        return True, texto
            except Exception:
                pass

        try:
            html = (page.content() or "")
            if _es_mensaje_objetivo(html):
                return True, "El prospecto no cuenta con curso vigente"
        except Exception:
            pass

        if time.time() >= deadline:
            return False, ""

        page.wait_for_timeout(120)


def detectar_error_documento_no_existe(page, max_wait_ms: int = 4500, min_ts_ms: int | None = None) -> tuple[bool, str]:
    """Detecta alerta de SUCAMEC: El documento ingresado no existe."""
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)

    def _es_mensaje_objetivo(texto: str) -> bool:
        t = _normalizar_columna(texto)
        return "documento ingresado" in t and "no existe" in t

    while True:
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                texto = str(msg_dict.get("text", "") or "").strip()
                if _es_mensaje_objetivo(texto):
                    return True, texto
        except Exception:
            pass

        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 8)
                for i in range(total):
                    texto = (loc.nth(i).text_content() or "").strip()
                    if _es_mensaje_objetivo(texto):
                        return True, texto
            except Exception:
                pass

        try:
            html = (page.content() or "")
            if _es_mensaje_objetivo(html):
                return True, "El documento ingresado no existe"
        except Exception:
            pass

        if time.time() >= deadline:
            return False, ""

        page.wait_for_timeout(120)


def detectar_error_carne_vigente_otra_empresa(page, max_wait_ms: int = 4500, min_ts_ms: int | None = None) -> tuple[bool, str]:
    """
    Detecta alerta de SUCAMEC cuando la persona ya cuenta con carné vigente
    en una empresa distinta.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)

    def _es_mensaje_objetivo(texto: str) -> bool:
        t = _normalizar_columna(texto)
        return (
            "no puede sacar" in t
            and "carne" in t
            and "personal de seguridad" in t
            and "ya cuenta con uno" in t
            and "distinta empresa" in t
        )

    while True:
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                texto = str(msg_dict.get("text", "") or "").strip()
                if _es_mensaje_objetivo(texto):
                    return True, texto
        except Exception:
            pass

        for selector in [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            "#mensajesGrowl_container .ui-growl-title",
            "#mensajesGrowl_container .ui-growl-message",
        ]:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), 8)
                for i in range(total):
                    texto = (loc.nth(i).text_content() or "").strip()
                    if _es_mensaje_objetivo(texto):
                        return True, texto
            except Exception:
                pass

        try:
            html = (page.content() or "")
            if _es_mensaje_objetivo(html):
                return True, "Esta persona no puede sacar un carné de personal de seguridad con esta empresa porque ya cuenta con uno en una distinta empresa"
        except Exception:
            pass

        if time.time() >= deadline:
            return False, ""

        page.wait_for_timeout(120)


def _registrar_error_tramite_en_comparacion(
    logger: logging.Logger,
    compare_url: str,
    compare_row_number: int,
    compare_fieldnames: list[str],
    mensaje: str,
    fecha_tramite: str,
) -> None:
    """Registra OBSERVACION/ESTADO + RESPONSABLE + FECHA TRAMITE en hoja de comparación."""
    if not compare_url or compare_row_number <= 0:
        return

    try:
        _, _, worker_tag = _worker_identity()
        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            compare_row_number,
            {
                "observacion": mensaje,
                "observación": mensaje,
                "estado_tramite": "ERROR EN TRAMITE",
                "estado tramite": "ERROR EN TRAMITE",
                "responsable": f"BOT CARNÉ SUCAMEC {worker_tag}",
                "fecha tramite": str(fecha_tramite or "").strip(),
                "fecha_tramite": str(fecha_tramite or "").strip(),
            },
            fieldnames=compare_fieldnames,
        )
        logger.info(
            "[FORM] Fila comparacion %s actualizada con OBSERVACION, ESTADO_TRAMITE, RESPONSABLE y FECHA TRAMITE",
            compare_row_number,
        )
    except Exception as exc:
        logger.warning("[FORM] No se pudo actualizar campos de error de trámite en comparación: %s", exc)


def _registrar_estado_post_guardar_en_comparacion(
    logger: logging.Logger,
    compare_url: str,
    compare_row_number: int,
    compare_fieldnames: list[str],
    fecha_tramite: str,
) -> None:
    """Registra estado post-guardar exitoso en la hoja de comparación."""
    if not compare_url or compare_row_number <= 0:
        return

    estado_post = str(os.getenv("CARNET_ESTADO_POST_GUARDAR", "POR TRAMSMITIR") or "POR TRAMSMITIR").strip()
    observacion_post = str(os.getenv("CARNET_OBSERVACION_POST_GUARDAR", "SOLICITUD GUARDADA") or "SOLICITUD GUARDADA").strip()

    try:
        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            compare_row_number,
            {
                "observacion": observacion_post,
                "observación": observacion_post,
                "estado_tramite": estado_post,
                "estado tramite": estado_post,
                "responsable": "BOT CARNÉ SUCAMEC",
                "fecha tramite": str(fecha_tramite or "").strip(),
                "fecha_tramite": str(fecha_tramite or "").strip(),
            },
            fieldnames=compare_fieldnames,
        )
        logger.info(
            "[FORM] Fila comparacion %s actualizada a estado post-guardar: %s",
            compare_row_number,
            estado_post,
        )
    except Exception as exc:
        logger.warning("[FORM] No se pudo actualizar estado post-guardar en comparación: %s", exc)


def _registrar_estado_en_transmision_en_comparacion(
    logger: logging.Logger,
    compare_url: str,
    compare_row_number: int,
    compare_fieldnames: list[str],
    fecha_tramite: str,
) -> None:
    if not compare_url or compare_row_number <= 0:
        return

    try:
        _, _, worker_tag = _worker_identity()
        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            compare_row_number,
            {
                "observacion": "SOLICITUD EN TRANSMISION",
                "observación": "SOLICITUD EN TRANSMISION",
                "estado_tramite": "EN TRANSMISION",
                "estado tramite": "EN TRANSMISION",
                "responsable": f"BOT CARNÉ SUCAMEC {worker_tag}",
                "fecha tramite": str(fecha_tramite or "").strip(),
                "fecha_tramite": str(fecha_tramite or "").strip(),
            },
            fieldnames=compare_fieldnames,
        )
        logger.info("[FORM] Fila comparacion %s marcada EN TRANSMISION", compare_row_number)
    except Exception as exc:
        logger.warning("[FORM] No se pudo actualizar estado EN TRANSMISION en comparación: %s", exc)


def _registrar_estado_transmitido_en_comparacion(
    logger: logging.Logger,
    compare_url: str,
    compare_row_number: int,
    compare_fieldnames: list[str],
    fecha_tramite: str,
    responsable: str = "BOT CARNÉ SUCAMEC",
) -> None:
    if not compare_url or compare_row_number <= 0:
        return

    estado_post = "TRANSMITIDO"
    observacion_post = str(
        os.getenv("CARNET_OBSERVACION_POST_TRANSMITIR", "Transmitido sin observaciones")
        or "Transmitido sin observaciones"
    ).strip()
    try:
        _actualizar_fila_comparacion_por_row(
            logger,
            compare_url,
            compare_row_number,
            {
                "observacion": observacion_post,
                "estado_tramite": estado_post,
                "responsable": str(responsable or "BOT CARNÉ SUCAMEC"),
                "fecha_tramite": str(fecha_tramite or "").strip(),
            },
            fieldnames=compare_fieldnames,
        )
    except Exception:
        pass


def _marcar_secuencia_usada_en_tercera_hoja(
    logger: logging.Logger,
    item: dict,
    dni: str,
    nombre_completo: str,
) -> None:
    """Marca la secuencia usada en tercera hoja y completa trazabilidad básica."""
    tercera_url = str(item.get("tercera_url", "") or "").strip()
    tercera_row = int(item.get("tercera_row_number", 0) or 0)
    third_fieldnames = item.get("fieldnames_third", []) or []
    col_estado = item.get("col_third_estado_sec")
    col_solicitado_por = item.get("col_third_solicitado_por")
    col_apellidos_nombre = item.get("col_third_apellidos_nombre")
    col_dni = item.get("col_third_dni")

    if not tercera_url or tercera_row <= 0:
        logger.info("[FORM] Sin fila válida en tercera hoja para marcar secuencia usada")
        return

    updates = {}
    if col_estado:
        updates[col_estado] = "USADO"
    if col_solicitado_por:
        updates[col_solicitado_por] = "BOT CARNÉ SUCAMEC"
    if col_dni:
        updates[col_dni] = str(dni or "").strip()
    if col_apellidos_nombre:
        updates[col_apellidos_nombre] = str(nombre_completo or "").strip()

    if not updates:
        logger.warning("[FORM] No se resolvieron columnas de tercera hoja para marcar secuencia usada")
        return

    try:
        _actualizar_fila_tercera_hoja_por_row(
            logger,
            tercera_url,
            tercera_row,
            updates,
            fieldnames=third_fieldnames,
        )
        logger.info("[FORM] Tercera hoja actualizada en fila %s con ESTADO SECUENCIA DE PAGO=USADO", tercera_row)
    except Exception as exc:
        logger.warning("[FORM] No se pudo actualizar tercera hoja para secuencia usada: %s", exc)


def guardar_solicitud_creada(page, logger: logging.Logger) -> tuple[bool, str]:
    """Acciona el botón Guardar en Crear Solicitud y espera finalización AJAX."""
    try:
        btn = page.locator(SEL["crear_solicitud_guardar_button"]).first
        btn.wait_for(state="visible", timeout=12000)
    except Exception as exc:
        return False, f"No se encontró botón Guardar en Crear Solicitud: {exc}"

    try:
        aria_disabled = str(btn.get_attribute("aria-disabled") or "").strip().lower()
        if aria_disabled == "true":
            return False, "Botón Guardar está deshabilitado"
    except Exception:
        pass

    try:
        btn.click(timeout=9000)
    except Exception:
        try:
            ok_js = page.evaluate(
                """() => {
                    const b = document.getElementById('createForm:botonGuardar');
                    if (!b) return false;
                    b.click();
                    return true;
                }"""
            )
            if not ok_js:
                return False, "No se pudo ejecutar click en botón Guardar"
        except Exception as exc:
            return False, f"Falló click en botón Guardar: {exc}"

    esperar_ajax_primefaces(page, timeout_ms=9000)
    page.wait_for_timeout(350)
    logger.info("[FORM] Botón Guardar accionado correctamente")
    return True, ""


def navegar_dssp_carne_bandeja_carnes(page, logger: logging.Logger) -> None:
    """Navega por DSSP -> CARNÉ -> BANDEJA DE EMISIÓN (Bandeja de Carnés)."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass

    # Fast-path por texto del link JSF.
    try:
        page.locator(SEL["menu_root"]).first.wait_for(state="visible", timeout=5000)
        click_directo = page.evaluate(
            """() => {
                const textosObjetivo = ['BANDEJA DE EMISIÓN', 'BANDEJA DE EMISION', 'BANDEJA DE CARNÉS', 'BANDEJA DE CARNES'];
                const anchors = Array.from(document.querySelectorAll('a[onclick*="addSubmitParam"][onclick*="menuprincipal"], a[onclick*="addSubmitParam"][onclick*="menuPrincipal"]'));
                const target = anchors.find((a) => {
                    const t = ((a.textContent || '').replace(/\\s+/g, ' ').trim().toUpperCase());
                    return textosObjetivo.some((x) => t === x || t.includes(x));
                });
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        if click_directo:
            logger.info("Fast-path: click directo en BANDEJA DE EMISIÓN")
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pass
            esperar_ajax_primefaces(page, timeout_ms=5000)
            return
    except Exception:
        pass

    root = page.locator(SEL["menu_root"]).first
    root.wait_for(state="visible", timeout=12000)

    header_dssp = root.locator(SEL["menu_header_dssp"]).first
    header_dssp.wait_for(state="visible", timeout=8000)

    aria_expanded = (header_dssp.get_attribute("aria-expanded") or "").strip().lower()
    if aria_expanded != "true":
        header_dssp.click(timeout=8000)
        page.wait_for_timeout(250)
    logger.info("Menú DSSP listo para navegación a bandeja")

    item_carne = root.locator(SEL["menu_item_carne"]).first
    item_carne.wait_for(state="visible", timeout=8000)
    item_carne.click(timeout=8000)

    item_bandeja = root.locator(SEL["menu_item_bandeja_emision_onclick"]).first
    try:
        item_bandeja.wait_for(state="visible", timeout=4000)
    except Exception:
        item_carne.click(timeout=8000)
        try:
            item_bandeja.wait_for(state="visible", timeout=3500)
        except Exception:
            item_bandeja = root.locator(SEL["menu_item_bandeja_emision"]).first
            item_bandeja.wait_for(state="visible", timeout=6000)

    item_bandeja.click(timeout=10000)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    esperar_ajax_primefaces(page, timeout_ms=6000)
    logger.info("Click en BANDEJA DE EMISIÓN ejecutado")


def seleccionar_estado_bandeja(page, logger: logging.Logger, estado_objetivo: str = "CREADO") -> None:
    """En Bandeja de Carnés selecciona Estado en el combo listForm:tipoFormacion."""
    seleccionar_opcion_primefaces(
        page,
        trigger_selector=SEL["bandeja_estado_trigger"],
        panel_selector=SEL["bandeja_estado_panel"],
        label_selector=SEL["bandeja_estado_label"],
        valor=estado_objetivo,
        nombre_campo="Estado bandeja",
    )
    logger.info("[BANDEJA] Estado aplicado en filtro: %s", estado_objetivo)

    try:
        btn_buscar = page.locator(SEL["bandeja_buscar_button"]).first
        btn_buscar.wait_for(state="visible", timeout=5000)
        btn_buscar.click(timeout=7000)
        esperar_ajax_primefaces(page, timeout_ms=9000)
        page.wait_for_timeout(350)
        logger.info("[BANDEJA] Búsqueda ejecutada con Estado=%s", estado_objetivo)
    except Exception as exc:
        raise Exception(f"No se pudo accionar Buscar en bandeja: {exc}")


def _fila_bandeja_por_dni(page, dni: str):
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    if not dni_digits:
        return None

    tbody = page.locator(SEL["bandeja_resultados_tbody"]).first
    tbody.wait_for(state="visible", timeout=8000)
    filas = tbody.locator("tr")

    try:
        total = int(filas.count())
    except Exception:
        total = 0

    for idx in range(total):
        fila = filas.nth(idx)
        try:
            txt = str(fila.inner_text() or "").strip()
        except Exception:
            txt = ""
        if not txt:
            continue
        txt_digits = "".join(ch for ch in txt if ch.isdigit())
        if dni_digits and dni_digits in txt_digits:
            return fila
    return None


def existe_registro_en_bandeja_por_dni(page, dni: str) -> bool:
    return _fila_bandeja_por_dni(page, dni) is not None


def seleccionar_registro_bandeja_por_dni(page, logger: logging.Logger, dni: str) -> None:
    """Selecciona solo el checkbox de la fila cuyo texto contiene el DNI del registro actual."""
    fila = _fila_bandeja_por_dni(page, dni)
    if fila is None:
        raise Exception(f"No se encontró fila en bandeja para DNI={dni}")

    chk = fila.locator("td.ui-selection-column .ui-chkbox-box").first
    chk.wait_for(state="visible", timeout=7000)
    cls = str(chk.get_attribute("class") or "")
    if "ui-state-active" not in cls:
        chk.click(timeout=7000)
        esperar_ajax_primefaces(page, timeout_ms=5000)
        page.wait_for_timeout(250)

    cls_final = str(chk.get_attribute("class") or "")
    if "ui-state-active" not in cls_final:
        raise Exception(f"No se logró activar checkbox de fila para DNI={dni}")

    logger.info("[BANDEJA] Checkbox de fila activado para DNI=%s", dni)


def seleccionar_todos_resultados_bandeja(page, logger: logging.Logger) -> None:
    """Selecciona el checkbox de cabecera para marcar todos los registros de la tabla."""
    tbody = page.locator(SEL["bandeja_resultados_tbody"]).first
    tbody.wait_for(state="visible", timeout=8000)

    # Si no hay filas, no hay nada que transmitir.
    row_count = 0
    try:
        row_count = tbody.locator("tr").count()
    except Exception:
        row_count = 0
    if row_count <= 0:
        raise Exception("La tabla de resultados no tiene filas para seleccionar")

    chk = page.locator(SEL["bandeja_select_all_checkbox"]).first
    chk.wait_for(state="visible", timeout=7000)
    cls = str(chk.get_attribute("class") or "")
    if "ui-state-active" not in cls:
        chk.click(timeout=7000)
        esperar_ajax_primefaces(page, timeout_ms=5000)
        page.wait_for_timeout(250)

    cls_final = str(chk.get_attribute("class") or "")
    if "ui-state-active" not in cls_final:
        raise Exception("No se logró activar el checkbox de selección total")

    logger.info("[BANDEJA] Checkbox de selección total activado")


def transmitir_resultados_bandeja(page, logger: logging.Logger) -> None:
    """Acciona el botón Transmitir en la bandeja luego de seleccionar registros."""
    btn = page.locator(SEL["bandeja_transmitir_button"]).first
    btn.wait_for(state="visible", timeout=9000)

    try:
        aria_disabled = str(btn.get_attribute("aria-disabled") or "").strip().lower()
        if aria_disabled == "true":
            raise Exception("Botón Transmitir deshabilitado")
    except Exception as exc:
        if "deshabilitado" in str(exc):
            raise

    btn.click(timeout=9000)
    esperar_ajax_primefaces(page, timeout_ms=9000)
    page.wait_for_timeout(400)

    # Confirmación final en modal "Transmisión de registros".
    dlg = page.locator(SEL["bandeja_transmitir_confirm_dialog"]).first
    dlg.wait_for(state="visible", timeout=9000)

    btn_confirmar = page.locator(SEL["bandeja_transmitir_confirm_button"]).first
    btn_confirmar.wait_for(state="visible", timeout=7000)

    aria_disabled_confirm = str(btn_confirmar.get_attribute("aria-disabled") or "").strip().lower()
    if aria_disabled_confirm == "true":
        raise Exception("Botón de confirmación Transmitir en modal está deshabilitado")

    btn_confirmar.click(timeout=9000)
    esperar_ajax_primefaces(page, timeout_ms=12000)
    page.wait_for_timeout(450)

    try:
        dlg.wait_for(state="hidden", timeout=6000)
    except Exception:
        logger.warning("[BANDEJA] El diálogo de confirmación no se ocultó a tiempo tras confirmar transmisión")

    logger.info("[BANDEJA] Transmisión confirmada en modal")


def reintentar_busqueda_con_cambio_empresa(page, logger: logging.Logger, dni: str, max_wait_ms: int = 1200) -> None:
    """
    Sub-validación: si se detecta alerta que exige CAMBIO DE EMPRESA, cambia el tipo y rebusca.
    Usa polling activo con deadline (5 segundos) para capturar mensajes confiablemente.
    """
    # Asegurar que el monitor está activo (monitorea en background)
    activar_monitor_carnet_growl(page)
    
    # Verificación corta para no penalizar el flujo base cuando no hay mensaje.
    encontrado, msg = detectar_mensaje_carne_cesado(page, max_wait_ms=max_wait_ms)
    
    if not encontrado:
        return

    logger.warning(
        "[SUB-VALIDACION] *** DETECTADA alerta de CAMBIO DE EMPRESA: %s",
        msg,
    )
    logger.info("[SUB-VALIDACION] Cambiando Tipo de Registro a CAMBIO DE EMPRESA...")
    seleccionar_tipo_registro(page, "CAMBIO DE EMPRESA")
    logger.info("[SUB-VALIDACION] Tipo de Registro cambiado a CAMBIO DE EMPRESA")

    dni_limpio = "".join(ch for ch in str(dni or "") if ch.isdigit())
    logger.info("[SUB-VALIDACION] Reintentando Buscar con DNI=%s tipo CAMBIO DE EMPRESA", dni_limpio)
    ingresar_documento_y_buscar(page, dni_limpio)
    logger.info("[SUB-VALIDACION] Búsqueda reintentada con CAMBIO DE EMPRESA")


def validar_autocompletado_datos_inicial(page, logger: logging.Logger) -> tuple[bool, str]:
    """Valida que Nombres/Apellidos se hayan cargado cuando el tipo de registro es INICIAL."""
    try:
        tipo_reg_text = (page.locator(SEL["crear_solicitud_tipo_registro_label"]).first.inner_text() or "").strip()
    except Exception:
        tipo_reg_text = ""

    tipo_reg_norm = _normalizar_columna(tipo_reg_text)
    if tipo_reg_norm != "inicial":
        return True, ""

    def _leer_valor(selector: str) -> str:
        try:
            loc = page.locator(selector).first
            loc.wait_for(state="attached", timeout=4000)
            return str(loc.input_value() or "").strip()
        except Exception:
            return ""

    nombres = _leer_valor(SEL["crear_solicitud_nombres_input"])
    ape_pat = _leer_valor(SEL["crear_solicitud_ape_pat_input"])
    ape_mat = _leer_valor(SEL["crear_solicitud_ape_mat_input"])

    if nombres and ape_pat and ape_mat:
        logger.info(
            "[FORM] [INICIAL] Datos autocompletados OK | NOMBRES=%s | APE_PAT=%s | APE_MAT=%s",
            nombres,
            ape_pat,
            ape_mat,
        )
        return True, ""

    return False, "Registro INICIAL sin autocompletado de Nombres/Apellido Paterno/Apellido Materno"


def detectar_resultado_verificacion_comprobante(page, max_wait_ms: int = 5000, min_ts_ms: int | None = None) -> tuple[str, str]:
    """
    Detecta el resultado de click al botón Verificar (Comprobante).
    
    Retorna:
    - ("ENCONTRADO", "Recibo encontrado") → Éxito, continuar
    - ("NO_ENCONTRADO", "No se encontró el recibo") → Error, fallback
    - ("TIMEOUT", "") → No se detectó nada en tiempo límite
    
    Usa polling activo con estrategia multinivel (Etiqueta Monto/Fecha + Buffer JS + DOM + HTML).
    Si min_ts_ms se define, ignora mensajes del buffer growl anteriores a ese timestamp.
    """
    deadline = time.time() + (max(0, int(max_wait_ms)) / 1000.0)
    
    while True:
        if time.time() >= deadline:
            return "TIMEOUT", ""
        
        # 0. Validación positiva robusta por etiqueta de monto/fecha.
        etiqueta_ok = _detectar_etiqueta_recibo_valido(page)
        if etiqueta_ok:
            return "ENCONTRADO", etiqueta_ok

        # 1. Buffer JS
        try:
            buffer = obtener_buffer_carnet_growl(page) or []
            for msg_dict in buffer:
                if min_ts_ms is not None:
                    try:
                        ts = int(msg_dict.get("ts", 0) or 0)
                        if ts and ts < int(min_ts_ms):
                            continue
                    except Exception:
                        pass
                msg_text = str(msg_dict.get("text", "") or "").lower()
                if "recibo encontrado" in msg_text:
                    return "ENCONTRADO", "Recibo encontrado"
                if "no se encontró" in msg_text and "recibo" in msg_text:
                    return "NO_ENCONTRADO", "No se encontró el recibo"
        except Exception:
            pass
        
        # 2. DOM actual
        try:
            growl_items = page.locator(".ui-growl-item").all()
            for item in growl_items:
                title_text = (item.locator(".ui-growl-title").inner_text() or "").lower()
                if "recibo encontrado" in title_text:
                    return "ENCONTRADO", "Recibo encontrado"
                if "no se encontró" in title_text:
                    return "NO_ENCONTRADO", "No se encontró el recibo"
        except Exception:
            pass
        
        # 3. HTML de página
        try:
            html = (page.content() or "").lower()
            if "recibo encontrado" in html:
                return "ENCONTRADO", "Recibo encontrado"
            if "no se encontró" in html and "recibo" in html:
                return "NO_ENCONTRADO", "No se encontró el recibo"
        except Exception:
            pass
        
        time.sleep(0.1)


def procesar_registro_cruce_en_formulario(page, logger: logging.Logger, item: dict) -> bool:
    """Aplica la sede de atención y actualiza la fila de comparación para un registro individual."""
    # Activar monitor de growl al inicio para capturar mensajes en background
    if page is not None:
        activar_monitor_carnet_growl(page)
    
    compare_url = str(item.get("compare_url", "") or os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()
    compare_row_number = int(item.get("compare_row_number", 0) or item.get("row_number", 0) or 0)
    compare_fieldnames = item.get("fieldnames_compare", item.get("compare_fieldnames", [])) or []
    fecha_hoy = str(item.get("fecha_tramite", "") or datetime.now().strftime("%d/%m/%Y")).strip()
    base_row = item.get("base_row")
    dni = str(item.get("dni", "") or "").strip()

    if not base_row:
        if compare_url and compare_row_number > 0:
            _actualizar_fila_comparacion_por_row(
                logger,
                compare_url,
                compare_row_number,
                {
                    "observacion": f"DNI {dni} no encontrado en la hoja base",
                    "fecha tramite": fecha_hoy,
                },
                fieldnames=compare_fieldnames,
            )
        return False

    # Valida documentos reales del expediente en Drive por DNI antes de continuar.
    try:
        drive_ok, drive_docs = validar_documentos_drive_por_dni(logger, dni)
    except Exception as exc:
        drive_ok, drive_docs = False, []
        logger.warning("[DRIVE][%s] Falló validación de documentos: %s", dni, exc)

    if not drive_ok:
        msg_drive = f"No se encontraron documentos del expediente en Drive para DNI {dni} (pdf/png/jpg/jpeg)"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_drive,
            fecha_hoy,
        )
        return False

    item["drive_docs_disponibles"] = drive_docs

    try:
        foto_ok, foto_local_path, foto_nombre = preparar_foto_local_desde_drive(logger, dni)
    except Exception as exc:
        foto_ok, foto_local_path, foto_nombre = False, None, str(exc)

    if not foto_ok or foto_local_path is None:
        msg_foto = f"No se pudo extraer foto del expediente Drive para DNI {dni}: {foto_nombre}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_foto,
            fecha_hoy,
        )
        return False

    item["drive_foto_local_path"] = str(foto_local_path)
    item["drive_foto_nombre"] = foto_nombre

    try:
        djfut_ok, djfut_local_path, djfut_nombre = preparar_djfut_local_desde_drive(logger, dni)
    except Exception as exc:
        djfut_ok, djfut_local_path, djfut_nombre = False, None, str(exc)

    if not djfut_ok or djfut_local_path is None:
        msg_djfut = f"No se pudo extraer DJFUT del expediente Drive para DNI {dni}: {djfut_nombre}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_djfut,
            fecha_hoy,
        )
        return False

    item["drive_djfut_local_path"] = str(djfut_local_path)
    item["drive_djfut_nombre"] = djfut_nombre

    sede_sugerida = str(item.get("sede", "") or "").strip() or "LIMA"
    sede = sede_sugerida
    origen_dropdown = "no_verificado_dropdown"
    if page is not None:
        sede, origen_dropdown = resolver_sede_para_dropdown(
            page,
            str(item.get("departamento", "") or "").strip(),
            sede_sugerida,
        )

    logger.info(
        "[FORM] DNI=%s | DEPARTAMENTO=%s | SEDE_SUGERIDA=%s | SEDE_APLICADA=%s | ORIGEN=%s | ORIGEN_DROPDOWN=%s",
        dni,
        item.get("departamento", ""),
        sede_sugerida,
        sede,
        item.get("origen_sede", ""),
        origen_dropdown,
    )
    seleccionar_sede_atencion(page, sede)
    logger.info("[FORM] Sede de atención aplicada en la vista: %s", sede)

    modalidad_objetivo = str(item.get("modalidad_objetivo", "") or "").strip() or "VIGILANCIA PRIVADA"
    modalidad_aplicada = modalidad_objetivo
    modalidad_dropdown_origen = "modalidad:no_verificado_dropdown"
    if page is not None:
        modalidad_aplicada, modalidad_dropdown_origen = resolver_modalidad_para_dropdown(page, modalidad_objetivo)

    logger.info(
        "[FORM] DNI=%s | PUESTO=%s | MODALIDAD_OBJETIVO=%s | MODALIDAD_APLICADA=%s | MODALIDAD_ORIGEN=%s | MODALIDAD_ORIGEN_DROPDOWN=%s",
        dni,
        item.get("puesto", ""),
        modalidad_objetivo,
        modalidad_aplicada,
        item.get("modalidad_origen", ""),
        modalidad_dropdown_origen,
    )
    seleccionar_modalidad(page, modalidad_aplicada)
    logger.info("[FORM] Modalidad aplicada en la vista: %s", modalidad_aplicada)

    # Validar tipo de registro sin forzar CAMBIO; el ajuste a CAMBIO se hace
    # solo cuando la sub-validacion detecta carné cesado tras Buscar.
    try:
        tipo_reg_label = page.locator(SEL["crear_solicitud_tipo_registro_label"]).first
        tipo_reg_text = (tipo_reg_label.inner_text() or "").strip()
        logger.info("[FORM] Tipo de Registro preseleccionado: %s", tipo_reg_text)
        tipo_reg_norm = _normalizar_columna(tipo_reg_text)
        if not tipo_reg_text or "seleccione" in tipo_reg_norm or tipo_reg_text == "---":
            logger.info("[FORM] Tipo de Registro no esta seleccionado; seleccionando INICIAL...")
            seleccionar_tipo_registro(page, "INICIAL")
            page.wait_for_timeout(300)
            tipo_reg_text = (page.locator(SEL["crear_solicitud_tipo_registro_label"]).first.inner_text() or "").strip()
            logger.info("[FORM] Tipo de Registro despues: %s", tipo_reg_text)
        elif tipo_reg_norm not in {"inicial", "cambio de empresa"}:
            logger.info("[FORM] Tipo de Registro fuera de objetivo (%s); ajustando a INICIAL", tipo_reg_text)
            seleccionar_tipo_registro(page, "INICIAL")
            page.wait_for_timeout(300)
            tipo_reg_text = (page.locator(SEL["crear_solicitud_tipo_registro_label"]).first.inner_text() or "").strip()
            logger.info("[FORM] Tipo de Registro despues de ajuste: %s", tipo_reg_text)
    except Exception as exc:
        logger.warning("[FORM] Error validando Tipo de Registro: %s", exc)

    tipo_doc_objetivo = str(item.get("tipo_doc_objetivo", "") or "").strip()
    dni_normalizado_tipo_doc = str(item.get("dni_normalizado_tipo_doc", "") or "").strip()
    tipo_doc_origen = str(item.get("tipo_doc_origen", "") or "").strip()
    if not tipo_doc_objetivo:
        tipo_doc_objetivo, dni_normalizado_tipo_doc, tipo_doc_origen = resolver_tipo_documento_desde_dni(dni)
    logger.info(
        "[FORM] DNI_RAW=%s | DNI_NORMALIZADO=%s | TIPO_DOC_OBJETIVO=%s | TIPO_DOC_ORIGEN=%s",
        dni,
        dni_normalizado_tipo_doc,
        tipo_doc_objetivo,
        tipo_doc_origen,
    )
    seleccionar_tipo_documento(page, tipo_doc_objetivo)
    logger.info("[FORM] Tipo de Documento aplicado en la vista: %s", tipo_doc_objetivo)

    # Restaurar flujo base: primero Documento + Buscar.
    dni_limpio = "".join(ch for ch in (dni or "") if ch.isdigit())
    logger.info("[FORM] Procediendo con búsqueda de documento...")
    logger.info("[FORM] Ingresando DNI: %s", dni_limpio)
    ts_busqueda_ms = int(time.time() * 1000)
    ingresar_documento_y_buscar(page, dni_limpio)
    logger.info("[FORM] Búsqueda de documento ejecutada")

    # Timeout corto y configurable para validaciones post-Buscar.
    post_search_wait_ms = max(300, _safe_int_env("CARNET_POST_SEARCH_ALERT_WAIT_MS", 1200))

    # Validacion pendiente 3/3: DNI inexistente en SUCAMEC.
    documento_no_existe, msg_doc_no_existe = detectar_error_documento_no_existe(
        page,
        max_wait_ms=post_search_wait_ms,
        min_ts_ms=ts_busqueda_ms,
    )
    if documento_no_existe:
        msg_tramite = (
            msg_doc_no_existe.strip()
            if str(msg_doc_no_existe or "").strip()
            else "El documento ingresado no existe"
        )
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_tramite)

        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_tramite,
            fecha_hoy,
        )

        return False

    # Validacion pendiente 4/4: carné vigente en otra empresa.
    carne_vigente_otra_empresa, msg_carne_vigente_otra_empresa = detectar_error_carne_vigente_otra_empresa(
        page,
        max_wait_ms=post_search_wait_ms,
        min_ts_ms=ts_busqueda_ms,
    )
    if carne_vigente_otra_empresa:
        msg_tramite = (
            msg_carne_vigente_otra_empresa.strip()
            if str(msg_carne_vigente_otra_empresa or "").strip()
            else "Esta persona no puede sacar un carné de personal de seguridad con esta empresa porque ya cuenta con uno en una distinta empresa"
        )
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_tramite)

        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_tramite,
            fecha_hoy,
        )

        return False

    # Si aparece carné cesado, esta rutina cambia a CAMBIO DE EMPRESA y reintenta Buscar.
    reintentar_busqueda_con_cambio_empresa(page, logger, dni_limpio, max_wait_ms=post_search_wait_ms)

    # Validación adicional: ya existe registro en la misma modalidad en estado TRANSMITIDO.
    transmitido, msg_transmitido = detectar_error_tramite_transmitido(
        page,
        max_wait_ms=post_search_wait_ms,
        min_ts_ms=ts_busqueda_ms,
    )
    if transmitido:
        msg_tramite = (
            msg_transmitido.strip()
            if str(msg_transmitido or "").strip()
            else "Este personal de seguridad cuenta con un registro en la misma modalidad en estado TRANSMITIDO"
        )
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_tramite)

        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_tramite,
            fecha_hoy,
        )

        return False

    # Validacion pendiente 1/3: registro en misma modalidad en estado OBSERVADO.
    observado, msg_observado = detectar_error_tramite_observado(page, max_wait_ms=post_search_wait_ms, min_ts_ms=ts_busqueda_ms)
    if observado:
        msg_tramite = (
            msg_observado.strip()
            if str(msg_observado or "").strip()
            else "Este personal de seguridad cuenta con un registro en la misma modalidad en estado OBSERVADO"
        )
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_tramite)

        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_tramite,
            fecha_hoy,
        )

        return False

    # Validacion pendiente 2/3: prospecto sin curso SUCAMEC vigente.
    curso_no_vigente, msg_curso = detectar_error_curso_no_vigente(page, max_wait_ms=post_search_wait_ms, min_ts_ms=ts_busqueda_ms)
    if curso_no_vigente:
        msg_tramite = (
            msg_curso.strip()
            if str(msg_curso or "").strip()
            else "El prospecto no cuenta con curso vigente"
        )
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_tramite)

        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_tramite,
            fecha_hoy,
        )

        return False

    # Si el tipo de registro quedó en INICIAL, validar que SUCAMEC haya autocompletado datos personales.
    ok_inicial, msg_inicial = validar_autocompletado_datos_inicial(page, logger)
    if not ok_inicial:
        logger.warning("[FORM] [ERROR_TRAMITE] %s", msg_inicial)
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_inicial,
            fecha_hoy,
        )
        return False

    foto_local_path = str(item.get("drive_foto_local_path", "") or "").strip()
    if not foto_local_path:
        logger.warning("[FORM][FOTO] No se preparó archivo local de foto para DNI %s. Se continúa flujo.", dni)
    else:
        try:
            foto_confirmada, foto_mensaje = cargar_archivo_foto_en_formulario(page, logger, Path(foto_local_path))
            if not foto_confirmada:
                msg_foto = foto_mensaje or f"No se pudo validar la carga de foto para DNI {dni}"
                _registrar_error_tramite_en_comparacion(
                    logger,
                    compare_url,
                    compare_row_number,
                    compare_fieldnames,
                    msg_foto,
                    fecha_hoy,
                )
                return False
        except Exception as exc:
            msg_foto = f"Error al cargar foto en formulario para DNI {dni}: {exc}"
            logger.warning("[FORM][FOTO] %s", msg_foto)
            _registrar_error_tramite_en_comparacion(
                logger,
                compare_url,
                compare_row_number,
                compare_fieldnames,
                msg_foto,
                fecha_hoy,
            )
            return False

    djfut_local_path = str(item.get("drive_djfut_local_path", "") or "").strip()
    if not djfut_local_path:
        logger.warning("[FORM][DJFUT] No se preparó archivo local DJFUT para DNI %s", dni)
        msg_djfut = f"No se preparó archivo local DJFUT para DNI {dni}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_djfut,
            fecha_hoy,
        )
        return False

    try:
        djfut_confirmado, djfut_mensaje = cargar_archivo_djfut_en_formulario(page, logger, Path(djfut_local_path))
        if not djfut_confirmado:
            msg_djfut = djfut_mensaje or f"No se confirmó la carga del DJFUT para DNI {dni}"
            _registrar_error_tramite_en_comparacion(
                logger,
                compare_url,
                compare_row_number,
                compare_fieldnames,
                msg_djfut,
                fecha_hoy,
            )
            return False
    except Exception as exc:
        msg_djfut = f"Error al cargar DJFUT en formulario para DNI {dni}: {exc}"
        logger.warning("[FORM][DJFUT] %s", msg_djfut)
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_djfut,
            fecha_hoy,
        )
        return False

    try:
        cert_ok, cert_local_path, cert_nombre = preparar_certificado_medico_local_desde_drive(logger, dni)
    except Exception as exc:
        cert_ok, cert_local_path, cert_nombre = False, None, str(exc)

    if not cert_ok or cert_local_path is None:
        msg_cert = f"No se pudo extraer certificado médico del expediente Drive para DNI {dni}: {cert_nombre}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_cert,
            fecha_hoy,
        )
        return False

    item["drive_certificado_medico_local_path"] = str(cert_local_path)
    item["drive_certificado_medico_nombre"] = cert_nombre

    try:
        cert_confirmado, cert_mensaje = cargar_archivo_certificado_medico_en_formulario(page, logger, Path(cert_local_path))
        if not cert_confirmado:
            msg_cert = cert_mensaje or f"No se confirmó la carga del certificado médico para DNI {dni}"
            _registrar_error_tramite_en_comparacion(
                logger,
                compare_url,
                compare_row_number,
                compare_fieldnames,
                msg_cert,
                fecha_hoy,
            )
            return False
    except Exception as exc:
        msg_cert = f"Error al cargar certificado médico en formulario para DNI {dni}: {exc}"
        logger.warning("[FORM][CERT_MED] %s", msg_cert)
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_cert,
            fecha_hoy,
        )
        return False

    # Reserva de secuencias por worker para evitar colisiones entre hilos/procesos.
    tercera_url = str(item.get("tercera_url", "") or os.getenv("CARNET_GSHEET_THIRD_URL", DEFAULT_GSHEET_THIRD_URL) or "").strip()
    if not tercera_url:
        logger.error("[FORM] No se configuró CARNET_GSHEET_THIRD_URL para reservar secuencia")
        return False

    secuencia_exitosa = False
    filas_secuencia_probadas: set[int] = set()
    max_intentos_secuencia = max(1, _safe_int_env("CARNET_MAX_SECUENCIA_INTENTOS", 40))
    secuencia_preasignada_consumida = False
    for intento_num in range(1, max_intentos_secuencia + 1):
        reservado = None
        if not secuencia_preasignada_consumida:
            nro_pre = str(item.get("nro_secuencia_objetivo", "") or item.get("copia_secuencia_pago", "") or "").strip()
            token_pre = str(item.get("tercera_reserva_token", "") or "").strip()
            row_pre = int(item.get("tercera_row_number", 0) or 0)
            if nro_pre and token_pre and row_pre > 0:
                reservado = {
                    "row_number": row_pre,
                    "token": token_pre,
                    "copia_secuencia_pago_raw": str(item.get("copia_secuencia_pago_raw", "") or nro_pre),
                    "copia_secuencia_pago": nro_pre,
                }
                secuencia_preasignada_consumida = True

        if reservado is None:
            reservado = _reservar_siguiente_secuencia_para_worker(
                logger,
                item,
                dni,
                filas_excluidas=filas_secuencia_probadas,
            )
        if not reservado:
            logger.warning("[FORM] Sin secuencias libres/reservables para DNI=%s tras %s intentos", dni, intento_num - 1)
            break

        nro_sec = str(reservado.get("copia_secuencia_pago", "") or "").strip()
        nro_sec_raw = str(reservado.get("copia_secuencia_pago_raw", "") or "").strip()
        tercera_row = int(reservado.get("row_number", 0) or 0)
        filas_secuencia_probadas.add(tercera_row)

        logger.info(
            "[FORM] Intento secuencia %s/%s: %s | TERCERA_FILA=%s",
            intento_num,
            max_intentos_secuencia,
            nro_sec,
            tercera_row if tercera_row > 0 else "N/A",
        )
        limpiar_buffer_carnet_growl(page)
        ts_intento_ms = int(time.time() * 1000)
        ingresar_copia_secuencia_pago(page, nro_sec)

        # Esperar AJAX y detectar resultado (AMBOS mensajes).
        page.wait_for_timeout(800)
        resultado, _msg = detectar_resultado_verificacion_comprobante(page, max_wait_ms=6000, min_ts_ms=ts_intento_ms)

        if resultado == "ENCONTRADO":
            logger.info(
                "[FORM] [OK] SECUENCIA %s VALIDA EN SUCAMEC | TERCERA_FILA=%s",
                nro_sec,
                tercera_row if tercera_row > 0 else "N/A",
            )
            secuencia_exitosa = True
            item["copia_secuencia_pago"] = nro_sec
            item["copia_secuencia_pago_raw"] = nro_sec_raw
            item["tercera_row_number"] = tercera_row
            break

        if resultado == "NO_ENCONTRADO":
            logger.warning(
                "[FORM] [ERROR] SECUENCIA %s NO ENCONTRADA EN SUCAMEC | TERCERA_FILA=%s",
                nro_sec,
                tercera_row if tercera_row > 0 else "N/A",
            )
            col_third_estado = item.get("col_third_estado_sec")
            third_fieldnames = item.get("fieldnames_third", []) or []
            if tercera_url and tercera_row > 0 and col_third_estado:
                try:
                    _actualizar_fila_tercera_hoja_por_row(
                        logger,
                        tercera_url,
                        tercera_row,
                        {col_third_estado: "NO ENCONTRADO"},
                        third_fieldnames,
                    )
                    item["tercera_reserva_token"] = ""
                    logger.info("[FORM] Fila %s marcada como NO ENCONTRADO en tercera hoja", tercera_row)
                except Exception as exc:
                    logger.warning("[FORM] No se pudo marcar NO ENCONTRADO en tercera hoja: %s", exc)
                    _liberar_reserva_secuencia_si_aplica(logger, item)

            try:
                limpiar_campo_copia_secuencia_pago(page)
            except Exception:
                pass
            continue

        # TIMEOUT: mantener criterio conservador histórico para no bloquear el flujo.
        logger.info("[FORM] [WARN] TIMEOUT en detección; asumiendo EXITO para %s", nro_sec)
        secuencia_exitosa = True
        item["copia_secuencia_pago"] = nro_sec
        item["copia_secuencia_pago_raw"] = nro_sec_raw
        item["tercera_row_number"] = tercera_row
        break

    if not secuencia_exitosa:
        logger.error("[FORM] [FRACASO] Sin secuencia valida tras %s intentos", max(1, len(filas_secuencia_probadas)))
        if compare_url and compare_row_number > 0:
            try:
                _actualizar_fila_comparacion_por_row(
                    logger,
                    compare_url,
                    compare_row_number,
                    {
                        "observacion": f"Sin secuencia valida tras {max(1, len(filas_secuencia_probadas))} intentos",
                        "fecha_tramite": fecha_hoy,
                    },
                    fieldnames=compare_fieldnames,
                )
            except Exception:
                pass
        _liberar_reserva_secuencia_si_aplica(logger, item)
        return False

    # Capturar el nombre completo antes de Guardar para evitar leer el DOM cuando la vista ya redirige.
    try:
        nombre_form = " ".join(
            [
                str(page.locator(SEL["crear_solicitud_ape_pat_input"]).first.input_value() or "").strip(),
                str(page.locator(SEL["crear_solicitud_ape_mat_input"]).first.input_value() or "").strip(),
                str(page.locator(SEL["crear_solicitud_nombres_input"]).first.input_value() or "").strip(),
            ]
        ).strip()
    except Exception:
        nombre_form = ""
    item["nombre_formulario"] = nombre_form

    # Paso 1: Guardar solicitud en la vista Crear Solicitud.
    guardado_ok, guardado_msg = guardar_solicitud_creada(page, logger)
    if not guardado_ok:
        msg_guardar = guardado_msg or f"No se pudo accionar Guardar para DNI {dni}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_guardar,
            fecha_hoy,
        )
        return False

    # Paso 2: Actualizar Google Sheet (comparación) a estado post-guardar.
    _registrar_estado_post_guardar_en_comparacion(
        logger,
        compare_url,
        compare_row_number,
        compare_fieldnames,
        fecha_hoy,
    )

    # Paso 2.1: Marcar secuencia usada en tercera hoja (si aplica), antes de cambiar de vista.
    nombre_form = str(item.get("nombre_formulario", "") or "").strip()
    _marcar_secuencia_usada_en_tercera_hoja(logger, item, dni, nombre_form)

    # Estado intermedio para trazabilidad durante la fase de bandeja/transmisión.
    _registrar_estado_en_transmision_en_comparacion(
        logger,
        compare_url,
        compare_row_number,
        compare_fieldnames,
        fecha_hoy,
    )

    # Paso 3: Ir a Bandeja de Carnés y filtrar por estado CREADO.
    estado_bandeja_objetivo = str(os.getenv("CARNET_BANDEJA_ESTADO_OBJETIVO", "CREADO") or "CREADO").strip() or "CREADO"
    transmitido_por_otro_worker = False
    try:
        navegar_dssp_carne_bandeja_carnes(page, logger)
        seleccionar_estado_bandeja(page, logger, estado_objetivo=estado_bandeja_objetivo)
        try:
            seleccionar_registro_bandeja_por_dni(page, logger, dni)
            transmitir_resultados_bandeja(page, logger)
        except Exception as exc_seleccion:
            logger.warning(
                "[BANDEJA] No se pudo transmitir fila por DNI=%s con estado=%s: %s",
                dni,
                estado_bandeja_objetivo,
                exc_seleccion,
            )
            # Revalidación: pudo ser transmitido por otro worker entre guardado y transmisión.
            estado_transmitido = str(os.getenv("CARNET_BANDEJA_ESTADO_TRANSMITIDO", "TRANSMITIDO") or "TRANSMITIDO").strip() or "TRANSMITIDO"
            seleccionar_estado_bandeja(page, logger, estado_objetivo=estado_transmitido)
            if existe_registro_en_bandeja_por_dni(page, dni):
                transmitido_por_otro_worker = True
                logger.info("[BANDEJA] Registro DNI=%s ya aparece en estado %s (posible transmisión por otro worker)", dni, estado_transmitido)
            else:
                raise exc_seleccion
    except Exception as exc:
        msg_bandeja = f"Solicitud guardada, pero falló navegación/filtro en Bandeja ({estado_bandeja_objetivo}): {exc}"
        _registrar_error_tramite_en_comparacion(
            logger,
            compare_url,
            compare_row_number,
            compare_fieldnames,
            msg_bandeja,
            fecha_hoy,
        )
        return False

    # Actualizar hoja de comparación
    responsable_final = "BOT CARNÉ SUCAMEC"
    if transmitido_por_otro_worker:
        _, _, worker_tag = _worker_identity()
        responsable_final = f"BOT CARNÉ SUCAMEC {worker_tag} (revalidado)"
    _registrar_estado_transmitido_en_comparacion(
        logger,
        compare_url,
        compare_row_number,
        compare_fieldnames,
        fecha_hoy,
        responsable=responsable_final,
    )

    # Limpieza de temporales locales de upload solo cuando el flujo llegó a TRANSMITIDO.
    limpiar_cache_upload_tmp_por_dni(logger, dni)

    # Requisito operativo: volver a CREAR SOLICITUD para iterar el siguiente registro.
    try:
        navegar_dssp_carne_crear_solicitud(page, logger)
        vista_ok_post = validar_vista_crear_solicitud_por_ui(page, timeout_ms=3200)
        if vista_ok_post:
            logger.info("[FORM] Retorno a CREAR SOLICITUD confirmado para siguiente iteración")
        else:
            logger.warning("[FORM] Retorno a CREAR SOLICITUD no confirmado por validador, se reintentará en siguiente registro")
    except Exception as exc:
        logger.warning("[FORM] No se pudo retornar a CREAR SOLICITUD tras transmitir: %s", exc)

    logger.info("[FORM] [OK] REGISTRO COMPLETADO EXITOSAMENTE")
    return True


def previsualizar_mapeo_sedes_desde_hoja_base(logger: logging.Logger, max_rows: int = 5) -> None:
    """Muestra cómo quedaría el mapeo departamento -> sede para los primeros registros de prueba."""
    url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
    rows, fields = _leer_google_sheet_rows(url_base, logger)
    col_dni = _resolver_columna(fields, ["dni"])
    col_departamento = _resolver_columna(
        fields,
        [
            "indicar el departamento donde labora o donde postuló",
            "indicar el departamento donde labora o donde postulo",
            "departamento",
        ],
    )

    if not col_departamento:
        logger.warning("[SEDE] No se encontró la columna de departamento en la hoja base")
        return

    limite = max(1, int(max_rows or 5))
    vistos = 0
    for row in rows:
        departamento = str(row.get(col_departamento, "") or "").strip()
        dni = str(row.get(col_dni, "") or "").strip() if col_dni else ""
        if not departamento and not dni:
            continue

        sede, origen = resolver_sede_atencion_desde_departamento(departamento)
        logger.info(
            "[SEDE] Muestra %s | DNI=%s | DEPARTAMENTO=%s | SEDE=%s | ORIGEN=%s",
            vistos + 1,
            dni,
            departamento,
            sede,
            origen,
        )
        vistos += 1
        if vistos >= limite:
            break

    if vistos == 0:
        logger.warning("[SEDE] No se encontraron filas válidas para previsualizar el mapeo")


def _drive_root_folder_id() -> str:
    return str(
        os.getenv(
            "DRIVE_ROOT_FOLDER_ID",
            os.getenv("CARNET_DRIVE_ROOT_FOLDER_ID", DEFAULT_DRIVE_ROOT_FOLDER_ID),
        )
        or ""
    ).strip()


def _drive_service():
    try:
        service_account = importlib.import_module("google.oauth2.service_account")
        google_build = importlib.import_module("googleapiclient.discovery").build
    except Exception as exc:
        raise Exception("Faltan dependencias de Google Drive API. Instala google-api-python-client y google-auth") from exc

    credentials_path = str(os.getenv("DRIVE_CREDENTIALS_JSON", DEFAULT_DRIVE_CREDENTIALS_JSON) or "").strip()
    if not credentials_path:
        raise Exception("Falta DRIVE_CREDENTIALS_JSON en .env")

    scopes = ["https://www.googleapis.com/auth/drive.readonly"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    return google_build("drive", "v3", credentials=creds, cache_discovery=False)


def _drive_list_children(service, folder_id: str) -> list[dict]:
    query = f"'{folder_id}' in parents and trashed = false"
    response = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, mimeType, parents)",
        pageSize=1000,
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    return response.get("files", []) or []


def _drive_get_folder_metadata(service, folder_id: str) -> dict:
    return service.files().get(
        fileId=folder_id,
        fields="id, name, mimeType, parents, trashed",
        supportsAllDrives=True,
    ).execute()


def _drive_find_child_folder(service, parent_id: str, folder_name: str) -> dict | None:
    target = _normalizar_columna(folder_name)
    for item in _drive_list_children(service, parent_id):
        if item.get("mimeType") == "application/vnd.google-apps.folder" and _normalizar_columna(item.get("name", "")) == target:
            return item
    return None


def _drive_find_dni_folder(service, root_folder_id: str, dni: str) -> dict | None:
    root_children = _drive_list_children(service, root_folder_id)
    # Primero intenta encontrar el DNI directamente bajo la raíz.
    target = _normalizar_columna(dni)
    for item in root_children:
        if item.get("mimeType") == "application/vnd.google-apps.folder" and _normalizar_columna(item.get("name", "")) == target:
            return item

    # Si no existe directo, intenta año/mes dinámico por la fecha actual.
    fecha_hoy = datetime.now()
    year_name = str(fecha_hoy.year)
    month_name = f"{fecha_hoy.month:02d}"
    year_folder = _drive_find_child_folder(service, root_folder_id, year_name)
    if year_folder:
        month_folder = _drive_find_child_folder(service, year_folder["id"], month_name)
        if month_folder:
            for item in _drive_list_children(service, month_folder["id"]):
                if item.get("mimeType") == "application/vnd.google-apps.folder" and _normalizar_columna(item.get("name", "")) == target:
                    return item

    # Fallback robusto: busca el DNI en subcarpetas de hasta 2 niveles
    # para soportar historicos en anyo/mes distinto al actual.
    for level1 in root_children:
        if level1.get("mimeType") != "application/vnd.google-apps.folder":
            continue
        for level2 in _drive_list_children(service, level1["id"]):
            if level2.get("mimeType") != "application/vnd.google-apps.folder":
                continue
            if _normalizar_columna(level2.get("name", "")) == target:
                return level2
            for level3 in _drive_list_children(service, level2["id"]):
                if level3.get("mimeType") == "application/vnd.google-apps.folder" and _normalizar_columna(level3.get("name", "")) == target:
                    return level3
    return None


def _drive_list_document_names(service, folder_id: str) -> list[str]:
    files = _drive_list_children(service, folder_id)
    return [f.get("name", "") for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]


def _drive_supported_doc_names(names: list[str]) -> list[str]:
    allowed = {".pdf", ".png", ".jpg", ".jpeg"}
    salida = []
    for name in names:
        ext = Path(str(name or "")).suffix.lower()
        if ext in allowed:
            salida.append(str(name or ""))
    return salida


def _drive_list_documents(service, folder_id: str) -> list[dict]:
    files = _drive_list_children(service, folder_id)
    return [f for f in files if f.get("mimeType") != "application/vnd.google-apps.folder"]


def _drive_supported_doc_files(files: list[dict]) -> list[dict]:
    allowed = {".pdf", ".png", ".jpg", ".jpeg"}
    salida = []
    for item in files:
        name = str(item.get("name", "") or "")
        ext = Path(name).suffix.lower()
        if ext in allowed:
            salida.append(item)
    return salida


def _drive_pick_foto_file(files: list[dict], dni: str) -> dict | None:
    image_exts = {".jpg", ".jpeg", ".png"}
    candidatos = []
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())

    for item in files:
        name = str(item.get("name", "") or "")
        ext = Path(name).suffix.lower()
        if ext not in image_exts:
            continue

        name_norm = _normalizar_columna(name)
        puntaje = 80
        if "foto_carne" in name_norm or ("foto" in name_norm and "carne" in name_norm):
            puntaje = 10
        elif "foto" in name_norm:
            puntaje = 20
        elif "imagen" in name_norm:
            puntaje = 30
        elif "selfie" in name_norm:
            puntaje = 40
        elif "firma" in name_norm:
            puntaje = 90

        if ext in {".jpg", ".jpeg"}:
            puntaje -= 2

        if dni_digits and dni_digits in "".join(ch for ch in name if ch.isdigit()):
            puntaje -= 1

        candidatos.append((puntaje, len(name), name_norm, item))

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: (x[0], x[1], x[2]))
    return candidatos[0][3]


def _drive_pick_djfut_file(files: list[dict], dni: str) -> dict | None:
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    candidatos = []

    for item in files:
        name = str(item.get("name", "") or "")
        ext = Path(name).suffix.lower()
        if ext != ".pdf":
            continue

        name_norm = _normalizar_columna(name)
        puntaje = 80
        if "djfut" in name_norm:
            puntaje = 5
        elif "dj" in name_norm:
            puntaje = 15
        elif "fut" in name_norm:
            puntaje = 20

        if dni_digits and dni_digits in "".join(ch for ch in name if ch.isdigit()):
            puntaje -= 1

        candidatos.append((puntaje, len(name), name_norm, item))

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: (x[0], x[1], x[2]))
    return candidatos[0][3]


def _drive_pick_certificado_medico_file(files: list[dict], dni: str) -> dict | None:
    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    candidatos = []

    for item in files:
        name = str(item.get("name", "") or "")
        ext = Path(name).suffix.lower()
        if ext != ".pdf":
            continue

        name_norm = _normalizar_columna(name)
        puntaje = 80
        if "certificado" in name_norm and "medico" in name_norm:
            puntaje = 5
        elif "certmed" in name_norm:
            puntaje = 8
        elif "cert" in name_norm and "med" in name_norm:
            puntaje = 15

        if dni_digits and dni_digits in "".join(ch for ch in name if ch.isdigit()):
            puntaje -= 1

        candidatos.append((puntaje, len(name), name_norm, item))

    if not candidatos:
        return None

    candidatos.sort(key=lambda x: (x[0], x[1], x[2]))
    return candidatos[0][3]


def _drive_download_file_to_local(service, file_id: str, destino: Path) -> Path:
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    contenido = request.execute()
    if not isinstance(contenido, (bytes, bytearray)):
        raise Exception("La descarga de Drive no devolvió contenido binario")

    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(bytes(contenido))
    if not destino.exists() or destino.stat().st_size <= 0:
        raise Exception(f"El archivo descargado quedó vacío: {destino}")
    return destino


def preparar_foto_local_desde_drive(logger: logging.Logger, dni: str) -> tuple[bool, Path | None, str]:
    """Descarga la foto del expediente DNI a un path local para cargarla en input file."""
    root_folder_id = _drive_root_folder_id()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    dni_folder = _drive_find_dni_folder(service, root_folder_id, dni)
    if not dni_folder:
        return False, None, "No se encontró carpeta DNI para extraer foto"

    docs = _drive_supported_doc_files(_drive_list_documents(service, dni_folder["id"]))
    if not docs:
        return False, None, "No hay documentos soportados en la carpeta DNI"

    foto = _drive_pick_foto_file(docs, dni)
    if not foto:
        return False, None, "No se encontró imagen de foto (.jpg/.jpeg/.png) en el expediente"

    nombre_foto = str(foto.get("name", "") or "").strip()
    foto_id = str(foto.get("id", "") or "").strip()
    if not foto_id:
        return False, None, "El archivo de foto no tiene id en Drive"

    ext = Path(nombre_foto).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png"}:
        ext = ".jpg"

    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    subdir = DATA_DIR / "cache" / "upload_tmp" / (dni_digits or "sin_dni")
    destino = subdir / f"foto_carne_{dni_digits or 'prospecto'}{ext}"

    _drive_download_file_to_local(service, foto_id, destino)
    logger.info(
        "[DRIVE][%s] Foto descargada para upload | archivo_drive=%s | local=%s | size_bytes=%s",
        dni,
        nombre_foto,
        destino,
        destino.stat().st_size,
    )
    return True, destino, nombre_foto


def preparar_djfut_local_desde_drive(logger: logging.Logger, dni: str) -> tuple[bool, Path | None, str]:
    """Descarga el DJFUT PDF del expediente DNI a un path local para cargarlo en input file."""
    root_folder_id = _drive_root_folder_id()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    dni_folder = _drive_find_dni_folder(service, root_folder_id, dni)
    if not dni_folder:
        return False, None, "No se encontró carpeta DNI para extraer DJFUT"

    docs = _drive_supported_doc_files(_drive_list_documents(service, dni_folder["id"]))
    if not docs:
        return False, None, "No hay documentos soportados en la carpeta DNI"

    djfut = _drive_pick_djfut_file(docs, dni)
    if not djfut:
        return False, None, "No se encontró PDF DJFUT en el expediente"

    nombre_djfut = str(djfut.get("name", "") or "").strip()
    djfut_id = str(djfut.get("id", "") or "").strip()
    if not djfut_id:
        return False, None, "El archivo DJFUT no tiene id en Drive"

    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    subdir = DATA_DIR / "cache" / "upload_tmp" / (dni_digits or "sin_dni")
    destino = subdir / f"djfut_{dni_digits or 'prospecto'}.pdf"

    _drive_download_file_to_local(service, djfut_id, destino)
    logger.info(
        "[DRIVE][%s] DJFUT descargado para upload | archivo_drive=%s | local=%s | size_bytes=%s",
        dni,
        nombre_djfut,
        destino,
        destino.stat().st_size,
    )
    return True, destino, nombre_djfut


def preparar_certificado_medico_local_desde_drive(logger: logging.Logger, dni: str) -> tuple[bool, Path | None, str]:
    """Descarga el certificado médico PDF del expediente DNI a un path local para cargarlo en input file."""
    root_folder_id = _drive_root_folder_id()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    dni_folder = _drive_find_dni_folder(service, root_folder_id, dni)
    if not dni_folder:
        return False, None, "No se encontró carpeta DNI para extraer certificado médico"

    docs = _drive_supported_doc_files(_drive_list_documents(service, dni_folder["id"]))
    if not docs:
        return False, None, "No hay documentos soportados en la carpeta DNI"

    certificado = _drive_pick_certificado_medico_file(docs, dni)
    if not certificado:
        return False, None, "No se encontró PDF de certificado médico en el expediente"

    nombre_cert = str(certificado.get("name", "") or "").strip()
    cert_id = str(certificado.get("id", "") or "").strip()
    if not cert_id:
        return False, None, "El archivo de certificado médico no tiene id en Drive"

    dni_digits = "".join(ch for ch in str(dni or "") if ch.isdigit())
    subdir = DATA_DIR / "cache" / "upload_tmp" / (dni_digits or "sin_dni")
    destino = subdir / f"certificado_medico_{dni_digits or 'prospecto'}.pdf"

    _drive_download_file_to_local(service, cert_id, destino)
    logger.info(
        "[DRIVE][%s] Certificado médico descargado para upload | archivo_drive=%s | local=%s | size_bytes=%s",
        dni,
        nombre_cert,
        destino,
        destino.stat().st_size,
    )
    return True, destino, nombre_cert


def validar_documentos_drive_por_dni(logger: logging.Logger, dni: str) -> tuple[bool, list[str]]:
    """Valida carpeta DNI en Drive y confirma documentos soportados (pdf/png/jpg/jpeg)."""
    root_folder_id = _drive_root_folder_id()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    logger.info("[DRIVE][%s] Validando documentos del expediente en Drive", dni)
    dni_folder = _drive_find_dni_folder(service, root_folder_id, dni)
    if not dni_folder:
        logger.warning("[DRIVE][%s] No se encontró carpeta DNI (buscado en raiz y subestructura)", dni)
        return False, []

    names = _drive_list_document_names(service, dni_folder["id"])
    docs = _drive_supported_doc_names(names)
    if not docs:
        logger.warning("[DRIVE][%s] Carpeta accesible pero sin documentos soportados (pdf/png/jpg/jpeg)", dni)
        return False, []

    logger.info(
        "[DRIVE][%s] Acceso OK a expediente | carpeta=%s | documentos_soportados=%s | archivos=%s",
        dni,
        dni_folder.get("name", ""),
        len(docs),
        ", ".join(docs),
    )
    return True, docs


def validar_drive_acceso_raiz(logger: logging.Logger, folder_id: str | None = None, max_items: int = 10) -> bool:
    """Valida que la service account pueda leer la carpeta raíz compartida y sus hijos visibles."""
    root_folder_id = str(folder_id or _drive_root_folder_id()).strip()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    metadata = _drive_get_folder_metadata(service, root_folder_id)
    logger.info(
        "[DRIVE] Carpeta raíz accesible: nombre=%s | id=%s | mimeType=%s",
        metadata.get("name", ""),
        metadata.get("id", ""),
        metadata.get("mimeType", ""),
    )

    children = _drive_list_children(service, root_folder_id)
    logger.info("[DRIVE] Elementos visibles en la carpeta raíz: %s", len(children))
    limite = max(1, int(max_items or 10))
    for idx, item in enumerate(children[:limite], start=1):
        logger.info(
            "[DRIVE] Hijo %s | nombre=%s | mimeType=%s | id=%s",
            idx,
            item.get("name", ""),
            item.get("mimeType", ""),
            item.get("id", ""),
        )

    if not children:
        logger.warning("[DRIVE] La carpeta raíz es accesible, pero no devolvió hijos visibles")
        return False

    return True


def validar_drive_por_dni(logger: logging.Logger, dni: str, required_names: list[str] | None = None) -> bool:
    """Valida que exista la carpeta del DNI y que exponga documentos visibles dentro."""
    root_folder_id = _drive_root_folder_id()
    if not root_folder_id:
        raise Exception("Falta DRIVE_ROOT_FOLDER_ID o CARNET_DRIVE_ROOT_FOLDER_ID en .env")

    service = _drive_service()
    logger.info("[DRIVE][%s] Validando acceso en carpeta raíz: %s", dni, root_folder_id)
    dni_folder = _drive_find_dni_folder(service, root_folder_id, dni)
    if not dni_folder:
        logger.warning("[DRIVE][%s] No se encontró carpeta DNI", dni)
        return False

    logger.info(
        "[DRIVE][%s] Carpeta DNI encontrada: %s (%s)",
        dni,
        dni_folder.get("name", ""),
        dni_folder.get("id", ""),
    )
    names = _drive_list_document_names(service, dni_folder["id"])
    logger.info("[DRIVE][%s] Documentos visibles en carpeta: %s", dni, ", ".join(names) if names else "<vacío>")

    if not required_names:
        return bool(names)

    names_norm = [_normalizar_columna(x) for x in names]
    faltantes = []
    for required in required_names:
        if not any(_normalizar_columna(required) in n for n in names_norm):
            faltantes.append(required)

    if faltantes:
        logger.warning("[DRIVE][%s] Faltan documentos: %s", dni, ", ".join(faltantes))
        return False

    logger.info("[DRIVE][%s] Validación OK: carpeta accesible y documentos requeridos presentes", dni)
    return True


def prevalidar_drive_desde_hoja(logger: logging.Logger, max_rows: int = 5) -> None:
    """Prevalida Drive por los primeros DNIs detectados en la hoja base, sin interrumpir el flujo."""
    if _as_bool_env("DRIVE_VALIDATE_ON_START", default=DEFAULT_DRIVE_VALIDATE_ON_START == "1") is False:
        return

    try:
        validar_drive_acceso_raiz(logger, max_items=max_rows)
        url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
        rows, fields = _leer_google_sheet_rows(url_base, logger)
        col_dni = _resolver_columna(fields, ["dni"])
        if not col_dni:
            logger.warning("[DRIVE] No se encontró columna DNI en la hoja base")
            return

        seen = 0
        for row in rows:
            dni = str(row.get(col_dni, "") or "").strip()
            if not dni:
                continue
            seen += 1
            try:
                validar_drive_por_dni(logger, dni)
            except Exception as exc:
                logger.warning("[DRIVE][%s] Falló validación: %s", dni, exc)
            if seen >= max(1, int(max_rows or 5)):
                break
    except Exception as exc:
        logger.warning("[DRIVE] No se pudo ejecutar prevalidación de Drive: %s", exc)


def comparar_dnis_entre_hojas(logger: logging.Logger, max_rows: int = 5) -> None:
    """Cruza dos hojas de Google Sheets por DNI y muestra coincidencias / diferencias."""
    url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
    url_compare = str(os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()

    if not url_compare:
        raise Exception("Falta CARNET_GSHEET_COMPARE_URL para poder hacer el cruce por DNI")

    rows_base, fields_base = _leer_google_sheet_rows(url_base, logger)
    rows_compare, fields_compare = _leer_google_sheet_rows(url_compare, logger)

    col_base_dni = _resolver_columna(fields_base, ["dni"])
    col_cmp_dni = _resolver_columna(fields_compare, ["dni"])
    col_cmp_compania = _resolver_columna(fields_compare, ["compania", "compañia", "empresa"])
    col_base_estado = _resolver_columna(fields_base, ["estado"])
    col_cmp_estado = _resolver_columna(fields_compare, ["estado_tramite"])
    col_base_obs = _resolver_columna(fields_base, ["observacion", "observaciones", "observ", "obs"])
    col_cmp_obs = _resolver_columna(fields_compare, ["observacion", "observaciones", "observ", "obs"])
    col_cmp_responsable = _resolver_columna(fields_compare, ["responsable"])
    col_cmp_fecha = _resolver_columna(fields_compare, ["fecha tramite", "fecha tram ite", "fechatramite", "fecha_tramite", "fecha trámite"])

    faltantes = []
    if not col_base_dni:
        faltantes.append("DNI hoja base")
    if not col_cmp_dni:
        faltantes.append("DNI hoja comparación")
    if not col_cmp_compania:
        faltantes.append("COMPANIA hoja comparación")
    if faltantes:
        raise Exception(f"No se pudo detectar la columna DNI: {faltantes}")

    base_por_dni = {}
    for row in rows_base:
        dni = str(row.get(col_base_dni, "") or "").strip()
        if dni and dni not in base_por_dni:
            base_por_dni[dni] = row

    compare_por_dni = {}
    for row in rows_compare:
        dni = str(row.get(col_cmp_dni, "") or "").strip()
        if dni and dni not in compare_por_dni:
            compare_por_dni[dni] = row

    comun = sorted(set(base_por_dni).intersection(compare_por_dni))
    solo_base = sorted(set(base_por_dni).difference(compare_por_dni))
    solo_compare = sorted(set(compare_por_dni).difference(base_por_dni))

    logger.info("Cruce por DNI activado")
    logger.info("Base: %s filas | Comparación: %s filas", len(rows_base), len(rows_compare))
    logger.info("Coinciden en ambas hojas: %s", len(comun))
    logger.info("Solo en base: %s | Solo en comparación: %s", len(solo_base), len(solo_compare))

    limite = max(1, int(max_rows or 5))
    for idx, dni in enumerate(comun[:limite], start=1):
        base_row = base_por_dni[dni]
        cmp_row = compare_por_dni[dni]
        base_estado = str(base_row.get(col_base_estado, "") or "").strip() if col_base_estado else ""
        cmp_compania = str(cmp_row.get(col_cmp_compania, "") or "").strip() if col_cmp_compania else ""
        cmp_responsable = str(cmp_row.get(col_cmp_responsable, "") or "").strip() if col_cmp_responsable else ""
        cmp_estado = str(cmp_row.get(col_cmp_estado, "") or "").strip() if col_cmp_estado else ""
        base_obs = str(base_row.get(col_base_obs, "") or "").strip() if col_base_obs else ""
        cmp_obs = str(cmp_row.get(col_cmp_obs, "") or "").strip() if col_cmp_obs else ""
        cmp_fecha = str(cmp_row.get(col_cmp_fecha, "") or "").strip() if col_cmp_fecha else ""
        logger.info(
            "Cruce %s | DNI=%s | BASE[ESTADO=%s | OBS=%s] | COMP[COMPANIA=%s | RESPONSABLE=%s | ESTADO=%s | OBS=%s | FECHA_TRAMITE=%s]",
            idx,
            dni,
            base_estado,
            base_obs,
            cmp_compania,
            cmp_responsable,
            cmp_estado,
            cmp_obs,
            cmp_fecha,
        )

    if not comun:
        logger.warning("No se encontraron DNIs en común entre ambas hojas")


def esperar_ajax_primefaces(page, timeout_ms: int = 7000) -> None:
    """Espera a que la cola AJAX de PrimeFaces quede vacía (si existe)."""
    try:
        page.wait_for_function(
            """() => {
                try {
                    if (!window.PrimeFaces || !PrimeFaces.ajax || !PrimeFaces.ajax.Queue) return true;
                    const q = PrimeFaces.ajax.Queue;
                    if (typeof q.isEmpty === 'function') return q.isEmpty();
                    const arr = q.requests || q.queue || [];
                    return !arr || arr.length === 0;
                } catch (e) {
                    return true;
                }
            }""",
            timeout=max(1000, int(timeout_ms)),
        )
    except Exception:
        pass


def validar_vista_crear_solicitud_por_ui(page, timeout_ms: int = 6000) -> bool:
    """
    Confirma vista de CREAR SOLICITUD por UI (sin depender de URL).
    Soporta selector personalizado opcional vía CARNET_CREAR_SOLICITUD_SELECTOR.
    """
    custom_selector = str(os.getenv("CARNET_CREAR_SOLICITUD_SELECTOR", "") or "").strip()
    deadline = time.time() + (max(600, int(timeout_ms)) / 1000.0)

    while time.time() < deadline:
        if custom_selector:
            try:
                if page.locator(custom_selector).first.is_visible(timeout=150):
                    return True
            except Exception:
                pass

        # Señales fuertes por presencia de campos del formulario createForm.
        try:
            ok_fields = page.evaluate(
                """() => {
                    const sels = [
                        'form#createForm',
                        '#createForm\\:dondeRecoger_label',
                        '#createForm\\:modalidad_label',
                        '#createForm\\:tipoRegistro_label',
                        '#createForm\\:tipoDoc_label',
                        '#createForm\\:nroSecuencia',
                        '#createForm\\:numDoc',
                        '#createForm\\:btnBuscarVigilante',
                    ];
                    return sels.some((sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return false;
                        if (el.tagName === 'FORM') return true;
                        return !!(el.offsetParent || (el.getClientRects && el.getClientRects().length));
                    });
                }"""
            )
            if bool(ok_fields):
                return True
        except Exception:
            pass

        try:
            ok = page.evaluate(
                """() => {
                    const candidates = [
                        '.ui-layout-center',
                        '#j_idt11\\:content',
                        '#contenido',
                        '#principal',
                        '#main',
                    ];
                    let root = null;
                    for (const sel of candidates) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetParent !== null) {
                            root = el;
                            break;
                        }
                    }
                    if (!root) return false;
                    const txt = String(root.innerText || '').replace(/\\s+/g, ' ').toUpperCase();
                    return txt.includes('CREAR SOLICITUD') || txt.includes('NRO. DE SECUENCIA') || txt.includes('TIPO DE DOCUMENTO');
                }"""
            )
            if bool(ok):
                return True
        except Exception:
            pass

        page.wait_for_timeout(180)

    return False


def navegar_dssp_carne_crear_solicitud(page, logger: logging.Logger) -> None:
    """Navega en el panel lateral por DSSP -> CARNÉ -> CREAR SOLICITUD."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass

    validation_timeout_ms = max(2000, _safe_int_env("CARNET_CREAR_SOLICITUD_VALIDATION_TIMEOUT_MS", 6500))

    # Fast-path: click directo al link JSF de CREAR SOLICITUD por onclick.
    try:
        page.locator(SEL["menu_root"]).first.wait_for(state="visible", timeout=5000)
        click_directo = page.evaluate(
            """() => {
                const anchors = Array.from(document.querySelectorAll('a[onclick*="addSubmitParam"][onclick*="menuprincipal"], a[onclick*="addSubmitParam"][onclick*="menuPrincipal"]'));
                const target = anchors.find((a) => ((a.textContent || '').replace(/\\s+/g, ' ').trim().toUpperCase() === 'CREAR SOLICITUD'));
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        if click_directo:
            logger.info("Fast-path: click directo en CREAR SOLICITUD (onclick JSF)")
            try:
                page.wait_for_load_state("domcontentloaded", timeout=4500)
            except Exception:
                pass
            esperar_ajax_primefaces(page, timeout_ms=3500)
            if validar_vista_crear_solicitud_por_ui(page, timeout_ms=2200):
                logger.info("Click JSF en CREAR SOLICITUD ejecutado (vista confirmada por UI)")
                return
            logger.warning("Fast-path no confirmó vista CREAR SOLICITUD; se intentará navegación jerárquica")
    except Exception:
        pass

    root = page.locator(SEL["menu_root"]).first
    root.wait_for(state="visible", timeout=12000)

    header_dssp = root.locator(SEL["menu_header_dssp"]).first
    header_dssp.wait_for(state="visible", timeout=8000)

    aria_expanded = (header_dssp.get_attribute("aria-expanded") or "").strip().lower()
    if aria_expanded != "true":
        header_dssp.click(timeout=8000)
        page.wait_for_timeout(250)
        aria_expanded = (header_dssp.get_attribute("aria-expanded") or "").strip().lower()
        if aria_expanded != "true":
            raise Exception("No se pudo expandir el menú DSSP")
    logger.info("Menú DSSP expandido")

    item_carne = root.locator(SEL["menu_item_carne"]).first
    item_carne.wait_for(state="visible", timeout=8000)
    item_carne.click(timeout=8000)
    logger.info("Click en opción CARNÉ")

    item_crear = root.locator(SEL["menu_item_crear_solicitud_onclick"]).first
    try:
        item_crear.wait_for(state="visible", timeout=4500)
    except Exception:
        # Si CARNÉ colapsa/expande en dos fases, repetimos el click una vez.
        item_carne.click(timeout=8000)
        try:
            item_crear.wait_for(state="visible", timeout=3500)
        except Exception:
            item_crear = root.locator(SEL["menu_item_crear_solicitud"]).first
            item_crear.wait_for(state="visible", timeout=6000)

    item_crear.click(timeout=10000)

    # Fallback fuerte: click por JS en caso de overlays/transiciones de PrimeFaces.
    try:
        page.evaluate(
            """() => {
                const anchors = Array.from(document.querySelectorAll('a[onclick*="addSubmitParam"][onclick*="menuprincipal"], a[onclick*="addSubmitParam"][onclick*="menuPrincipal"]'));
                const target = anchors.find((a) => ((a.textContent || '').replace(/\\s+/g, ' ').trim().toUpperCase() === 'CREAR SOLICITUD'));
                if (target) target.click();
            }"""
        )
    except Exception:
        pass

    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    esperar_ajax_primefaces(page, timeout_ms=6000)
    if validar_vista_crear_solicitud_por_ui(page, timeout_ms=validation_timeout_ms):
        logger.info("Click en CREAR SOLICITUD ejecutado (vista confirmada por UI)")
    else:
        logger.warning("Click en CREAR SOLICITUD ejecutado, pero no se confirmó vista por UI")


def preparar_flujo_emision_carnet(logger: logging.Logger, page, grupo: str, registro_formulario: dict | None = None) -> None:
    logger.info("Login correcto para grupo %s. Continúa el flujo de emisión de carnet.", grupo)
    navegar_dssp_carne_crear_solicitud(page, logger)
    if registro_formulario is not None:
        procesar_registro_cruce_en_formulario(page, logger, registro_formulario)
    elif _as_bool_env("CARNET_FORM_PRUEBA", default=True):
        ejecutar_prueba_cruce_y_sede_en_formulario(
            page,
            logger,
            max_rows=max(1, _safe_int_env("CARNET_FORM_PRUEBA_ROWS", 1)),
        )
    logger.info("URL post-login: %s", page.url)


def obtener_grupo_ruc(valor: str) -> str:
    base = _normalizar_columna(valor).upper()
    if "SELVA" in base or "20493762789" in base:
        return "SELVA"
    if "J&V" in base or "J V" in base or "RESGUARDO" in base or "20100901481" in base:
        return "JV"
    return "OTRO"


def resolver_grupos_objetivo() -> list:
    grupos_env = str(os.getenv("CARNET_GRUPOS", "SELVA,JV") or "SELVA,JV")
    grupos = [x.strip().upper() for x in grupos_env.split(",") if x.strip()]
    salida = []
    for g in grupos:
        if g in {"SELVA", "JV"} and g not in salida:
            salida.append(g)
    return salida or ["JV"]


def credenciales_por_grupo(grupo: str) -> dict:
    if grupo == "SELVA":
        return CREDENCIALES_SELVA
    return CREDENCIALES_JV


def _cerrar_paginas_extra_context(context, page_principal, logger: logging.Logger) -> None:
    """Cierra páginas adicionales para mantener 1 pestaña activa por worker."""
    try:
        pages = list(context.pages)
    except Exception:
        return

    for p in pages:
        if p == page_principal:
            continue
        try:
            p.close()
            logger.info("[WORKER] Pestaña extra cerrada para evitar superposición de flujo")
        except Exception:
            pass


def _abrir_sesion_grupo(playwright, logger: logging.Logger, grupo: str):
    headless = _as_bool_env("CARNET_HEADLESS", default=False)
    launch_args = _build_launch_args_for_window()
    logger.info("[%s] Args Chromium: %s", grupo, " ".join(launch_args))

    browser = playwright.chromium.launch(
        headless=headless,
        slow_mo=0,
        args=launch_args,
    )
    context = browser.new_context(no_viewport=True, ignore_https_errors=True)
    page = context.new_page()
    return browser, context, page


def _ejecutar_login_en_pagina(page, logger: logging.Logger, grupo: str) -> None:
    credenciales = credenciales_por_grupo(grupo)
    validar_credenciales_configuradas(credenciales, grupo)
    login_validation_timeout_ms = max(1000, _safe_int_env("LOGIN_VALIDATION_TIMEOUT_MS", 12000))

    logger.info("[%s] Navegando a login", grupo)
    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=45000)
    esperar_hasta_servicio_disponible(page, URL_LOGIN, espera_segundos=8)

    activar_pestana_autenticacion_tradicional(page)
    page.locator(SEL["numero_documento"]).wait_for(state="visible", timeout=9000)

    page.select_option(SEL["tipo_doc_select"], value=credenciales["tipo_documento_valor"])
    page.wait_for_timeout(300)

    escribir_input_rapido(page, SEL["numero_documento"], credenciales["numero_documento"])
    escribir_input_rapido(page, SEL["usuario"], credenciales["usuario"])
    escribir_input_rapido(page, SEL["clave"], credenciales["contrasena"])
    logger.info("[%s] Credenciales cargadas", grupo)

    captcha_text = solve_captcha_ocr(page, logger)
    escribir_input_rapido(page, SEL["captcha_input"], captcha_text)
    logger.info("[%s] Captcha escrito automáticamente", grupo)

    page.locator(SEL["ingresar"]).click(timeout=10000)
    ok, msg_error, tiempo = validar_resultado_login_por_ui(page, timeout_ms=login_validation_timeout_ms)
    if not ok:
        raise Exception(f"[{grupo}] Login fallido: {msg_error}")

    logger.info("[%s] Login exitoso en %.2fs", grupo, tiempo)


def ejecutar_login_grupo(
    playwright,
    logger: logging.Logger,
    grupo: str,
    registro_formulario: dict | None = None,
    keep_browser_open_on_finish: bool = False,
):
    hold_browser_open = _as_bool_env("HOLD_BROWSER_OPEN", default=False)
    headless = _as_bool_env("CARNET_HEADLESS", default=False)
    keep_open_now = bool(
        (keep_browser_open_on_finish or hold_browser_open)
        and (not _is_scheduled_mode())
        and (not headless)
    )

    browser = None
    context = None
    page = None
    try:
        browser, context, page = _abrir_sesion_grupo(playwright, logger, grupo)
        _ejecutar_login_en_pagina(page, logger, grupo)
        _cerrar_paginas_extra_context(context, page, logger)
        preparar_flujo_emision_carnet(logger, page, grupo, registro_formulario=registro_formulario)

        if keep_open_now:
            logger.info("[%s] HOLD_BROWSER_OPEN=1. Esperando Ctrl+C", grupo)
            try:
                while True:
                    time.sleep(60)
            except KeyboardInterrupt:
                logger.info("[%s] Interrupción manual detectada", grupo)
    finally:
        try:
            if context is not None and not keep_open_now:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None and not keep_open_now:
                browser.close()
        except Exception:
            pass


def _resolver_grupo_para_item(item: dict) -> str:
    base_row = item.get("base_row") or {}
    compania_compare = str(item.get("compania", "") or "").strip()
    if compania_compare:
        grupo_cmp = obtener_grupo_ruc(compania_compare)
        if grupo_cmp in {"JV", "SELVA"}:
            return grupo_cmp

    grupo = obtener_grupo_ruc(
        str(
            base_row.get("ruc", "")
            or compania_compare
            or item.get("departamento", "")
            or ""
        )
    )
    if grupo == "OTRO":
        grupo = obtener_grupo_ruc(str(base_row.get("compania", "") or item.get("compania", "") or "JV"))
    if grupo == "OTRO":
        grupo = "JV"
    return grupo


def _ejecutar_flujo_fila_por_fila(logger: logging.Logger, max_rows: int = 1) -> int:
    """Procesa registros de comparación, con reserva dinámica cuando corre como worker."""
    is_multiworker_child = _as_bool_env("MULTIWORKER_CHILD", default=False)
    max_login_retries_per_group = max(1, _safe_int_env("MAX_LOGIN_RETRIES_PER_GROUP", 12))
    max_rows_int = int(max_rows or 0)
    worker_items_file = str(os.getenv("CARNET_WORKER_ITEMS_FILE", "") or "").strip()
    worker_usa_items_preasignados = bool(is_multiworker_child and worker_items_file)

    pendientes = []
    if not is_multiworker_child:
        pendientes = _cargar_cruce_pendiente_desde_hojas(
            logger,
            max_rows=max(1, max_rows_int or 1),
            preasignar_secuencias=False,
            permitir_en_proceso_expirado=False,
        )
        if not pendientes:
            logger.warning("[CRUCE] No hay registros pendientes para procesar uno por uno")
            return 0
    elif worker_usa_items_preasignados:
        try:
            with Path(worker_items_file).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, list):
                pendientes = [x for x in data if isinstance(x, dict)]
        except Exception as exc:
            logger.warning("[WORKER] No se pudo cargar items preasignados: %s", exc)
            pendientes = []

        if not pendientes:
            logger.info("[WORKER] Sin items preasignados para este worker")
            return 0

        logger.info("[WORKER] Items preasignados cargados: %s", len(pendientes))

    playwright = sync_playwright().start()
    procesados = 0
    browser = None
    context = None
    page = None
    grupo_sesion_actual = ""

    def _cerrar_sesion_activa() -> None:
        nonlocal browser, context, page, grupo_sesion_actual
        try:
            if context is not None:
                context.close()
        except Exception:
            pass
        try:
            if browser is not None:
                browser.close()
        except Exception:
            pass
        browser = None
        context = None
        page = None
        grupo_sesion_actual = ""

    try:
        cursor_local = 0

        while True:
            if max_rows_int > 0 and procesados >= max_rows_int:
                break

            if is_multiworker_child:
                if worker_usa_items_preasignados:
                    if cursor_local >= len(pendientes):
                        logger.info("[WORKER] Sin más items preasignados para este worker")
                        break
                    item = pendientes[cursor_local]
                    cursor_local += 1

                    # Reserva defensiva de la fila en comparación para evitar colisiones
                    # con corridas concurrentes fuera de este orquestador.
                    if not _intentar_reservar_registro_compare(logger, item):
                        logger.info(
                            "[WORKER] Item preasignado no reservable (fila=%s DNI=%s); se omite",
                            int(item.get("compare_row_number", 0) or item.get("row_number", 0) or 0),
                            str(item.get("dni", "") or "").strip(),
                        )
                        continue
                else:
                    item = _reservar_siguiente_item_para_worker(logger)
                    if not item:
                        logger.info("[WORKER] Sin más registros reservables para este worker")
                        break
            else:
                if cursor_local >= len(pendientes):
                    break
                item = pendientes[cursor_local]
                cursor_local += 1

            procesados += 1
            base_row = item.get("base_row")
            compare_url = str(item.get("compare_url", "") or "").strip()
            compare_row_number = int(item.get("compare_row_number", 0) or item.get("row_number", 0) or 0)
            compare_fieldnames = item.get("fieldnames_compare", []) or []
            fecha_hoy = datetime.now().strftime("%d/%m/%Y")

            if not base_row:
                _registrar_error_tramite_en_comparacion(
                    logger,
                    compare_url,
                    compare_row_number,
                    compare_fieldnames,
                    f"DNI {item.get('dni', '')} no encontrado en hoja base",
                    fecha_hoy,
                )
                _liberar_reserva_secuencia_si_aplica(logger, item)
                continue

            grupo = _resolver_grupo_para_item(item)
            logger.info(
                "[FILA %s] DNI=%s | COMPANIA=%s | GRUPO=%s | DEPARTAMENTO=%s | PUESTO=%s | SEDE=%s | MODALIDAD_OBJETIVO=%s | TIPO_DOC_OBJETIVO=%s | ORIGEN=%s | CRUCE_BASE_FILA=%s",
                procesados,
                item.get("dni", ""),
                item.get("compania", ""),
                grupo,
                item.get("departamento", ""),
                item.get("puesto", ""),
                item.get("sede", ""),
                item.get("modalidad_objetivo", ""),
                item.get("tipo_doc_objetivo", ""),
                item.get("origen_sede", ""),
                item.get("base_row_number", 0),
            )

            sesion_operativa = False
            ensure_tries = 3
            for ensure_try in range(ensure_tries):
                requiere_login = (page is None) or (grupo_sesion_actual != grupo)

                if requiere_login:
                    login_ok = False
                    intento = 0
                    while intento < max_login_retries_per_group:
                        intento += 1
                        logger.info(
                            "[FILA %s][%s] Intento login %s/%s",
                            procesados,
                            grupo,
                            intento,
                            max_login_retries_per_group,
                        )
                        try:
                            _cerrar_sesion_activa()
                            browser, context, page = _abrir_sesion_grupo(playwright, logger, grupo)
                            _ejecutar_login_en_pagina(page, logger, grupo)
                            _cerrar_paginas_extra_context(context, page, logger)
                            grupo_sesion_actual = grupo
                            login_ok = True
                            break
                        except PlaywrightTimeoutError as exc:
                            logger.warning(
                                "[FILA %s][%s] Timeout en intento login %s: %s",
                                procesados,
                                grupo,
                                intento,
                                exc,
                            )
                            _cerrar_sesion_activa()
                        except Exception as exc:
                            logger.warning(
                                "[FILA %s][%s] Error en intento login %s: %s",
                                procesados,
                                grupo,
                                intento,
                                exc,
                            )
                            _cerrar_sesion_activa()

                        if intento < max_login_retries_per_group:
                            espera_backoff = min(8, 1 + intento)
                            logger.info(
                                "[FILA %s][%s] Reintentando login en %ss...",
                                procesados,
                                grupo,
                                espera_backoff,
                            )
                            time.sleep(espera_backoff)

                    if not login_ok:
                        break

                try:
                    navegar_dssp_carne_crear_solicitud(page, logger)
                    if not validar_vista_crear_solicitud_por_ui(
                        page,
                        timeout_ms=max(2500, _safe_int_env("CARNET_CREAR_SOLICITUD_VALIDATION_TIMEOUT_MS", 6500)),
                    ):
                        raise Exception("No se confirmó vista CREAR SOLICITUD")
                    _cerrar_paginas_extra_context(context, page, logger)
                    sesion_operativa = True
                    break
                except Exception as exc:
                    logger.warning(
                        "[FILA %s][%s] No se pudo dejar sesión en CREAR SOLICITUD (try %s/%s): %s",
                        procesados,
                        grupo,
                        ensure_try + 1,
                        ensure_tries,
                        exc,
                    )
                    page_closed = False
                    try:
                        page_closed = (page is None) or page.is_closed()
                    except Exception:
                        page_closed = True

                    if page_closed:
                        _cerrar_sesion_activa()
                        continue

                    # Reintento suave: misma sesión, sin relogin inmediato.
                    if ensure_try + 1 < ensure_tries:
                        try:
                            _cerrar_paginas_extra_context(context, page, logger)
                            page.wait_for_timeout(350)
                        except Exception:
                            pass
                        continue

                    _cerrar_sesion_activa()

            if not sesion_operativa:
                _registrar_error_tramite_en_comparacion(
                    logger,
                    compare_url,
                    compare_row_number,
                    compare_fieldnames,
                    f"No se pudo confirmar vista CREAR SOLICITUD para grupo {grupo}",
                    fecha_hoy,
                )
                _liberar_reserva_secuencia_si_aplica(logger, item)
                continue

            try:
                procesar_registro_cruce_en_formulario(page, logger, item)
            except Exception as exc:
                logger.warning(
                    "[FILA %s][%s] Excepción no controlada en procesamiento de registro: %s",
                    procesados,
                    grupo,
                    exc,
                )

            _liberar_reserva_secuencia_si_aplica(logger, item)

        return 0
    finally:
        _cerrar_sesion_activa()
        try:
            playwright.stop()
        except Exception:
            pass


def ejecutar_flujo_secundario() -> int:
    worker_id = str(os.getenv("WORKER_ID", "main") or "main")
    child_suffix = f"worker_{worker_id}" if _as_bool_env("MULTIWORKER_CHILD", default=False) else "main"
    logger = setup_logger("carnet_emision", suffix=child_suffix)
    logger.info("INICIANDO FLUJO CARNET - Login automático")

    worker_items_file = str(os.getenv("CARNET_WORKER_ITEMS_FILE", "") or "").strip()
    skip_precheck_worker = _as_bool_env("CARNET_WORKER_SKIP_PRECHECK", default=True)
    es_worker_con_items = _as_bool_env("MULTIWORKER_CHILD", default=False) and bool(worker_items_file)

    if es_worker_con_items and skip_precheck_worker:
        logger.info("[WORKER] Precheck de hojas omitido: usando items preasignados por orquestador")
    else:
        try:
            url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
            confirmar_acceso_google_sheet(logger, url_base, "HOJA_BASE")

            url_compare = str(os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()
            if url_compare:
                confirmar_acceso_google_sheet(logger, url_compare, "HOJA_COMPARACION")

            url_third = str(os.getenv("CARNET_GSHEET_THIRD_URL", DEFAULT_GSHEET_THIRD_URL) or "").strip()
            if url_third:
                confirmar_acceso_google_sheet(logger, url_third, "HOJA_TERCERA")
        except Exception as exc:
            logger.warning("No se pudo confirmar acceso a Google Sheets: %s", exc)

    if _as_bool_env("CARNET_SHEET_CROSSCHECK_ONLY", default=False):
        try:
            comparar_dnis_entre_hojas(
                logger,
                max_rows=max(1, _safe_int_env("CARNET_SHEET_SAMPLE_ROWS", 5)),
            )
        except Exception as exc:
            logger.warning("No se pudo completar el cruce de hojas: %s", exc)
        logger.info("CARNET_SHEET_CROSSCHECK_ONLY=1 -> finaliza tras el cruce de hojas")
        return 0

    if _as_bool_env("CARNET_SHEET_DEMO_ONLY", default=False):
        logger.info("CARNET_SHEET_DEMO_ONLY=1 -> finaliza tras confirmar acceso a Google Sheets")
        return 0

    if _as_bool_env("DRIVE_VALIDATE_ONLY", default=False):
        validar_drive_acceso_raiz(logger)
        logger.info("DRIVE_VALIDATE_ONLY=1 -> finaliza tras validar la carpeta raíz de Drive")
        return 0

    if _as_bool_env("CARNET_ROW_BY_ROW", default=True):
        row_limit = _safe_int_env("CARNET_FORM_PRUEBA_ROWS", 1)
        if _as_bool_env("MULTIWORKER_CHILD", default=False):
            row_limit = _safe_int_env("CARNET_WORKER_MAX_ROWS", 0)
        return _ejecutar_flujo_fila_por_fila(
            logger,
            max_rows=row_limit,
        )

    grupos = resolver_grupos_objetivo()
    group_override = str(os.getenv("WORKER_GROUP", "") or "").strip().upper()
    if group_override:
        grupos = [group_override]

    max_login_retries_per_group = max(1, _safe_int_env("MAX_LOGIN_RETRIES_PER_GROUP", 12))

    playwright = sync_playwright().start()
    try:
        for grupo in grupos:
            intento = 0
            while intento < max_login_retries_per_group:
                intento += 1
                logger.info("[%s] Intento login %s/%s", grupo, intento, max_login_retries_per_group)
                try:
                    ejecutar_login_grupo(playwright, logger, grupo)
                    break
                except PlaywrightTimeoutError as exc:
                    logger.warning("[%s] Timeout en intento %s: %s", grupo, intento, exc)
                except Exception as exc:
                    logger.warning("[%s] Error en intento %s: %s", grupo, intento, exc)

                if intento >= max_login_retries_per_group:
                    raise Exception(f"[{grupo}] No se pudo completar login tras {max_login_retries_per_group} intentos")
                time.sleep(min(8, 1 + intento))

        logger.info("Flujo finalizado correctamente")
        return 0
    except Exception as exc:
        logger.exception("Fallo general del flujo: %s", exc)
        return 1
    finally:
        try:
            playwright.stop()
        except Exception:
            pass
        logger.info("Navegador cerrado")


def _build_units_for_workers(workers: int) -> list[dict]:
    return [{"worker_id": wid} for wid in range(1, max(1, int(workers or 1)) + 1)]


def _preasignar_secuencia_inicial_por_item(logger: logging.Logger, items: list[dict]) -> int:
    """Reserva una secuencia inicial por item para reducir latencia por registro."""
    if not items:
        return 0

    tercera_url = str(os.getenv("CARNET_GSHEET_THIRD_URL", DEFAULT_GSHEET_THIRD_URL) or "").strip()
    if not tercera_url:
        return 0

    try:
        rows_third, fields_third = _leer_google_sheet_rows(tercera_url, logger)
    except Exception as exc:
        logger.warning("[ORQ][SECUENCIA] No se pudo leer tercera hoja para preasignación: %s", exc)
        return 0

    col_third_dni = _resolver_columna(fields_third, ["dni"])
    col_third_copia = _resolver_columna(
        fields_third,
        ["copia de secuencia de pago", "copia secuencia de pago", "secuencia de pago"],
    )
    col_third_estado = _resolver_columna(
        fields_third,
        ["estado secuencia de pago", "estado secuencia pago", "estado_secuencia_pago", "estado secuencia"],
    )

    if not col_third_copia or not col_third_estado:
        logger.warning("[ORQ][SECUENCIA] No se resolvieron columnas necesarias en tercera hoja")
        return 0

    lease_min = max(5, _safe_int_env("CARNET_TERCERA_RESERVA_LEASE_MINUTES", 120))
    disponibles = []
    for row_number, row in enumerate(rows_third, start=2):
        copia_raw = str(row.get(col_third_copia, "") or "").strip()
        if not copia_raw:
            continue

        estado_raw = str(row.get(col_third_estado, "") or "").strip()
        estado_norm = _normalizar_columna(estado_raw)
        if estado_norm == "usado":
            continue

        reserva_expirada = _estado_reserva_expirada(estado_raw, lease_minutes=lease_min)
        if estado_norm and not reserva_expirada:
            continue

        dni_actual = str(row.get(col_third_dni, "") or "").strip() if col_third_dni else ""
        if dni_actual and (not reserva_expirada) and estado_norm != "no encontrado":
            continue

        disponibles.append(
            {
                "row_number": row_number,
                "copia_secuencia_pago_raw": copia_raw,
                "copia_secuencia_pago": normalizar_copia_secuencia_pago(copia_raw),
            }
        )

    reservadas = 0
    for item, seq in zip(items, disponibles):
        dni = str(item.get("dni", "") or "").strip()
        if not dni:
            continue

        token = _token_estado_secuencia_reservada(dni)
        updates = {col_third_estado: token}
        if col_third_dni:
            updates[col_third_dni] = dni

        try:
            _actualizar_fila_tercera_hoja_por_row(
                logger,
                tercera_url,
                int(seq["row_number"]),
                updates,
                fieldnames=fields_third,
            )
        except Exception:
            continue

        item["tercera_url"] = tercera_url
        item["fieldnames_third"] = fields_third
        item["col_third_estado_sec"] = col_third_estado
        item["col_third_dni"] = col_third_dni
        item["tercera_row_number"] = int(seq["row_number"])
        item["copia_secuencia_pago_raw"] = str(seq["copia_secuencia_pago_raw"])
        item["copia_secuencia_pago"] = str(seq["copia_secuencia_pago"])
        item["nro_secuencia_objetivo"] = str(seq["copia_secuencia_pago"])
        item["nro_secuencia_origen"] = "tercera_hoja:preasignada_orquestador"
        item["tercera_reserva_token"] = token
        reservadas += 1

    return reservadas


def _distribuir_items_preasignados_para_workers(items: list[dict], workers: int) -> dict[int, list[dict]]:
    distribucion = {wid: [] for wid in range(1, max(1, int(workers or 1)) + 1)}
    if not items:
        return distribucion

    worker_ids = sorted(distribucion.keys())
    for idx, item in enumerate(items):
        wid = worker_ids[idx % len(worker_ids)]
        distribucion[wid].append(item)
    return distribucion


def _run_worker_unit(
    worker_id: int,
    workers: int,
    screen_w_eff: int,
    screen_h_eff: int,
    run_id: str,
    logger: logging.Logger,
    worker_items_file: str = "",
) -> int:
    env = os.environ.copy()
    env["MULTIWORKER_CHILD"] = "1"
    env["WORKER_ID"] = str(worker_id)
    env["WORKER_RUN_ID"] = run_id
    env["CARNET_ROW_BY_ROW"] = "1"
    env["CARNET_WORKER_MAX_ROWS"] = str(_safe_int_env("CARNET_WORKER_MAX_ROWS", 0))
    env["BROWSER_TILE_ENABLE"] = "1"
    env["BROWSER_TILE_TOTAL"] = str(workers)
    env["BROWSER_TILE_INDEX"] = str(worker_id - 1)
    env["BROWSER_TILE_SCREEN_W"] = str(_safe_int_env("BROWSER_TILE_SCREEN_W", screen_w_eff))
    env["BROWSER_TILE_SCREEN_H"] = str(_safe_int_env("BROWSER_TILE_SCREEN_H", screen_h_eff))
    env["BROWSER_TILE_TOP_OFFSET"] = str(_safe_int_env("BROWSER_TILE_TOP_OFFSET", 0))
    env["BROWSER_TILE_GAP"] = str(_safe_int_env("BROWSER_TILE_GAP", 6))
    env["BROWSER_TILE_FRAME_PAD"] = str(_safe_int_env("BROWSER_TILE_FRAME_PAD", 2))
    if worker_items_file:
        env["CARNET_WORKER_ITEMS_FILE"] = worker_items_file
        env.setdefault("CARNET_WORKER_SKIP_PRECHECK", "1")

    cmd = [sys.executable, str(BASE_DIR / "carnet_emision.py")]
    logger.info("[W%s] Iniciando worker de lote | run_id=%s", worker_id, run_id)

    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    worker_log = LOGS_DIR / f"worker_{worker_id}_batch_{stamp}.log"
    worker_log.write_text(
        "# STDOUT\n"
        + (proc.stdout or "")
        + "\n\n# STDERR\n"
        + (proc.stderr or ""),
        encoding="utf-8",
    )

    if proc.returncode != 0:
        logger.error("[W%s] Worker falló con exit_code=%s | log=%s", worker_id, proc.returncode, worker_log)
    else:
        logger.info("[W%s] Worker OK | log=%s", worker_id, worker_log)
    return proc.returncode


def _ejecutar_scheduled_multihilo_orquestador() -> int:
    logger = setup_logger("carnet_emision_multi", suffix="orchestrator")
    workers = max(1, min(4, _safe_int_env("SCHEDULED_WORKERS", 4)))
    screen_w_eff, screen_h_eff = _detect_windows_screen_size()
    units = _build_units_for_workers(workers)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

    preload_enabled = _as_bool_env("SCHEDULED_PRELOAD_ITEMS_FOR_WORKERS", default=True)
    if preload_enabled:
        max_preload = max(1, _safe_int_env("CARNET_WORKER_PRELOAD_SCAN_ROWS", 600))
        permitir_stale = _as_bool_env("CARNET_COMPARE_ALLOW_STALE_IN_PROGRESS", default=True)
        try:
            candidatos = _cargar_cruce_pendiente_desde_hojas(
                logger,
                max_rows=max_preload,
                preasignar_secuencias=False,
                permitir_en_proceso_expirado=permitir_stale,
            )
        except Exception as exc:
            logger.warning("Precarga de items falló; se usará reserva dinámica por worker: %s", exc)
            candidatos = []

        if candidatos:
            secuencias_preasignadas = _preasignar_secuencia_inicial_por_item(logger, candidatos)
            asignacion = _distribuir_items_preasignados_para_workers(candidatos, workers)
            units = []
            for wid in sorted(asignacion.keys()):
                items_w = asignacion[wid]
                if not items_w:
                    continue
                items_file = LOGS_DIR / f"worker_{wid}_items_{run_id}.json"
                try:
                    items_file.write_text(
                        json.dumps(items_w, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    units.append({
                        "worker_id": wid,
                        "items_file": str(items_file),
                        "assigned": len(items_w),
                    })
                except Exception as exc:
                    logger.warning("No se pudo guardar items preasignados para W%s: %s", wid, exc)

            if units:
                logger.info(
                    "Precarga de registros activada: candidatos=%s | secuencias_preasignadas=%s | workers_activos=%s",
                    len(candidatos),
                    secuencias_preasignadas,
                    len(units),
                )

    if not units:
        logger.info("Sin unidades con trabajo para workers; fin de corrida")
        return 0

    logger.info(
        "SCHEDULED_MULTIWORKER activado | workers=%s | units=%s | run_id=%s | pantalla_efectiva=%sx%s",
        workers,
        len(units),
        run_id,
        screen_w_eff,
        screen_h_eff,
    )
    results = []

    def worker_loop(unit: dict):
        worker_id = int(unit.get("worker_id", 0) or 0)
        worker_items_file = str(unit.get("items_file", "") or "")
        code = _run_worker_unit(
            worker_id,
            workers,
            screen_w_eff,
            screen_h_eff,
            run_id,
            logger,
            worker_items_file=worker_items_file,
        )
        results.append({"worker": worker_id, "exit_code": code})

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(worker_loop, unit) for unit in units]
        for f in futures:
            f.result()

    failed = [r for r in results if int(r.get("exit_code", 1)) != 0]
    if failed:
        logger.error("Workers con fallo: %s", len(failed))
        for r in failed:
            logger.error("[W%s] exit=%s", r["worker"], r["exit_code"])
        return 1

    logger.info("Orquestador multihilo finalizado sin fallos")
    return 0


def main() -> int:
    if _multiworker_habilitado():
        return _ejecutar_scheduled_multihilo_orquestador()
    return ejecutar_flujo_secundario()


if __name__ == "__main__":
    raise SystemExit(main())
