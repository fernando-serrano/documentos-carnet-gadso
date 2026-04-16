from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import GaleniusConfig
from .scraping_utils import esperar_hasta, recolectar_textos_ui
from .selectors import LOGIN_ERROR_SELECTORS, SEL


class GaleniusFlowError(Exception):
    pass


class LoginGaleniusError(GaleniusFlowError):
    pass


def _validar_config_login(cfg: GaleniusConfig) -> None:
    faltantes = []
    if not cfg.url_login or "example.com" in cfg.url_login:
        faltantes.append("GALENIUS_URL_LOGIN")
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


def ejecutar_flujo_galenius(cfg: GaleniusConfig, logger, event_logger) -> dict:
    """
    Script unico del flujo Galenius.
    Etapa actual implementada: login robusto.
    Etapas siguientes: barrido lote PDF + descarga + compresion + resumen.
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
            event_logger.event("flow_start", stage="login", url=cfg.url_login)
            final_url = _ejecutar_login(page, cfg, logger, event_logger)

            # Placeholder de etapa de lote, preparado para continuar con HTML real.
            resumen = {
                "descargados": 0,
                "comprimidos": 0,
                "errores_descarga": 0,
                "errores_compresion": 0,
                "final_url": final_url,
            }
            event_logger.event("flow_finish", status="ok", **resumen)
            return resumen
        except PlaywrightTimeoutError as exc:
            event_logger.event("flow_error", reason="timeout", detail=str(exc))
            raise GaleniusFlowError(f"Timeout en flujo Galenius: {exc}") from exc
        finally:
            context.close()
            browser.close()
