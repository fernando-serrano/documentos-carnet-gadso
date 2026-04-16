from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .config import GaleniusConfig
from .selectors import SEL


class LoginGaleniusError(Exception):
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


def _es_login_exitoso(page, cfg: GaleniusConfig) -> bool:
    url_actual = page.url.lower()
    for token in cfg.success_url_contains:
        if token.lower() in url_actual:
            return True

    for sel in cfg.success_selectors:
        try:
            if page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue

    try:
        if page.locator(SEL["login_form"]).first.is_visible(timeout=400):
            return False
    except Exception:
        return True
    return False


def ejecutar_login_galenius(cfg: GaleniusConfig, logger, event_logger) -> str:
    _validar_config_login(cfg)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=cfg.headless, slow_mo=0)
        context = browser.new_context(no_viewport=True, ignore_https_errors=True)
        page = context.new_page()

        try:
            logger.info("[GALENIUS] Abriendo login: %s", cfg.url_login)
            event_logger.event("login_start", url=cfg.url_login)
            page.goto(cfg.url_login, wait_until="domcontentloaded", timeout=cfg.timeout_ms)

            page.locator(SEL["login_form"]).wait_for(state="visible", timeout=cfg.timeout_ms)
            page.locator(SEL["username"]).fill(cfg.usuario)
            page.locator(SEL["password"]).fill(cfg.contrasena)

            # El submit oficial dispara validaciones del backend y token CSRF.
            with page.expect_navigation(wait_until="domcontentloaded", timeout=cfg.timeout_ms):
                page.locator(SEL["submit"]).click(timeout=cfg.timeout_ms)

            if not _es_login_exitoso(page, cfg):
                texto_pagina = ""
                try:
                    texto_pagina = (page.locator("body").inner_text(timeout=1500) or "")[:700]
                except Exception:
                    texto_pagina = ""

                msg = (
                    "No se pudo confirmar login exitoso en Galenius. "
                    f"URL actual: {page.url}"
                )
                logger.error("[GALENIUS] %s", msg)
                event_logger.event(
                    "login_error",
                    reason="not_confirmed",
                    url=page.url,
                    body_excerpt=texto_pagina,
                )
                raise LoginGaleniusError(msg)

            logger.info("[GALENIUS] Login exitoso. URL actual: %s", page.url)
            event_logger.event("login_ok", url=page.url)
            return page.url
        except PlaywrightTimeoutError as exc:
            logger.exception("[GALENIUS] Timeout durante login")
            event_logger.event("login_error", reason="timeout", detail=str(exc))
            raise LoginGaleniusError(f"Timeout en login Galenius: {exc}") from exc
        finally:
            context.close()
            browser.close()
