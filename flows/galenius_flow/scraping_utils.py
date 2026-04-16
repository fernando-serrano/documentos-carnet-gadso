import re
import time


def _normalizar_texto(texto: str) -> str:
    return re.sub(r"\s+", " ", str(texto or "").strip())


def recolectar_textos_ui(page, selectores: list[str], max_por_selector: int = 6) -> list[str]:
    """
    Barrido robusto de mensajes estilo validadores de negocio del proyecto:
    - recorre múltiples selectores
    - deduplica
    - agrega fallback del body parcial
    """
    textos = []
    for sel in selectores:
        try:
            loc = page.locator(sel)
            total = min(loc.count(), max_por_selector)
            for i in range(total):
                raw = loc.nth(i).inner_text(timeout=600)
                t = _normalizar_texto(raw)
                if t:
                    textos.append(t)
        except Exception:
            continue

    try:
        body_excerpt = page.locator("body").inner_text(timeout=900)[:1200]
        if body_excerpt:
            textos.append(_normalizar_texto(body_excerpt))
    except Exception:
        pass

    vistos = set()
    unicos = []
    for t in textos:
        key = t.lower()
        if key in vistos:
            continue
        vistos.add(key)
        unicos.append(t)
    return unicos


def esperar_hasta(
    condicion_fn,
    timeout_ms: int,
    sleep_ms: int = 180,
):
    deadline = time.time() + (max(200, int(timeout_ms)) / 1000.0)
    last = None
    while time.time() < deadline:
        last = condicion_fn()
        if last:
            return last
        time.sleep(max(0.05, sleep_ms / 1000.0))
    return None
