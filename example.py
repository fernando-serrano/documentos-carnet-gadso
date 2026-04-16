import os
import sys
import shutil
import queue
import traceback
import threading
import subprocess
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import time
import re
import unicodedata
import itertools
from collections import deque
from datetime import date, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

try:
    import pandas as pd
except ImportError:
    pd = None

load_dotenv()

# ====================== INTENTO DE IMPORTAR OCR (opcional) ======================
OCR_AVAILABLE = False
OCR_BACKEND = "manual"
EASYOCR_READER = None
EASYOCR_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
EASYOCR_LANGS = ["en"]
np = None
try:
    from PIL import Image, ImageFilter, ImageEnhance, ImageOps
    from io import BytesIO
    import numpy as np
    import easyocr

    langs_env = str(os.getenv("EASYOCR_LANGS", "en") or "en")
    EASYOCR_LANGS = [x.strip() for x in langs_env.split(",") if x.strip()] or ["en"]
    EASYOCR_ALLOWLIST = str(os.getenv("EASYOCR_ALLOWLIST", EASYOCR_ALLOWLIST) or EASYOCR_ALLOWLIST).strip() or EASYOCR_ALLOWLIST
    easyocr_use_gpu = str(os.getenv("EASYOCR_USE_GPU", "0") or "0").strip().lower() in {"1", "true", "yes", "si", "sí"}

    EASYOCR_READER = easyocr.Reader(EASYOCR_LANGS, gpu=easyocr_use_gpu, verbose=False)
    OCR_AVAILABLE = True
    OCR_BACKEND = "easyocr"
    print(f"[INFO] OCR (easyocr) cargado correctamente | langs={EASYOCR_LANGS} | gpu={easyocr_use_gpu}")
except ImportError as e:
    print(f"[WARNING] easyocr no esta instalado ({e}) -> se usara modo MANUAL (captcha a mano)")
except Exception as e:
    print(f"[WARNING] Error al cargar easyocr: {e} -> modo MANUAL")

URL_LOGIN = "https://www.sucamec.gob.pe/sel/faces/login.xhtml?faces-redirect=true"
script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(script_dir)
excel_path_env = os.getenv("EXCEL_PATH", "").strip()
if excel_path_env:
    EXCEL_PATH = excel_path_env if os.path.isabs(excel_path_env) else os.path.join(project_root, excel_path_env)
else:
    EXCEL_PATH = os.path.join(project_root, "data", "programaciones-armas.xlsx")

CREDENCIALES = {
    "tipo_documento_valor": os.getenv("TIPO_DOC", "RUC"),
    "numero_documento": os.getenv("NUMERO_DOCUMENTO", ""),
    "usuario": os.getenv("USUARIO_SEL", ""),
    "contrasena": os.getenv("CLAVE_SEL", ""),
}

CREDENCIALES_SELVA = {
    "tipo_documento_valor": os.getenv("SELVA_TIPO_DOC", "RUC"),
    "numero_documento": os.getenv("SELVA_NUMERO_DOCUMENTO", ""),
    "usuario": os.getenv("SELVA_USUARIO_SEL", ""),
    "contrasena": os.getenv("SELVA_CLAVE_SEL", ""),
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

    # ── Menú PanelMenu PrimeFaces ─────────────────────────────────────────────
    # Header del acordeón CITAS  ->  el <h3> que contiene el <a>CITAS</a>
    # Hacemos clic en él para expandir/colapsar el panel
    "menu_citas_header": '#j_idt11\\:menuPrincipal .ui-panelmenu-header:has(a:text-is("CITAS"))',

    # Panel de contenido que se despliega al hacer clic en el header CITAS
    # id fijo según el HTML: j_idt11:menuPrincipal_7
    "menu_citas_panel": '#j_idt11\\:menuPrincipal_7',

    # Ítem "RESERVAS DE CITAS" — usa el onclick con menuid='7_1'
    # Selector más robusto: busca dentro del panel CITAS el span con ese texto
    "submenu_reservas": '#j_idt11\\:menuPrincipal_7 span.ui-menuitem-text:text-is("RESERVAS DE CITAS")',

    # ── SelectOneMenu: tipo de cita en Gestión de Citas ──────────────────────
    "tipo_cita_trigger": '#gestionCitasForm\\:j_idt32 .ui-selectonemenu-trigger',
    "tipo_cita_panel": '#gestionCitasForm\\:j_idt32_panel',
    "tipo_cita_label": '#gestionCitasForm\\:j_idt32_label',
    "tipo_cita_opcion_poligono": '#gestionCitasForm\\:j_idt32_panel li[data-label="EXAMEN PARA POLÍGONO DE TIRO"]',

    # ── Reserva de Cupos (tabGestion:creaCitaPolJurForm) ───────────────────
    "reserva_form": '#tabGestion\\:creaCitaPolJurForm',
    "sede_trigger": '#tabGestion\\:creaCitaPolJurForm\\:sedeId .ui-selectonemenu-trigger',
    "sede_panel": '#tabGestion\\:creaCitaPolJurForm\\:sedeId_panel',
    "sede_label": '#tabGestion\\:creaCitaPolJurForm\\:sedeId_label',
    "fecha_trigger": '#tabGestion\\:creaCitaPolJurForm\\:listaDiasId .ui-selectonemenu-trigger',
    "fecha_panel": '#tabGestion\\:creaCitaPolJurForm\\:listaDiasId_panel',
    "fecha_label": '#tabGestion\\:creaCitaPolJurForm\\:listaDiasId_label',

    # ── Tabla de programación de cupos ──────────────────────────────────────
    "tabla_programacion": '#tabGestion\\:creaCitaPolJurForm\\:dtProgramacion',
    "tabla_programacion_rows": '#tabGestion\\:creaCitaPolJurForm\\:dtProgramacion_data tr',
    "boton_siguiente": '#tabGestion\\:creaCitaPolJurForm button:has-text("Siguiente")',
    "boton_limpiar": '#tabGestion\\:creaCitaPolJurForm\\:botonLimpiar',

    # ── Paso 2 del Wizard ───────────────────────────────────────────────────
    "tipo_operacion_trigger": '#tabGestion\\:creaCitaPolJurForm\\:tipoOpe .ui-selectonemenu-trigger',
    "tipo_operacion_panel": '#tabGestion\\:creaCitaPolJurForm\\:tipoOpe_panel',
    "tipo_operacion_items": '#tabGestion\\:creaCitaPolJurForm\\:tipoOpe_panel li.ui-selectonemenu-item',
    "tipo_operacion_label": '#tabGestion\\:creaCitaPolJurForm\\:tipoOpe_label',
    "tipo_tramite_trigger": '#tabGestion\\:creaCitaPolJurForm\\:tipoTramite .ui-selectonemenu-trigger',
    "tipo_tramite_panel": '#tabGestion\\:creaCitaPolJurForm\\:tipoTramite_panel',
    "tipo_tramite_label": '#tabGestion\\:creaCitaPolJurForm\\:tipoTramite_label',
    "tipo_tramite_seg_priv": '#tabGestion\\:creaCitaPolJurForm\\:tipoTramite_panel li[data-label="SEGURIDAD PRIVADA"]',
    "doc_vig_input": '#tabGestion\\:creaCitaPolJurForm\\:nroDocVig_input',
    "doc_vig_panel": '#tabGestion\\:creaCitaPolJurForm\\:nroDocVig_panel',
    "doc_vig_items": '#tabGestion\\:creaCitaPolJurForm\\:nroDocVig_panel li.ui-autocomplete-item',
    "seleccione_solicitud_trigger": '#tabGestion\\:creaCitaPolJurForm\\:seleccioneSolicitud .ui-selectonemenu-trigger',
    "seleccione_solicitud_panel": '#tabGestion\\:creaCitaPolJurForm\\:seleccioneSolicitud_panel',
    "seleccione_solicitud_si": '#tabGestion\\:creaCitaPolJurForm\\:seleccioneSolicitud_panel li[id$="_1"]',
    "seleccione_solicitud_label": '#tabGestion\\:creaCitaPolJurForm\\:seleccioneSolicitud_label',
    "nro_solicitud_trigger": '#tabGestion\\:creaCitaPolJurForm\\:nroSolicitud .ui-selectonemenu-trigger',
    "nro_solicitud_panel": '#tabGestion\\:creaCitaPolJurForm\\:nroSolicitud_panel',
    "nro_solicitud_items": '#tabGestion\\:creaCitaPolJurForm\\:nroSolicitud_panel li.ui-selectonemenu-item',
    "nro_solicitud_label": '#tabGestion\\:creaCitaPolJurForm\\:nroSolicitud_label',

    # ── Paso 3 del Wizard (Resumen de Cita) ───────────────────────────────
    "fase3_panel": '#tabGestion\\:creaCitaPolJurForm\\:panelPaso4',
    "fase3_captcha_img": '#tabGestion\\:creaCitaPolJurForm\\:imgCaptcha',
    "fase3_captcha_input": '#tabGestion\\:creaCitaPolJurForm\\:textoCaptcha',
    "fase3_boton_refresh": '#tabGestion\\:creaCitaPolJurForm\\:botonCaptcha',
    "fase3_terminos_box": '#tabGestion\\:creaCitaPolJurForm\\:terminos .ui-chkbox-box',
    "fase3_terminos_input": '#tabGestion\\:creaCitaPolJurForm\\:terminos_input',
    "fase3_boton_generar_cita": '#tabGestion\\:creaCitaPolJurForm\\:j_idt561',
}


class SinCupoError(Exception):
    """Se lanza cuando la hora objetivo existe pero no tiene cupos libres."""


class FechaNoDisponibleError(Exception):
    """Se lanza cuando la fecha objetivo no aparece en el combo de fechas."""


class TurnoDuplicadoError(Exception):
    """Se lanza cuando SEL informa turno ya registrado para la persona/tipo de licencia."""


class CuposOcupadosPostValidacionError(Exception):
    """Se lanza cuando SEL indica que el horario ya se ocupó al generar la cita final."""


def _debug_turno_duplicado_activo() -> bool:
    return str(os.getenv("DEBUG_TURNO_DUPLICADO", "0") or "0").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _hora_adaptativa_habilitada() -> bool:
    """Activa selección flexible de horario con fallback por vecinos/bloques."""
    return str(os.getenv("ADAPTIVE_HOUR_SELECTION", "0") or "0").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _hora_adaptativa_bloque_mediodia_completo() -> bool:
    """Si está activo, en bloque 11:45-13:00 evalúa todos los slots del bloque."""
    return str(os.getenv("ADAPTIVE_HOUR_NOON_FULL_BLOCK", "1") or "1").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _log_debug_turno_duplicado(msg: str):
    if _debug_turno_duplicado_activo():
        print(f"[DEBUG][TURNO_DUPLICADO] {msg}")


def obtener_buffer_growl(page, limite: int = 8) -> list:
    """Devuelve últimas entradas capturadas por el monitor growl (diagnóstico opcional)."""
    try:
        data = page.evaluate(
            """
            (limit) => {
                const arr = window.__armasGrowlBuffer || [];
                return arr.slice(-Math.max(1, Number(limit) || 8)).map(x => x && x.text ? String(x.text) : '');
            }
            """,
            limite,
        )
        if isinstance(data, list):
            return [str(x or "").strip() for x in data if str(x or "").strip()]
    except Exception:
        pass
    return []


def _script_monitor_growl_js() -> str:
    return """
    (() => {
        if (window.__armasGrowlInstalled) return;
        window.__armasGrowlInstalled = true;
        window.__armasGrowlBuffer = window.__armasGrowlBuffer || [];

        const pushMessage = (text) => {
            if (!text) return;
            const t = String(text).trim();
            if (!t) return;
            window.__armasGrowlBuffer.push({ text: t, ts: Date.now() });
            if (window.__armasGrowlBuffer.length > 160) {
                window.__armasGrowlBuffer = window.__armasGrowlBuffer.slice(-160);
            }
        };

        const extractFromNode = (node) => {
            if (!node) return;
            const selectors = '.ui-growl-title, .ui-growl-message, .ui-growl-message-error, #mensajesGrowl_container .ui-growl-title, #mensajesGrowl_container .ui-growl-message';

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


def activar_monitor_growl(page):
    """Instala un buffer JS para conservar mensajes growl aunque desaparezcan del DOM."""
    try:
        monitor_script = _script_monitor_growl_js()
        page.add_init_script(script=monitor_script)
        page.evaluate(monitor_script)
        _log_debug_turno_duplicado("monitor growl instalado")
    except Exception:
        pass


def detectar_turno_duplicado_en_growl(page, max_wait_ms: int = 0) -> str:
    """Busca mensaje de turno duplicado en growls con espera opcional."""
    deadline = time.time() + (max_wait_ms / 1000.0)
    while True:
        mensajes = []
        instalacion_activa = False
        try:
            instalacion_activa = bool(page.evaluate("() => Boolean(window.__armasGrowlInstalled)"))
        except Exception:
            instalacion_activa = False

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

        # Mensajes históricos capturados por monitor (aunque ya no estén visibles).
        try:
            buffer_msgs = page.evaluate(
                """
                () => (window.__armasGrowlBuffer || []).map(x => x && x.text ? String(x.text) : '')
                """
            )
            if isinstance(buffer_msgs, list):
                for txt in buffer_msgs:
                    t = str(txt or "").strip()
                    if t:
                        mensajes.append(t)
        except Exception:
            pass

        # Fallback adicional: mensaje renderizado fuera de contenedores growl estándar.
        try:
            body_text = (page.locator("body").text_content(timeout=400) or "").strip()
            if body_text:
                mensajes.append(body_text)
        except Exception:
            pass

        # Fallback fuerte: buscar directamente en el HTML del documento (incluye nodos ocultos).
        try:
            html_doc = (page.content() or "").lower()
            if (
                "ya existe un turno registrado" in html_doc
                or ("misma persona" in html_doc and "tipo de licencia" in html_doc)
            ):
                _log_debug_turno_duplicado("mensaje detectado por fallback HTML")
                return "Ya existe un turno registrado para la misma persona y Tipo de Licencia"
        except Exception:
            pass

        for msg in mensajes:
            msg_low = msg.lower()
            if (
                "ya existe un turno registrado" in msg_low
                or ("misma persona" in msg_low and "tipo de licencia" in msg_low)
            ):
                _log_debug_turno_duplicado(f"mensaje detectado: {msg[:180]}")
                return msg

        if max_wait_ms <= 0 or time.time() >= deadline:
            if _debug_turno_duplicado_activo():
                ultimos = obtener_buffer_growl(page, limite=8)
                _log_debug_turno_duplicado(
                    f"sin match. monitor_instalado={instalacion_activa} | mensajes_buffer={len(ultimos)} | ultimos={ultimos}"
                )
            return ""
        page.wait_for_timeout(120)


def validar_turno_duplicado_o_lanzar(page, max_wait_ms: int = 0):
    """Lanza TurnoDuplicadoError si detecta mensaje en growl/DOM."""
    msg = detectar_turno_duplicado_en_growl(page, max_wait_ms=max_wait_ms)
    if msg:
        raise TurnoDuplicadoError(msg)


def esperar_transicion_a_fase3_o_turno_duplicado(page, timeout_ms: int = 12000):
    """
    Espera robusta de transición tras 'Siguiente' en Paso 2:
    - Si aparece mensaje de turno duplicado, lanza TurnoDuplicadoError.
    - Si aparece panel de Fase 3, retorna OK.
    - Si vence timeout sin ambas señales, lanza excepción de desincronización.
    """
    deadline = time.time() + (max(1000, int(timeout_ms)) / 1000.0)
    while time.time() < deadline:
        validar_turno_duplicado_o_lanzar(page, max_wait_ms=0)

        try:
            if page.locator(SEL["fase3_panel"]).is_visible(timeout=200):
                return
        except Exception:
            pass

        page.wait_for_timeout(180)

    # Último barrido por si el growl llegó al final de la ventana de espera.
    validar_turno_duplicado_o_lanzar(page, max_wait_ms=1200)
    raise Exception("No se confirmó transición a Fase 3 tras 'Siguiente' de Paso 2")


# ============================================================
# OCR helpers  (sin cambios)
# ============================================================

def corregir_captcha_ocr(texto_raw: str) -> str:
    if not texto_raw:
        return ""
    texto = texto_raw.strip().upper().replace(" ", "").replace("\n", "").replace("\r", "")
    texto = ''.join(c for c in texto if c.isalnum())
    return texto


def validar_captcha_texto(texto: str) -> bool:
    if not texto or len(texto) != 5:
        return False
    return texto.isalnum()


def captcha_fuzzy_normalize(texto: str) -> str:
    """
    Normalización suave para comparar candidatos OCR de CAPTCHA.
    No se usa directamente como resultado final, solo para puntuar consenso.
    """
    mapa = {
        "O": "0", "Q": "0", "D": "0",
        "I": "1", "L": "1",
        "Z": "2",
        "S": "5",  # para consenso, S suele confundirse con 5/8
        "T": "7",  # en este captcha T suele confundirse con 7
        "B": "8",
        "G": "6",
    }
    base = ''.join(c for c in str(texto or "").upper() if c.isalnum())
    return ''.join(mapa.get(c, c) for c in base)


def generar_candidatos_len5(texto: str) -> set:
    """Genera candidatos de longitud 5 desde una lectura OCR cruda."""
    limpio = ''.join(c for c in str(texto or "").upper() if c.isalnum())
    candidatos = set()

    if len(limpio) == 5:
        candidatos.add(limpio)

    if 6 <= len(limpio) <= 8:
        # Si OCR mete caracteres extra, probamos podas hasta len=5.
        quitar = len(limpio) - 5
        for idxs in itertools.combinations(range(len(limpio)), quitar):
            rec = ''.join(ch for i, ch in enumerate(limpio) if i not in idxs)
            if len(rec) == 5 and rec.isalnum():
                candidatos.add(rec)

    # Expansión por confusiones frecuentes (solo para casos ya len=5).
    expandidos = set(candidatos)
    swaps = {
        "0": ["O", "Q", "D"],
        "1": ["I", "L"],
        "2": ["Z"],
        "3": ["E"],
        "6": ["G"],
        "7": ["T"],
        "8": ["B", "S"],
        "5": ["S"],
        "E": ["3"],
        "B": ["8"],
    }
    for c in list(candidatos):
        for i, ch in enumerate(c):
            for alt in swaps.get(ch, []):
                expandidos.add(c[:i] + alt + c[i+1:])

    return expandidos


def seleccionar_mejor_captcha_por_consenso(observaciones: list) -> str:
    """Elige el mejor candidato len=5 por consenso entre varias lecturas OCR."""
    if not observaciones:
        return ""

    sets_obs = []
    for obs in observaciones:
        candidatos = generar_candidatos_len5(obs)
        if candidatos:
            sets_obs.append(candidatos)

    if not sets_obs:
        return ""

    universo = set().union(*sets_obs)
    mejor = ""
    mejor_score = -1
    mejor_exact = -1

    for cand in universo:
        cand_fuzzy = captcha_fuzzy_normalize(cand)
        score = 0
        exact = 0
        for cands_obs in sets_obs:
            fuzzy_obs = {captcha_fuzzy_normalize(x) for x in cands_obs}
            if cand_fuzzy in fuzzy_obs:
                score += 1
            if cand in cands_obs:
                exact += 1

        if (score > mejor_score) or (score == mejor_score and exact > mejor_exact):
            mejor = cand
            mejor_score = score
            mejor_exact = exact

    return mejor if validar_captcha_texto(mejor) else ""


def medir_consenso_captcha(candidato: str, observaciones: list) -> tuple:
    """Devuelve (fuzzy_hits, exact_hits, total_observaciones_validas)."""
    if not candidato:
        return 0, 0, 0

    sets_obs = []
    for obs in observaciones:
        candidatos = generar_candidatos_len5(obs)
        if candidatos:
            sets_obs.append(candidatos)

    if not sets_obs:
        return 0, 0, 0

    cand_fuzzy = captcha_fuzzy_normalize(candidato)
    fuzzy_hits = 0
    exact_hits = 0
    for cands_obs in sets_obs:
        fuzzy_obs = {captcha_fuzzy_normalize(x) for x in cands_obs}
        if cand_fuzzy in fuzzy_obs:
            fuzzy_hits += 1
        if candidato in cands_obs:
            exact_hits += 1

    return fuzzy_hits, exact_hits, len(sets_obs)


def captcha_tiene_ambiguedad(texto: str) -> bool:
    """Detecta caracteres con alta confusión visual para decidir refresh de captcha."""
    t = ''.join(c for c in str(texto or "").upper() if c.isalnum())
    if len(t) != 5:
        return True

    grupos_ambiguos = [
        set("A4"),
        set("1I"),
        set("I7"),
        set("S8"),
        set("S5"),
    ]

    for ch in t:
        for grupo in grupos_ambiguos:
            if ch in grupo:
                return True
    return False


def escribir_input_jsf(page, selector: str, valor: str):
    for intento in range(4):
        campo = page.locator(selector)
        campo.wait_for(state="visible", timeout=12000)

        # Intento principal: tipeo humano con delay alto para evitar pérdida de dígitos.
        campo.click()
        campo.press("Control+A")
        campo.press("Backspace")
        campo.type(valor, delay=65)
        campo.evaluate('el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }')
        page.wait_for_timeout(140)

        actual = campo.input_value().strip()
        if actual != valor:
            # Fallback fuerte: asignación directa del value y eventos JSF.
            campo.evaluate(
                '''(el, val) => {
                    el.focus();
                    el.value = val;
                    el.setAttribute("value", val);
                    el.dispatchEvent(new Event("input", { bubbles: true }));
                    el.dispatchEvent(new Event("change", { bubbles: true }));
                }''',
                valor
            )
            page.wait_for_timeout(120)
            actual = campo.input_value().strip()

        if actual == valor:
            # Dispara blur al final para el comportamiento JSF de validación.
            campo.evaluate('el => el.blur()')
            page.wait_for_timeout(220)
            try:
                confirmado = page.locator(selector).input_value().strip()
            except Exception:
                confirmado = ""
            if confirmado == valor:
                return
            actual = confirmado

        print(f"   [WARNING] Campo {selector}: esperado '{valor}', tiene '{actual}' -> reintentando ({intento+1}/4)")
        page.wait_for_timeout(260)

    raise Exception(f"No se pudo fijar correctamente el valor del campo {selector}")


def escribir_input_rapido(page, selector: str, valor: str):
    campo = page.locator(selector)
    campo.wait_for(state="visible", timeout=10000)
    campo.click()
    campo.fill(valor)
    campo.evaluate('el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }')
    campo.blur()
    if campo.input_value() != valor:
        campo.click()
        campo.press("Control+A")
        campo.press("Backspace")
        campo.type(valor, delay=10)
        campo.evaluate('el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); }')
        campo.blur()


def _is_scheduled_mode() -> bool:
    return os.getenv("RUN_MODE", "manual").strip().lower() == "scheduled"


def solve_captcha_manual(page):
    if _is_scheduled_mode():
        raise Exception(
            "CAPTCHA_MANUAL_REQUERIDO_EN_SCHEDULED: OCR no resolvio captcha y no hay entrada interactiva"
        )
    print("\n[MANUAL] MODO MANUAL ACTIVADO")
    print("Completa el codigo de verificacion en la ventana del navegador")
    input("[INFO] Cuando hayas escrito el captcha -> presiona ENTER para continuar...")


def preprocesar_imagen_captcha(img_bytes: bytes, variante: int = 0) -> 'Image':
    img = Image.open(BytesIO(img_bytes))
    img = img.convert('L')
    if variante == 0:
        img = ImageEnhance.Contrast(img).enhance(3.5)
        w, h = img.size
        img = img.resize((w * 4, h * 4), Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(size=3))
        img = ImageOps.invert(img)
        img = img.point(lambda p: 255 if p > 130 else 0)
        img = ImageEnhance.Sharpness(img).enhance(3.0)
    elif variante == 1:
        img = ImageEnhance.Contrast(img).enhance(2.5)
        w, h = img.size
        img = img.resize((w * 3, h * 3), Image.LANCZOS)
        img = img.filter(ImageFilter.MedianFilter(size=5))
        img = img.point(lambda p: 255 if p > 160 else 0)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
    else:
        img = ImageEnhance.Contrast(img).enhance(4.0)
        w, h = img.size
        img = img.resize((w * 5, h * 5), Image.LANCZOS)
        img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
        img = ImageOps.invert(img)
        img = img.point(lambda p: 255 if p > 110 else 0)
        img = ImageEnhance.Sharpness(img).enhance(4.0)
    return img


def _leer_texto_easyocr_desde_imagen(img: 'Image', decoder: str = "greedy") -> str:
    """Ejecuta easyOCR sobre una imagen PIL y devuelve texto bruto."""
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
        # Compatibilidad con versiones que no soporten todos los kwargs.
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


def solve_captcha_ocr_base(
    page,
    captcha_img_selector: str,
    boton_refresh_selector: str = None,
    contexto: str = "CAPTCHA",
    evitar_ambiguos: bool = False,
    min_fuzzy_hits: int = 0,
    max_intentos=6,
):
    """
    Motor OCR estilo login: acepta la primera lectura válida por intento.
    Si evitar_ambiguos=True, aplica un filtro adicional antes de aceptar.
    """
    if not OCR_AVAILABLE:
        return None

    NUM_VARIANTES = 3
    DECODERS = ["greedy", "beamsearch"]

    intento = 0
    while True:
        if max_intentos is not None and max_intentos > 0 and intento >= max_intentos:
            break
        intento += 1
        try:
            total_txt = str(max_intentos) if (max_intentos is not None and max_intentos > 0) else "∞"
            print(f" OCR {contexto}: intento interno {intento}/{total_txt}...")
            page.wait_for_timeout(200)
            img_bytes = page.locator(captcha_img_selector).screenshot(type="png")

            mejor_texto = None
            observaciones = []
            for variante in range(NUM_VARIANTES):
                img = preprocesar_imagen_captcha(img_bytes, variante=variante)
                for decoder in DECODERS:
                    texto_raw = _leer_texto_easyocr_desde_imagen(img, decoder=decoder)
                    texto = corregir_captcha_ocr(texto_raw)
                    observaciones.append(texto)

                    if validar_captcha_texto(texto):
                        print(f"   -> Variante {variante}, Decoder {decoder}: '{texto_raw}' -> '{texto}' [INFO]")
                        mejor_texto = texto
                        break
                    else:
                        print(f"   -> Variante {variante}, Decoder {decoder}: '{texto_raw}' -> '{texto}' (len={len(texto)}) [WARNING]")
                if mejor_texto:
                    break

            if not mejor_texto:
                mejor_texto = seleccionar_mejor_captcha_por_consenso(observaciones)
                if validar_captcha_texto(mejor_texto):
                    print(f"   [INFO] CAPTCHA por consenso -> Usando: {mejor_texto}")

            if mejor_texto:
                if evitar_ambiguos:
                    fuzzy_hits, exact_hits, total_hits = medir_consenso_captcha(mejor_texto, observaciones)
                    print(f"    Consenso OCR: fuzzy={fuzzy_hits}/{total_hits}, exacto={exact_hits}/{total_hits}")

                    es_ambiguo = captcha_tiene_ambiguedad(mejor_texto)
                    consenso_debil = total_hits > 0 and fuzzy_hits < min_fuzzy_hits

                    if es_ambiguo or consenso_debil:
                        motivo = "ambiguo" if es_ambiguo else "consenso débil"
                        print(f"   [WARNING] CAPTCHA {motivo} detectado ('{mejor_texto}') -> se solicitar uno nuevo")
                        if boton_refresh_selector:
                            page.locator(boton_refresh_selector).click(force=True)
                            page.wait_for_timeout(500)
                            continue

                print(f"   [INFO] CAPTCHA vlido -> Usando: {mejor_texto}")
                return mejor_texto

            if boton_refresh_selector:
                print("   [WARNING] Ninguna combinacin dio resultado -> Refrescando CAPTCHA...")
                print("-------------------------------------------")
                page.locator(boton_refresh_selector).click(force=True)
                page.wait_for_timeout(500)
            else:
                print("   [WARNING] Ninguna combinacin dio resultado (sin botn refresh configurado)")

        except Exception as e:
            print(f"   Error en intento {intento}: {str(e)}")
            page.wait_for_timeout(300)

    if max_intentos is None or max_intentos <= 0:
        print(f"[ERROR] No se pudo resolver {contexto} automticamente (modo sin lmite agotado por salida externa) -> modo manual")
    else:
        print(f"[ERROR] No se pudo resolver {contexto} automticamente despus de {max_intentos} intentos -> modo manual")
    return None


def solve_captcha_ocr_generico(
    page,
    captcha_img_selector: str,
    boton_refresh_selector: str = None,
    contexto: str = "CAPTCHA",
    evitar_ambiguos: bool = False,
):
    return solve_captcha_ocr_base(
        page,
        captcha_img_selector=captcha_img_selector,
        boton_refresh_selector=boton_refresh_selector,
        contexto=contexto,
        evitar_ambiguos=evitar_ambiguos,
        min_fuzzy_hits=6,
    )


def solve_captcha_ocr(page):
    """Lógica original estable del login: primera lectura válida por intento."""
    return solve_captcha_ocr_base(
        page,
        captcha_img_selector=SEL["captcha_img"],
        boton_refresh_selector=SEL["boton_refresh"],
        contexto="CAPTCHA",
        evitar_ambiguos=False,
        min_fuzzy_hits=0,
    )


def completar_fase_3_resumen(page):
    """Paso 3: resolver captcha del resumen y aceptar términos y condiciones."""
    print("\n Completando Fase 3 (Resumen de cita)...")

    try:
        page.locator(SEL["fase3_panel"]).wait_for(state="visible", timeout=12000)
    except Exception as e:
        try:
            validar_turno_duplicado_o_lanzar(page, max_wait_ms=4500)
        except TurnoDuplicadoError as e_dup:
            raise TurnoDuplicadoError(str(e_dup)) from e
        raise

    captcha_text = solve_captcha_ocr_base(
        page,
        captcha_img_selector=SEL["fase3_captcha_img"],
        boton_refresh_selector=None,
        contexto="CAPTCHA Fase 3",
        evitar_ambiguos=False,
        min_fuzzy_hits=0,
        max_intentos=None,
    )

    if captcha_text and len(captcha_text) == 5:
        escribir_input_rapido(page, SEL["fase3_captcha_input"], captcha_text)
        print(f"   [INFO] CAPTCHA Fase 3 escrito: {captcha_text}")
    else:
        print("   [WARNING] OCR no resolvi CAPTCHA Fase 3; usa ingreso manual en el navegador")
        solve_captcha_manual(page)

    checkbox_input = page.locator(SEL["fase3_terminos_input"])
    checkbox_box = page.locator(SEL["fase3_terminos_box"])
    checkbox_box.wait_for(state="visible", timeout=7000)

    marcado = False
    try:
        marcado = checkbox_input.is_checked()
    except Exception:
        marcado = False

    if not marcado:
        checkbox_box.click()
        page.wait_for_timeout(180)

    try:
        marcado = checkbox_input.is_checked()
    except Exception:
        marcado = False

    if not marcado:
        clase_box = checkbox_box.get_attribute("class") or ""
        if "ui-state-active" in clase_box:
            marcado = True

    if not marcado:
        raise Exception("No se pudo marcar 'Acepto los términos y condiciones de Sucamec'")

    print("   [INFO] Trminos y condiciones marcados")


def limpiar_para_siguiente_registro(page, motivo: str = ""):
    """Pulsa botón Limpiar para reiniciar el wizard y seguir con el siguiente registro."""
    boton_limpiar = page.locator(SEL["boton_limpiar"])
    boton_limpiar.wait_for(state="visible", timeout=8000)
    boton_limpiar.first.click(timeout=8000)
    page.wait_for_timeout(180)
    if motivo:
        print(f"   [INFO] Click en 'Limpiar' ({motivo})")
    else:
        print("   [INFO] Click en 'Limpiar'")


def generar_cita_final_con_reintento_rapido(page, max_intentos: int = 3):
    """
    Paso final opcional (desactivado por ahora en el flujo principal).
    Hace click en 'Generar Cita' y, si detecta error de captcha/validación,
    reintenta rápido regenerando el captcha de Fase 3.
    """
    print("\n Paso final opcional: Generar Cita (reintento rpido)")

    boton_generar = page.locator(SEL["fase3_boton_generar_cita"])
    boton_generar.wait_for(state="visible", timeout=10000)

    def recolectar_mensajes_ui(max_por_selector: int = 4) -> list:
        textos = []
        selectores = [
            ".ui-growl-item .ui-growl-title",
            ".ui-growl-item .ui-growl-message",
            ".ui-growl-message-error",
            ".ui-messages-error",
            ".ui-message-error",
            ".mensajeError",
        ]
        for selector in selectores:
            try:
                loc = page.locator(selector)
                total = min(loc.count(), max_por_selector)
                for i in range(total):
                    txt = (loc.nth(i).inner_text() or "").strip()
                    if txt:
                        textos.append(txt)
            except Exception:
                pass
        try:
            buffer_msgs = page.evaluate(
                """
                () => (window.__armasGrowlBuffer || []).slice(-20).map(x => x && x.text ? String(x.text) : '')
                """
            )
            if isinstance(buffer_msgs, list):
                for txt in buffer_msgs:
                    t = str(txt or "").strip()
                    if t:
                        textos.append(t)
        except Exception:
            pass
        # Deduplicar conservando orden.
        vistos = set()
        unicos = []
        for t in textos:
            if t not in vistos:
                vistos.add(t)
                unicos.append(t)
        return unicos

    def detectar_error_captcha(mensajes: list) -> str:
        for msg in mensajes:
            if re.search(r"captcha.*incorrect|error.*captcha|captcha", msg, flags=re.IGNORECASE):
                return msg
        return ""

    def detectar_error_cupos_ocupados(mensajes: list) -> str:
        patrones = [
            r"cupos?.*horario.*ocupad",
            r"cupos?.*ocupad",
            r"escoja\s+otro\s+horario",
            r"ya\s+han\s+sido\s+ocupados",
        ]
        for msg in mensajes:
            msg_norm = normalizar_texto_comparable(msg)
            if "CUPOS" in msg_norm and "HORARIO" in msg_norm and "OCUP" in msg_norm:
                return msg
            if any(re.search(p, msg, flags=re.IGNORECASE) for p in patrones):
                return msg
        return ""

    def detectar_exito_fuerte() -> bool:
        # Éxito real: salió de la pantalla de resumen (ya no se ve botón Generar Cita)
        # o cambió claramente de vista fuera de GestionCitas.
        try:
            if boton_generar.count() == 0 or not boton_generar.first.is_visible():
                return True
        except Exception:
            return True

        try:
            url_actual = page.url or ""
            if "/faces/aplicacion/" in url_actual and "GestionCitas.xhtml" not in url_actual:
                if page.locator(SEL["fase3_boton_generar_cita"]).count() == 0:
                    return True
        except Exception:
            pass
        return False

    for intento in range(1, max_intentos + 1):
        inicio_validacion = time.time()
        print(f"    Intento generar cita {intento}/{max_intentos}")
        boton_generar.click(timeout=10000)

        # Ventana corta de observación para capturar growl intermitente.
        error_captcha_msg = ""
        error_cupos_msg = ""
        ultimo_error = ""
        deadline = time.time() + 2.5
        while time.time() < deadline:
            mensajes = recolectar_mensajes_ui()
            if mensajes:
                for msg in mensajes:
                    if not ultimo_error:
                        ultimo_error = msg
                candidato_cupos = detectar_error_cupos_ocupados(mensajes)
                if candidato_cupos:
                    error_cupos_msg = candidato_cupos
                    break
                candidato = detectar_error_captcha(mensajes)
                if candidato:
                    error_captcha_msg = candidato
                    break

            if detectar_exito_fuerte():
                tiempo = time.time() - inicio_validacion
                print(f"   [INFO] Generar Cita confirmado en {tiempo:.2f}s")
                print(f"   -> URL: {page.url}")
                return True

            page.wait_for_timeout(120)

        tiempo = time.time() - inicio_validacion
        if error_cupos_msg:
            print(f"   [WARNING] Mensaje de cupos detectado: {error_cupos_msg}")
            raise CuposOcupadosPostValidacionError(error_cupos_msg)
        if error_captcha_msg:
            print(f"   [WARNING] Mensaje captcha detectado: {error_captcha_msg}")
        elif ultimo_error:
            print(f"   [WARNING] Mensaje detectado: {ultimo_error}")
        print(f"    Validacin final: {tiempo:.2f}s")

        if not error_captcha_msg:
            raise Exception(
                "No se pudo confirmar la generación de cita de forma robusta "
                "(sin seales claras de xito y sin captcha incorrecto explícito)"
            )

        # Reintento rápido: resolver captcha de Fase 3 y remarcado de términos si aplica.
        nuevo_captcha = solve_captcha_ocr_base(
            page,
            captcha_img_selector=SEL["fase3_captcha_img"],
            boton_refresh_selector=SEL["fase3_boton_refresh"],
            contexto="CAPTCHA Fase 3 (reintento final)",
            evitar_ambiguos=False,
            min_fuzzy_hits=0,
            max_intentos=3,
        )

        if nuevo_captcha and len(nuevo_captcha) == 5:
            escribir_input_rapido(page, SEL["fase3_captcha_input"], nuevo_captcha)
            print(f"   [INFO] CAPTCHA reintento escrito: {nuevo_captcha}")
        else:
            print("   [WARNING] OCR no resolvi captcha en reintento final; pasar a ingreso manual")
            solve_captcha_manual(page)

        try:
            if not page.locator(SEL["fase3_terminos_input"]).is_checked():
                page.locator(SEL["fase3_terminos_box"]).click()
                page.wait_for_timeout(150)
        except Exception:
            pass

    raise Exception("No se pudo generar cita tras reintentos rápidos")


def validar_resultado_login_por_ui(page, timeout_ms: int = 3000):
    """
    Determina resultado de login por señales de UI (no por URL):
    - Éxito: aparece menú principal/controles de sesión autenticada.
    - Falla: aparece mensaje de error de validación/captcha.
    Devuelve: (login_ok: bool, mensaje_error: str|None, tiempo_segundos: float)
    """
    inicio = time.time()

    selectores_exito = [
        "#j_idt11\\:menuPrincipal",
        "#j_idt11\\:j_idt18",  # botón "Cerrar Sesión"
        "form#gestionCitasForm",
    ]
    selectores_error = [
        ".ui-messages-error",
        ".ui-message-error",
        ".ui-growl-message-error",
        ".mensajeError",
        "[class*='error']",
        "[class*='Error']",
    ]

    while (time.time() - inicio) * 1000 < timeout_ms:
        try:
            if "/faces/aplicacion/inicio.xhtml" in (page.url or ""):
                return True, None, (time.time() - inicio)
        except Exception:
            pass

        for sel in selectores_exito:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return True, None, (time.time() - inicio)
            except Exception:
                pass

        for sel in selectores_error:
            try:
                loc = page.locator(sel)
                total = min(loc.count(), 3)
                for i in range(total):
                    txt = (loc.nth(i).inner_text() or "").strip()
                    if txt:
                        return False, txt, (time.time() - inicio)
            except Exception:
                pass

        page.wait_for_timeout(120)

    # Última comprobación rápida al vencer el timeout.
    try:
        if "/faces/aplicacion/inicio.xhtml" in (page.url or ""):
            return True, None, (time.time() - inicio)
    except Exception:
        pass

    for sel in selectores_exito:
        try:
            if page.locator(sel).count() > 0:
                return True, None, (time.time() - inicio)
        except Exception:
            pass

    mensaje_error = None
    for sel in selectores_error:
        try:
            loc = page.locator(sel)
            total = min(loc.count(), 3)
            for i in range(total):
                txt = (loc.nth(i).inner_text() or "").strip()
                if txt:
                    mensaje_error = txt
                    break
            if mensaje_error:
                break
        except Exception:
            pass

    return False, mensaje_error, (time.time() - inicio)


def activar_pestana_autenticacion_tradicional(page):
    """Activa la pestaña tradicional sin depender de ids j_idt variables."""
    try:
        campo_doc = page.locator(SEL["numero_documento"])
        if campo_doc.count() > 0 and campo_doc.first.is_visible():
            print("[INFO] Pestaña tradicional ya activa")
            return
    except Exception:
        pass

    candidatos = [
        SEL["tab_tradicional"],
        '#tabViewLogin a:has-text("Autenticación Tradicional")',
        '#tabViewLogin a:has-text("Autenticacion Tradicional")',
    ]

    ultimo_error = None
    for sel in candidatos:
        try:
            tab = page.locator(sel).first
            tab.wait_for(state="visible", timeout=6000)
            tab.click(timeout=6000)
            page.locator(SEL["numero_documento"]).wait_for(state="visible", timeout=8000)
            print("[INFO] Pestaña 'Autenticación Tradicional' seleccionada")
            return
        except Exception as e:
            ultimo_error = e

    raise Exception(
        "No se pudo activar la pestaña 'Autenticación Tradicional'. "
        f"Detalle: {ultimo_error}"
    )


def pagina_muestra_servicio_no_disponible(page) -> bool:
    """Detecta HTML de caída del servicio (HTTP 503 / Service Unavailable)."""
    # Señales de estado saludable: si aparecen, no hay caída.
    selectores_ok = [
        SEL["tab_tradicional"],
        SEL["numero_documento"],
        "#j_idt11\\:menuPrincipal",
        "form#gestionCitasForm",
        SEL["reserva_form"],
    ]
    for sel in selectores_ok:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                return False
        except Exception:
            pass

    # Título de pestaña en estados 503.
    try:
        titulo = (page.title() or "").strip().upper()
        if "SERVICE UNAVAILABLE" in titulo:
            return True
    except Exception:
        pass

    # h1 explícito del error de Apache/Proxy.
    try:
        h1 = (page.locator("h1").first.inner_text() or "").strip().upper()
        if "SERVICE UNAVAILABLE" in h1:
            return True
    except Exception:
        pass

    # Fallback textual ligero (evita leer todo el HTML para no ralentizar iteraciones).
    try:
        body_text = (page.locator("body").inner_text() or "").upper()
        if "SERVICE UNAVAILABLE" in body_text and "AUTENTICACION TRADICIONAL" not in body_text:
            return True
    except Exception:
        pass

    return False


def esperar_hasta_servicio_disponible(page, url_objetivo: str, espera_segundos: int = 8):
    """
    Si SUCAMEC responde con Service Unavailable, espera y reintenta hasta recuperar servicio.
    Se mantiene en bucle indefinido por requerimiento operativo.
    """
    intento = 0
    while pagina_muestra_servicio_no_disponible(page):
        intento += 1
        print(f"[WARNING] SUCAMEC no disponible (Service Unavailable). Reintento {intento} en {espera_segundos}s...")
        time.sleep(espera_segundos)
        try:
            page.goto(url_objetivo, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print(f"    Error al reintentar acceso: {e}")


def normalizar_fecha_excel(valor_fecha: str) -> str:
    """Convierte fechas de Excel al formato dd/mm/yyyy esperado por SEL."""
    texto = str(valor_fecha or "").strip()
    if not texto:
        return ""

    # Si ya viene en formato con barras, asumimos entrada local dd/mm/yyyy.
    if "/" in texto:
        dt = pd.to_datetime(texto, errors="coerce", dayfirst=True)
        if pd.notna(dt):
            return dt.strftime("%d/%m/%Y")

    # Caso típico de Excel ISO: 2026-03-31 00:00:00 / 2026-03-31
    dt = pd.to_datetime(texto, errors="coerce", dayfirst=False)
    if pd.notna(dt):
        return dt.strftime("%d/%m/%Y")

    # Si ya viene como dd/mm/yyyy o similar, lo conservamos sin hora
    texto = texto.split(" ")[0]
    return texto


def normalizar_hora_fragmento(valor_hora: str) -> str:
    """Normaliza una hora a HH:MM (ej: 8:5 -> 08:05)."""
    texto = str(valor_hora or "").strip().replace(".", ":")
    if ":" not in texto:
        return texto
    partes = texto.split(":")
    if len(partes) != 2:
        return texto
    try:
        hh = int(partes[0])
        mm = int(partes[1])
    except ValueError:
        return texto
    return f"{hh:02d}:{mm:02d}"


def normalizar_hora_rango(valor_rango: str) -> str:
    """Normaliza rango de hora a HH:MM-HH:MM."""
    texto = str(valor_rango or "").strip()
    if not texto:
        return ""
    texto = texto.replace("–", "-").replace("—", "-").replace(" a ", "-").replace(" ", "")
    partes = texto.split("-")
    if len(partes) != 2:
        return texto
    inicio = normalizar_hora_fragmento(partes[0])
    fin = normalizar_hora_fragmento(partes[1])
    return f"{inicio}-{fin}"


def _parsear_rango_hora_a_minutos(valor_rango: str):
    """Convierte HH:MM-HH:MM a minutos (inicio, fin). Devuelve None si no parsea."""
    texto = normalizar_hora_rango(valor_rango)
    m = re.match(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2})$", texto)
    if not m:
        return None
    ini = int(m.group(1)) * 60 + int(m.group(2))
    fin = int(m.group(3)) * 60 + int(m.group(4))
    return ini, fin


def _formatear_minutos_hhmm(total_min: int) -> str:
    hh = (int(total_min) // 60) % 24
    mm = int(total_min) % 60
    return f"{hh:02d}:{mm:02d}"


def _rango_desplazado_15m(valor_rango: str, delta_slots: int) -> str:
    parsed = _parsear_rango_hora_a_minutos(valor_rango)
    if not parsed:
        return ""
    ini, fin = parsed
    delta = int(delta_slots) * 15
    return f"{_formatear_minutos_hhmm(ini + delta)}-{_formatear_minutos_hhmm(fin + delta)}"


def convertir_a_entero(texto: str) -> int:
    numeros = re.findall(r"\d+", str(texto or ""))
    return int(numeros[0]) if numeros else 0


def normalizar_texto_comparable(texto: str) -> str:
    base = str(texto or "").strip().upper()
    base = unicodedata.normalize("NFKD", base)
    base = "".join(c for c in base if not unicodedata.combining(c))
    base = re.sub(r"\s+", " ", base)
    return base


def limpiar_valor_excel(valor: str) -> str:
    """Limpia artefactos comunes de celdas Excel exportadas a texto."""
    t = str(valor or "")
    t = re.sub(r"_x[0-9A-Fa-f]{4}_", "", t)
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def extraer_token_solicitud(valor: str) -> str:
    """Obtiene el número principal de solicitud para comparar dentro del label del combo."""
    texto = str(valor or "")
    grupos = re.findall(r"\d+", texto)
    if not grupos:
        return ""
    token = grupos[0].lstrip("0")
    return token if token else "0"


def normalizar_tipo_arma_excel(valor: str) -> str:
    """Normaliza valor de tipo_arma del Excel para comparaciones."""
    base = normalizar_texto_comparable(valor)
    equivalencias = {
        "LARG": "LARGA",
        "LARGA": "LARGA",
        "CORTA": "CORTA",
        "PISTOLA": "PISTOLA",
        "REVOLVER": "REVOLVER",
        "CARABINA": "CARABINA",
        "ESCOPETA": "ESCOPETA",
    }
    return equivalencias.get(base, base)


def inferir_objetivo_arma_desde_excel(valor: str) -> str:
    """
    Interpreta texto libre de tipo_arma y devuelve una clave usable.
    Ejemplos válidos: "CORTA", "CORTA PISTOLA", "LARGA ESCOPETA".
    """
    base = normalizar_texto_comparable(valor)
    if not base:
        return ""

    # Priorizamos el arma específica si está presente.
    if "ESCOPETA" in base:
        return "ESCOPETA"
    if "CARABINA" in base:
        return "CARABINA"
    if "REVOLVER" in base:
        return "REVOLVER"
    if "PISTOLA" in base:
        return "PISTOLA"

    # Si no hay arma específica, devolvemos tipo general.
    if "LARG" in base:
        return "LARGA"
    if "CORT" in base:
        return "CORTA"

    return normalizar_tipo_arma_excel(base)


def fecha_comparable(valor_fecha: str) -> str:
    """Convierte fecha de Excel a una cadena comparable dd/mm/yyyy."""
    return normalizar_fecha_excel(valor_fecha)


def normalizar_ruc_operativo(valor_ruc: str) -> str:
    """Normaliza texto de RUC/razón social para clasificación operativa."""
    return normalizar_texto_comparable(limpiar_valor_excel(valor_ruc))


def obtener_indices_relacionados_registro(registro: dict) -> list:
    """Devuelve índices de Excel asociados al registro actual (sin duplicados)."""
    indices = []
    for idx in registro.get("_excel_indices_relacionados", []) or []:
        try:
            indices.append(int(idx))
        except Exception:
            continue

    idx_principal = registro.get("_excel_index", None)
    try:
        if idx_principal is not None:
            indices.append(int(idx_principal))
    except Exception:
        pass

    return sorted(set(indices))


def clasificar_motivo_detencion(error: BaseException) -> str:
    """Clasifica cierres/interrupciones para logs operativos más claros."""
    if isinstance(error, KeyboardInterrupt):
        return "INTERRUPCION_MANUAL"

    texto = str(error or "").lower()
    señales_cierre = [
        "target page, context or browser has been closed",
        "browser has been closed",
        "context closed",
        "page closed",
        "connection closed",
    ]
    if any(s in texto for s in señales_cierre):
        return "VENTANA_CERRADA"

    return ""


def obtener_grupo_ruc(valor_ruc: str) -> str:
    """Clasifica el RUC/razón social en SELVA, JV u OTRO."""
    base = normalizar_ruc_operativo(valor_ruc)
    if "SELVA" in base or "20493762789" in base:
        return "SELVA"
    if "J&V" in base or "J V" in base or "RESGUARDO" in base or "20100901481" in base:
        return "JV"
    return "OTRO"


def prioridad_orden(valor_prioridad: str) -> int:
    """ALTA tiene precedencia sobre NORMAL; cualquier otro valor cae en NORMAL."""
    base = normalizar_texto_comparable(limpiar_valor_excel(valor_prioridad))
    return 0 if base == "ALTA" else 1


def resolver_credenciales_por_grupo_ruc(grupo_ruc: str) -> dict:
    if grupo_ruc == "SELVA":
        return CREDENCIALES_SELVA
    return CREDENCIALES


def obtener_trabajos_pendientes_excel(ruta_excel: str) -> list:
    """
    Devuelve trabajos pendientes únicos y ordenados por prioridad operativa:
    1) SELVA primero
    2) prioridad Alta antes de Normal
    3) orden original del Excel como desempate
    """
    if pd is None:
        raise Exception("Falta dependencia 'pandas'. Instala con: pip install pandas openpyxl")
    if not os.path.exists(ruta_excel):
        raise Exception(f"No se encontró el Excel en: {ruta_excel}")

    df = pd.read_excel(ruta_excel, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]
    if "estado" not in df.columns:
        raise Exception("El Excel no contiene la columna 'estado'")

    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).apply(limpiar_valor_excel)

    if "doc_vigilante" not in df.columns:
        df["doc_vigilante"] = ""
    if "dni" not in df.columns:
        df["dni"] = ""
    if "nro_solicitud" not in df.columns:
        df["nro_solicitud"] = ""
    if "ruc" not in df.columns:
        df["ruc"] = ""
    if "prioridad" not in df.columns:
        df["prioridad"] = "Normal"

    fecha_col_programacion = "fecha_programacion" if "fecha_programacion" in df.columns else "fecha"
    if fecha_col_programacion not in df.columns:
        raise Exception("El Excel no contiene columna de fecha (fecha_programacion/fecha)")

    pendientes = df[df["estado"].str.upper().str.contains("PENDIENTE", na=False)].copy()
    print(f"   -> Registros con estado 'PENDIENTE': {len(pendientes)}")
    if pendientes.empty:
        return []

    validar_hoy = os.getenv("VALIDAR_FECHA_PROGRAMACION_HOY", "1").strip().lower() in {"1", "true", "si", "sí", "yes"}
    if validar_hoy:
        hoy = date.today().strftime("%d/%m/%Y")
        print(f"   -> Validando fecha de hoy: {hoy}")
        pendientes_antes = len(pendientes)
        pendientes = pendientes[
            pendientes[fecha_col_programacion].apply(fecha_comparable) == hoy
        ]
        print(f"   -> Registros despus de filtrar por fecha: {len(pendientes)} (filtrados: {pendientes_antes - len(pendientes)})")
        if pendientes.empty:
            return []

    pendientes["_idx_excel"] = pendientes.index
    pendientes["_doc_norm"] = pendientes.apply(
        lambda r: str(r.get("doc_vigilante", "") or r.get("dni", "")).strip(),
        axis=1,
    )
    pendientes["_nro_norm"] = pendientes["nro_solicitud"].apply(lambda v: str(v or "").strip())
    pendientes["_fecha_prog"] = pendientes[fecha_col_programacion].apply(fecha_comparable)
    pendientes["_ruc_raw"] = pendientes["ruc"].apply(lambda v: str(v or "").strip())
    pendientes["_ruc_grupo"] = pendientes["_ruc_raw"].apply(obtener_grupo_ruc)
    pendientes["_ruc_orden"] = pendientes["_ruc_grupo"].map({"SELVA": 0, "JV": 1, "OTRO": 2})
    pendientes["_prioridad_raw"] = pendientes["prioridad"].apply(lambda v: str(v or "").strip())
    pendientes["_prioridad_orden"] = pendientes["_prioridad_raw"].apply(prioridad_orden)

    pendientes = pendientes.sort_values(
        by=["_ruc_orden", "_prioridad_orden", "_idx_excel"],
        ascending=[True, True, True],
        kind="stable",
    )

    trabajos = []
    claves_vistas = set()
    for _, fila in pendientes.iterrows():
        clave = (
            fila.get("_doc_norm", ""),
            fila.get("_nro_norm", ""),
            fila.get("_fecha_prog", ""),
            fila.get("_ruc_grupo", "OTRO"),
        )
        if clave in claves_vistas:
            continue
        claves_vistas.add(clave)
        trabajos.append(
            {
                "idx_excel": int(fila.get("_idx_excel")),
                "ruc": fila.get("_ruc_raw", ""),
                "ruc_grupo": fila.get("_ruc_grupo", "OTRO"),
                "prioridad": fila.get("_prioridad_raw", "Normal"),
                "fecha_programacion": fila.get("_fecha_prog", ""),
            }
        )

    return trabajos


def obtener_indices_pendientes_excel(ruta_excel: str) -> list:
    """
    Devuelve índices de trabajo (únicos) en estado Pendiente.
    Deduplica por doc_vigilante+dni, nro_solicitud y fecha_programacion/fecha,
    para evitar reprocesar registros que pertenecen a una misma cita/iteración.
    """
    trabajos = obtener_trabajos_pendientes_excel(ruta_excel)
    return [t["idx_excel"] for t in trabajos]


def cargar_primer_registro_pendiente_desde_excel(ruta_excel: str, indice_excel_objetivo: int = None) -> dict:
    """
    Lee el Excel y devuelve el primer registro con estado 'Pendiente'.
    Campos mínimos requeridos para este paso: sede y fecha.
    """
    if pd is None:
        raise Exception("Falta dependencia 'pandas'. Instala con: pip install pandas openpyxl")

    if not os.path.exists(ruta_excel):
        raise Exception(f"No se encontró el Excel en: {ruta_excel}")

    df = pd.read_excel(ruta_excel, dtype=str)
    df.columns = [str(c).strip() for c in df.columns]

    columnas_requeridas = {"sede", "fecha", "hora_rango", "tipo_operacion", "nro_solicitud", "tipo_arma", "arma", "estado"}
    faltantes = [c for c in columnas_requeridas if c not in df.columns]
    if faltantes:
        raise Exception(f"Faltan columnas requeridas en Excel: {faltantes}")

    for col in df.columns:
        df[col] = df[col].fillna("").astype(str).apply(limpiar_valor_excel)

    pendientes = df[df["estado"].str.upper().str.contains("PENDIENTE", na=False)]
    if pendientes.empty:
        raise Exception("No hay registros con estado 'Pendiente' en el Excel")

    indice_primer_pendiente = pendientes.index[0] if indice_excel_objetivo is None else indice_excel_objetivo
    if indice_primer_pendiente not in pendientes.index:
        raise Exception(f"El índice objetivo {indice_primer_pendiente} no está en estado Pendiente")

    registro = pendientes.loc[indice_primer_pendiente].to_dict()
    registro["_excel_index"] = int(indice_primer_pendiente)
    registro["_excel_path"] = ruta_excel

    fecha_col_programacion = "fecha_programacion" if "fecha_programacion" in df.columns else "fecha"
    fecha_programacion_valor = fecha_comparable(registro.get(fecha_col_programacion, registro.get("fecha", "")))

    sede = registro.get("sede", "").strip()
    fecha = normalizar_fecha_excel(registro.get("fecha", ""))
    hora_rango = normalizar_hora_rango(registro.get("hora_rango", ""))
    tipo_operacion = registro.get("tipo_operacion", "").strip()
    nro_solicitud = registro.get("nro_solicitud", "").strip()
    doc_vigilante = registro.get("doc_vigilante", registro.get("dni", "")).strip()
    tipo_arma_base = inferir_objetivo_arma_desde_excel(registro.get("tipo_arma", ""))
    arma_base = inferir_objetivo_arma_desde_excel(registro.get("arma", ""))

    if not sede or not fecha or not hora_rango:
        raise Exception("El registro pendiente no tiene 'sede', 'fecha' o 'hora_rango' con valor")
    if not tipo_operacion or not nro_solicitud or not doc_vigilante:
        raise Exception("El registro pendiente no tiene 'tipo_operacion', 'doc_vigilante/dni' o 'nro_solicitud'")
    if not tipo_arma_base:
        raise Exception("El registro pendiente no tiene 'tipo_arma'")
    if not arma_base:
        raise Exception("El registro pendiente no tiene 'arma'")

    # Agrupa registros de la misma programación/cita:
    # mismo usuario + misma solicitud + misma fecha_programacion/fecha.
    fecha_base = fecha_comparable(registro.get(fecha_col_programacion, registro.get("fecha", "")))
    doc_base = doc_vigilante
    nro_base = nro_solicitud
    pendientes_aux = pendientes.copy()
    pendientes_aux["fecha_norm"] = pendientes_aux[fecha_col_programacion].apply(fecha_comparable)
    pendientes_aux["doc_norm"] = pendientes_aux.apply(
        lambda r: str(r.get("doc_vigilante", "") or r.get("dni", "")).strip(), axis=1
    )
    pendientes_aux["nro_norm"] = pendientes_aux["nro_solicitud"].apply(lambda v: str(v or "").strip())
    relacionados = pendientes_aux[
        (pendientes_aux["fecha_norm"] == fecha_base) &
        (pendientes_aux["doc_norm"] == doc_base) &
        (pendientes_aux["nro_norm"] == nro_base)
    ]
    indices_relacionados = [int(i) for i in relacionados.index.tolist()]
    if int(indice_primer_pendiente) not in indices_relacionados:
        indices_relacionados.append(int(indice_primer_pendiente))
    indices_relacionados = sorted(set(indices_relacionados))

    # Validación adicional: revisar explícitamente el siguiente registro.
    siguiente_mismo_doc_y_fecha = False
    siguiente_idx = indice_primer_pendiente + 1
    if siguiente_idx in df.index:
        fila_sig = df.loc[siguiente_idx]
        estado_sig = str(fila_sig.get("estado", "")).strip().upper()
        doc_sig = str(fila_sig.get("doc_vigilante", "") or fila_sig.get("dni", "")).strip()
        nro_sig = str(fila_sig.get("nro_solicitud", "")).strip()
        fecha_sig = fecha_comparable(fila_sig.get(fecha_col_programacion, fila_sig.get("fecha", "")))
        if estado_sig == "PENDIENTE" and doc_sig == doc_base and nro_sig == nro_base and fecha_sig == fecha_base:
            siguiente_mismo_doc_y_fecha = True

    tipos_arma_excel = []
    armas_excel = []
    objetivos_arma = []
    armas_especificas = {"PISTOLA", "REVOLVER", "CARABINA", "ESCOPETA"}

    for _, fila in relacionados.iterrows():
        tipo_raw = str(fila.get("tipo_arma", "")).strip()
        arma_raw = str(fila.get("arma", "")).strip()
        tipo_inferido = inferir_objetivo_arma_desde_excel(tipo_raw)
        arma_inferida = inferir_objetivo_arma_desde_excel(arma_raw)

        if not arma_inferida:
            arma_inferida = inferir_objetivo_arma_desde_excel(tipo_raw)

        tipo_norm_texto = normalizar_texto_comparable(tipo_raw)
        if arma_inferida in {"PISTOLA", "REVOLVER"}:
            tipo_fila = "CORTA"
        elif arma_inferida in {"CARABINA", "ESCOPETA"}:
            tipo_fila = "LARGA"
        elif "CORT" in tipo_norm_texto or tipo_inferido == "CORTA":
            tipo_fila = "CORTA"
        elif "LARG" in tipo_norm_texto or tipo_inferido == "LARGA":
            tipo_fila = "LARGA"
        else:
            continue

        if arma_inferida in armas_especificas:
            arma_objetivo = arma_inferida
        else:
            arma_objetivo = "PISTOLA" if tipo_fila == "CORTA" else "CARABINA"

        if tipo_fila not in tipos_arma_excel:
            tipos_arma_excel.append(tipo_fila)
        if arma_objetivo not in armas_excel:
            armas_excel.append(arma_objetivo)

        par_objetivo = (tipo_fila, arma_objetivo)
        if par_objetivo not in objetivos_arma:
            objetivos_arma.append(par_objetivo)

    if not objetivos_arma:
        # Fallback mínimo usando el primer registro, manteniendo origen en Excel.
        if arma_base in {"PISTOLA", "REVOLVER"}:
            tipo_base = "CORTA"
            arma_objetivo = arma_base
        elif arma_base in {"CARABINA", "ESCOPETA"}:
            tipo_base = "LARGA"
            arma_objetivo = arma_base
        elif tipo_arma_base == "LARGA":
            tipo_base = "LARGA"
            arma_objetivo = "CARABINA"
        else:
            tipo_base = "CORTA"
            arma_objetivo = "PISTOLA"

        objetivos_arma = [(tipo_base, arma_objetivo)]
        tipos_arma_excel = [tipo_base]
        armas_excel = [arma_objetivo]

    tipos_arma_objetivo = [t for t, _ in objetivos_arma]

    registro["fecha"] = fecha
    registro["hora_rango"] = hora_rango
    registro["doc_vigilante"] = doc_vigilante
    registro["fecha_programacion"] = fecha_programacion_valor
    registro["ruc"] = registro.get("ruc", "")
    registro["prioridad"] = registro.get("prioridad", "")
    registro["objetivos_arma"] = objetivos_arma
    registro["tipos_arma_objetivo"] = tipos_arma_objetivo
    registro["armas_objetivo"] = armas_excel
    registro["_excel_indices_relacionados"] = indices_relacionados

    print(" Registro tomado desde Excel:")
    print(f"   - id_registro: {registro.get('id_registro', '')}")
    print(f"   - sede: {sede}")
    print(f"   - fecha: {fecha}")
    print(f"   - hora_rango: {hora_rango}")
    print(f"   - tipo_operacion: {tipo_operacion}")
    print(f"   - doc_vigilante: {doc_vigilante}")
    print(f"   - nro_solicitud: {nro_solicitud}")
    print(f"   - fecha_programacion: {fecha_programacion_valor}")
    print(f"   - ruc: {registro.get('ruc', '')}")
    print(f"   - prioridad: {registro.get('prioridad', '')}")
    print(f"   - siguiente_mismo_doc_y_fecha: {siguiente_mismo_doc_y_fecha}")
    print(f"   - indices_relacionados_excel: {indices_relacionados}")
    print(f"   - tipo_arma (excel): {tipos_arma_excel}")
    print(f"   - arma (excel): {armas_excel}")
    print(f"   - objetivos_arma: {objetivos_arma}")
    print(f"   - tipos_arma_objetivo: {tipos_arma_objetivo}")
    return registro


def registrar_sin_cupo_en_excel(ruta_excel: str, registro: dict, observacion: str):
    """Registra observación de sin cupo en Excel sin modificar el estado actual."""
    if pd is None:
        return
    if not ruta_excel or not os.path.exists(ruta_excel):
        return

    try:
        df = pd.read_excel(ruta_excel, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        col_obs = "observaciones" if "observaciones" in df.columns else (
            "observacion" if "observacion" in df.columns else "observaciones"
        )
        if col_obs not in df.columns:
            df[col_obs] = ""

        idx = registro.get("_excel_index", None)
        indices_rel = obtener_indices_relacionados_registro(registro)
        actualizado = False
        total_actualizados = 0

        indices_validos = [i for i in indices_rel if i in df.index]
        if indices_validos:
            df.loc[indices_validos, col_obs] = observacion
            actualizado = True
            total_actualizados = len(indices_validos)
        else:
            # Fallback por coincidencia de campos claves.
            sede = str(registro.get("sede", "")).strip()
            fecha = str(registro.get("fecha", "")).strip()
            hora = str(registro.get("hora_rango", "")).strip()
            nro = str(registro.get("nro_solicitud", "")).strip()

            def col_norm(nombre_col: str):
                if nombre_col in df.columns:
                    return df[nombre_col].fillna("").astype(str).str.strip()
                return pd.Series([""] * len(df), index=df.index)

            mask = (
                (col_norm("sede") == sede) &
                (col_norm("fecha") == fecha) &
                (col_norm("hora_rango") == hora) &
                (col_norm("nro_solicitud") == nro)
            )
            idx_candidatos = df[mask].index.tolist()
            if idx_candidatos:
                df.loc[idx_candidatos, col_obs] = observacion
                actualizado = True
                total_actualizados = len(idx_candidatos)

        if actualizado:
            df.to_excel(ruta_excel, index=False)
            print(
                f"   📝 Excel actualizado: {col_obs}='{observacion}' "
                f"en {total_actualizados} fila(s)"
            )
        else:
            print(
                "   [WARNING] No se pudo ubicar el registro en Excel para actualizar observación. "
                f"_excel_index={idx}, indices_rel={indices_rel}"
            )
    except Exception as e:
        print(f"   [WARNING] No se pudo actualizar Excel con observacin de sin cupo: {e}")


def registrar_cita_programada_en_excel(ruta_excel: str, registro: dict):
    """Actualiza el estado del registro en Excel a 'Cita Programada'."""
    if pd is None:
        return
    if not ruta_excel or not os.path.exists(ruta_excel):
        return

    try:
        df = pd.read_excel(ruta_excel, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]

        if "estado" not in df.columns:
            print("   [WARNING] Columna 'estado' no encontrada en Excel")
            return

        idx = registro.get("_excel_index", None)
        indices_rel = obtener_indices_relacionados_registro(registro)
        actualizado = False
        total_actualizados = 0

        indices_validos = [i for i in indices_rel if i in df.index]
        if indices_validos:
            df.loc[indices_validos, "estado"] = "Cita Programada"
            actualizado = True
            total_actualizados = len(indices_validos)
        else:
            # Fallback por coincidencia de campos claves.
            sede = str(registro.get("sede", "")).strip()
            fecha = str(registro.get("fecha", "")).strip()
            hora = str(registro.get("hora_rango", "")).strip()
            nro = str(registro.get("nro_solicitud", "")).strip()

            def col_norm(nombre_col: str):
                if nombre_col in df.columns:
                    return df[nombre_col].fillna("").astype(str).str.strip()
                return pd.Series([""] * len(df), index=df.index)

            mask = (
                (col_norm("sede") == sede) &
                (col_norm("fecha") == fecha) &
                (col_norm("hora_rango") == hora) &
                (col_norm("nro_solicitud") == nro)
            )
            idx_candidatos = df[mask].index.tolist()
            if idx_candidatos:
                df.loc[idx_candidatos, "estado"] = "Cita Programada"
                actualizado = True
                total_actualizados = len(idx_candidatos)

        if actualizado:
            df.to_excel(ruta_excel, index=False)
            print(f"   [INFO] Excel actualizado: estado='Cita Programada' en {total_actualizados} fila(s)")
        else:
            print(
                "   [WARNING] No se pudo ubicar el registro en Excel para actualizar estado. "
                f"_excel_index={idx}, indices_rel={indices_rel}"
            )
    except Exception as e:
        print(f"   [WARNING] No se pudo actualizar Excel con estado 'Cita Programada': {e}")


def seleccionar_en_selectonemenu(page, trigger_selector: str, panel_selector: str, label_selector: str, valor: str, nombre_campo: str):
    """Selecciona una opción PrimeFaces SelectOneMenu por data-label o texto visible."""
    trigger = page.locator(trigger_selector)
    trigger.wait_for(state="visible", timeout=12000)
    trigger.click()

    panel = page.locator(panel_selector)
    panel.wait_for(state="visible", timeout=7000)

    if str(nombre_campo or "").strip().lower() == "fecha":
        items = panel.locator("li.ui-selectonemenu-item")
        try:
            items.first.wait_for(state="visible", timeout=5000)
        except Exception as e:
            raise FechaNoDisponibleError(
                f"No hay opciones visibles en el combo de Fecha para '{valor}'."
            ) from e

        total = items.count()
        opciones_disponibles = []
        opcion_objetivo = None
        valor_norm = str(valor or "").strip().upper()
        for i in range(total):
            txt = (items.nth(i).inner_text() or "").strip()
            if not txt:
                continue
            opciones_disponibles.append(txt)
            if txt.upper() == valor_norm:
                opcion_objetivo = items.nth(i)

        if opcion_objetivo is None:
            raise FechaNoDisponibleError(
                f"Fecha '{valor}' no disponible en combo. Opciones actuales: {opciones_disponibles}"
            )

        opcion_objetivo.click()
        page.wait_for_timeout(250)

        texto_label = page.locator(label_selector).inner_text().strip()
        if texto_label.upper() != valor_norm:
            raise Exception(
                f"No se confirmó la selección de {nombre_campo}. Esperado: '{valor}' | Actual: '{texto_label}'"
            )
        print(f"   [INFO] {nombre_campo} seleccionado: {texto_label}")
        return

    opcion = panel.locator(f'li.ui-selectonemenu-item[data-label="{valor}"]')
    try:
        opcion.wait_for(state="visible", timeout=2000)
    except PlaywrightTimeoutError:
        opcion = panel.locator("li.ui-selectonemenu-item").filter(has_text=valor)
        opcion.wait_for(state="visible", timeout=5000)

    opcion.first.click()
    page.wait_for_timeout(250)

    texto_label = page.locator(label_selector).inner_text().strip()
    if texto_label.upper() != valor.upper():
        raise Exception(
            f"No se confirmó la selección de {nombre_campo}. Esperado: '{valor}' | Actual: '{texto_label}'"
        )
    print(f"   [INFO] {nombre_campo} seleccionado: {texto_label}")


# ============================================================
# NAVEGACIÓN: CITAS -> RESERVAS DE CITAS
# ============================================================

def navegar_reservas_citas(page):
    """
    El menú de SUCAMEC es un PrimeFaces PanelMenu (acordeón).
    NO usa hover — se expande haciendo CLIC en el <h3> header.

    Flujo:
      1. Clic en el <h3> de "CITAS" para expandir el panel.
      2. Esperar a que el panel interno sea visible (display:block).
      3. Clic en el <a> de "RESERVAS DE CITAS" (dispara el submit JSF).
      4. Esperar a que la nueva vista cargue.
    """
    print("\n Navegando a CITAS -> RESERVAS DE CITAS...")

    # 1. Esperar carga base
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except Exception:
        pass

    def vista_reservas_lista(timeout_ms: int = 3500) -> bool:
        """Confirma que ya estamos en la vista donde aparece el combo 'Cita para'."""
        try:
            page.locator("form#gestionCitasForm").wait_for(state="visible", timeout=timeout_ms)
            page.locator(SEL["tipo_cita_trigger"]).wait_for(state="visible", timeout=timeout_ms)
            return True
        except Exception:
            return False

    # FAST PATH: clic directo al item menuid=7_1 dentro del panel lateral j_idt10.
    # Es más rápido porque evita expandir manualmente el acordeón CITAS.
    url_antes = page.url
    try:
        page.locator("#j_idt10").wait_for(state="visible", timeout=4000)
        click_directo = page.evaluate(
            '''() => {
                const link = document.querySelector('#j_idt10 a[onclick*="7_1"][onclick*="menuPrincipal"]');
                if (!link) return false;
                link.click();
                return true;
            }'''
        )
        if click_directo:
            print("    Fast-path: click directo en 'RESERVAS DE CITAS' (menuid 7_1)")
            try:
                page.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pass
            if ("GestionCitas.xhtml" in page.url) or (page.url != url_antes) or vista_reservas_lista(5000):
                print(f"[INFO] Navegacin completada (fast-path) -> URL: {page.url}")
                return
            print("   [WARNING] Fast-path no confirm navegacin -> usando flujo estndar")
    except Exception:
        pass

    # ── PASO 1: Clic en el header "CITAS" del PanelMenu ──────────────────────
    # El header es el <h3> que contiene <a href="#" tabindex="-1">CITAS</a>
    # Usamos el <a> interno como punto de clic (más preciso).
    header_citas = page.locator(
        '#j_idt11\\:menuPrincipal .ui-panelmenu-header a[tabindex="-1"]'
    ).filter(has_text="CITAS")

    try:
        header_citas.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeoutError:
        raise Exception("No se encontró el header 'CITAS' en el PanelMenu")

    header_citas.click()
    print("   [INFO] Clic en header 'CITAS' -> expandiendo panel...")

    # ── PASO 2: Esperar a que el panel de CITAS sea visible ──────────────────
    # El panel tiene id fijo: j_idt11:menuPrincipal_7
    # PrimeFaces lo muestra quitando la clase ui-helper-hidden y poniendo display:block
    panel_citas = page.locator('#j_idt11\\:menuPrincipal_7')
    try:
        # Esperar a que el panel sea visible (PrimeFaces hace toggle de display)
        panel_citas.wait_for(state="visible", timeout=2500)
        print("   [INFO] Panel CITAS desplegado")
    except PlaywrightTimeoutError:
        # En algunas versiones de PF el panel ya está en el DOM pero con display:none
        # Forzamos visibilidad vía JS como fallback
        print("   [WARNING] Panel no visible por Playwright -> forzando visibilidad va JS")
        page.evaluate("""
            const panel = document.getElementById('j_idt11:menuPrincipal_7');
            if (panel) {
                panel.classList.remove('ui-helper-hidden');
                panel.style.display = 'block';
            }
        """)
        page.wait_for_timeout(180)

    # ── PASO 3: Clic en "RESERVAS DE CITAS" ──────────────────────────────────
    # Buscamos el <a> que contiene el span con texto "RESERVAS DE CITAS"
    # dentro del panel de CITAS ya desplegado.
    reservas_link = panel_citas.locator(
        'a.ui-menuitem-link:has(span.ui-menuitem-text:text-is("RESERVAS DE CITAS"))'
    )
    try:
        reservas_link.wait_for(state="visible", timeout=2500)
    except PlaywrightTimeoutError:
        # Fallback: buscar directamente por el onclick con menuid 7_1
        print("   [WARNING] Link no visible -> usando fallback por menuid 7_1")
        reservas_link = page.locator(
            'a[onclick*="7_1"][onclick*="menuPrincipal"]'
        )
        reservas_link.wait_for(state="visible", timeout=3000)

    reservas_link.click()
    print("   [INFO] Clic en 'RESERVAS DE CITAS'")

    # ── PASO 4: Esperar a que la nueva vista cargue ───────────────────────────
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass

    if not vista_reservas_lista(6000):
        raise Exception("No se confirmó la vista de 'Reservas de Citas' tras la navegación")

    print(f"[INFO] Navegacin completada -> URL: {page.url}")


def seleccionar_tipo_cita_poligono(page):
    """
    En la vista de Gestión de Citas, abre el SelectOneMenu de tipo de cita
    y selecciona la opción "EXAMEN PARA POLÍGONO DE TIRO".
    """
    print("\n Seleccionando tipo de cita: EXAMEN PARA POLGONO DE TIRO...")

    # Esperar que la vista de gestión esté lista
    page.locator("form#gestionCitasForm").wait_for(state="visible", timeout=12000)

    # 1) Abrir el combo (trigger)
    trigger = page.locator(SEL["tipo_cita_trigger"])
    try:
        trigger.wait_for(state="visible", timeout=6000)
        trigger.click()
    except PlaywrightTimeoutError:
        # Fallback: clic en el label del select para abrir panel
        print("   [WARNING] Trigger no visible -> usando fallback sobre label")
        label = page.locator(SEL["tipo_cita_label"])
        label.wait_for(state="visible", timeout=6000)
        label.click()

    # 2) Esperar panel de opciones
    panel = page.locator(SEL["tipo_cita_panel"])
    panel.wait_for(state="visible", timeout=6000)

    # 3) Seleccionar opción de polígono
    opcion = page.locator(SEL["tipo_cita_opcion_poligono"])
    try:
        opcion.wait_for(state="visible", timeout=4000)
    except PlaywrightTimeoutError:
        print("   [WARNING] Opcin por data-label no visible -> buscando por texto")
        opcion = panel.locator("li.ui-selectonemenu-item").filter(has_text="EXAMEN PARA POLÍGONO DE TIRO")
        opcion.wait_for(state="visible", timeout=4000)

    opcion.click()

    # 4) Validar que el label del combo refleje la selección
    label = page.locator(SEL["tipo_cita_label"])
    page.wait_for_timeout(250)
    texto_label = label.inner_text().strip().upper()
    if "POLÍGONO DE TIRO" not in texto_label and "POLIGONO DE TIRO" not in texto_label:
        raise Exception(f"No se confirmó la selección en el combo. Label actual: '{texto_label}'")

    print(f"   [INFO] Tipo de cita seleccionado: {texto_label}")


def seleccionar_sede_y_fecha_desde_registro(page, registro: dict):
    """
    En Reserva de Cupos, selecciona Sede y Fecha según el registro del Excel.
    """
    sede = registro["sede"].strip()
    fecha = registro["fecha"].strip()

    print("\n Completando Reserva de Cupos con datos del Excel...")
    page.locator(SEL["reserva_form"]).wait_for(state="visible", timeout=15000)

    seleccionar_en_selectonemenu(
        page,
        trigger_selector=SEL["sede_trigger"],
        panel_selector=SEL["sede_panel"],
        label_selector=SEL["sede_label"],
        valor=sede,
        nombre_campo="Sede"
    )

    # Al cambiar sede, PrimeFaces suele refrescar opciones de fecha por AJAX.
    page.wait_for_timeout(700)

    seleccionar_en_selectonemenu(
        page,
        trigger_selector=SEL["fecha_trigger"],
        panel_selector=SEL["fecha_panel"],
        label_selector=SEL["fecha_label"],
        valor=fecha,
        nombre_campo="Fecha"
    )


def seleccionar_hora_con_cupo_y_avanzar(page, registro: dict):
    """
    Busca la hora del Excel en la tabla de cupos, valida cupos > 0,
    selecciona el radiobutton de la fila y presiona 'Siguiente'.
    """
    hora_objetivo = normalizar_hora_rango(registro.get("hora_rango", ""))
    if not hora_objetivo:
        raise Exception("El registro no tiene 'hora_rango' válido")

    print(f"\n Buscando hora en tabla: {hora_objetivo}")

    tabla = page.locator(SEL["tabla_programacion"])
    tabla.wait_for(state="visible", timeout=15000)

    filas = page.locator(SEL["tabla_programacion_rows"])
    total_filas = filas.count()
    if total_filas == 0:
        # Fallback para tablas PrimeFaces donde el sufijo _data no aparece en todos los entornos.
        filas = page.locator(f"{SEL['tabla_programacion']} tbody tr")
        total_filas = filas.count()
    if total_filas == 0:
        raise Exception("La tabla de programación no tiene filas para la fecha/sede seleccionadas")

    fila_objetivo = None
    cupos_objetivo = 0
    resumen = []
    horas_descartadas = {
        normalizar_hora_rango(x)
        for x in (registro.get("_horas_descartadas", []) or [])
        if normalizar_hora_rango(x)
    }
    usar_hora_adaptativa = _hora_adaptativa_habilitada()

    def extraer_hora_rango_desde_texto(texto: str) -> str:
        t = str(texto or "").replace(".", ":")
        m = re.search(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", t)
        if m:
            ini = normalizar_hora_fragmento(m.group(1))
            fin = normalizar_hora_fragmento(m.group(2))
            return f"{ini}-{fin}"
        return normalizar_hora_rango(t)

    def extraer_cupos_desde_celdas(textos_celdas: list) -> int:
        # Busca el último texto numérico que no sea rango horario.
        for txt in reversed(textos_celdas):
            t = str(txt or "").strip()
            if not t or ":" in t:
                continue
            if re.search(r"\d+", t):
                return convertir_a_entero(t)
        return 0

    def click_boton_limpiar_obligatorio():
        try:
            boton_limpiar = page.locator(SEL["boton_limpiar"])
            boton_limpiar.wait_for(state="visible", timeout=7000)
            boton_limpiar.first.click(timeout=7000)
            page.wait_for_timeout(350)
            print("   [INFO] Click en botn 'Limpiar' por falta de cupos")
        except Exception as e:
            raise SinCupoError(f"No se pudo accionar el botón 'Limpiar' tras detectar cupo 0: {e}")

    slots = []
    for i in range(total_filas):
        fila = filas.nth(i)
        celdas = fila.locator("td")
        total_celdas = celdas.count()
        if total_celdas == 0:
            continue

        textos_celdas = []
        for j in range(total_celdas):
            try:
                textos_celdas.append((celdas.nth(j).inner_text() or "").strip())
            except Exception:
                textos_celdas.append("")

        hora_tabla = ""
        for txt in textos_celdas:
            cand = extraer_hora_rango_desde_texto(txt)
            if cand and "-" in cand and re.search(r"\d{2}:\d{2}-\d{2}:\d{2}", cand):
                hora_tabla = cand
                break

        cupos = extraer_cupos_desde_celdas(textos_celdas)
        if hora_tabla:
            resumen.append(f"{hora_tabla} ({cupos})")
            slots.append({
                "hora": hora_tabla,
                "cupos": cupos,
                "fila": fila,
                "orden": i,
                "rango": _parsear_rango_hora_a_minutos(hora_tabla),
            })

    for slot in slots:
        if slot["hora"] == hora_objetivo:
            fila_objetivo = slot["fila"]
            cupos_objetivo = slot["cupos"]
            break

    if fila_objetivo is None:
        raise Exception(
            "No se encontró la hora objetivo en la tabla. "
            f"Objetivo: '{hora_objetivo}' | Disponibles: {', '.join(resumen)}"
        )

    # Regla de negocio: siempre respetar primero la hora exacta del Excel.
    # Solo aplicar fallback adaptativo cuando la hora objetivo no tenga cupos.
    if cupos_objetivo > 0:
        print("   [INFO] Estrategia horario: prioridad a hora exacta del Excel")
    elif usar_hora_adaptativa and slots:
        slots_ordenados = sorted(
            slots,
            key=lambda s: (
                s["rango"][0] if s["rango"] else 9999,
                s["orden"],
            ),
        )
        slot_objetivo = next((s for s in slots_ordenados if s["hora"] == hora_objetivo), None)

        candidatos = []
        bloque_mediodia = [
            "11:45-12:00",
            "12:00-12:15",
            "12:15-12:30",
            "12:30-12:45",
            "12:45-13:00",
        ]

        if hora_objetivo in bloque_mediodia and _hora_adaptativa_bloque_mediodia_completo():
            candidatos = [s for s in slots_ordenados if s["hora"] in bloque_mediodia]
            print("   [INFO] Estrategia horario: bloque completo de mediodía (11:45-13:00)")
        else:
            idx_obj = next((i for i, s in enumerate(slots_ordenados) if s["hora"] == hora_objetivo), -1)
            if idx_obj >= 0:
                inferior = slots_ordenados[idx_obj - 1] if idx_obj - 1 >= 0 else None
                superior = slots_ordenados[idx_obj + 1] if idx_obj + 1 < len(slots_ordenados) else None
                if inferior and superior:
                    candidatos = [inferior, superior]
                elif inferior and slot_objetivo:
                    candidatos = [inferior, slot_objetivo]
                elif superior and slot_objetivo:
                    candidatos = [slot_objetivo, superior]
                elif slot_objetivo:
                    candidatos = [slot_objetivo]

            if not candidatos and slot_objetivo:
                prev_hora = _rango_desplazado_15m(hora_objetivo, -1)
                next_hora = _rango_desplazado_15m(hora_objetivo, 1)
                candidatos = [
                    s for s in slots_ordenados
                    if s["hora"] in {prev_hora, next_hora, hora_objetivo}
                ]
            print("   [INFO] Estrategia horario: vecinos inmediatos (inferior/superior)")

        if not candidatos and slot_objetivo:
            candidatos = [slot_objetivo]

        if horas_descartadas:
            candidatos_filtrados = [s for s in candidatos if s["hora"] not in horas_descartadas]
            if candidatos_filtrados:
                candidatos = candidatos_filtrados

        candidatos_disponibles = [s for s in candidatos if s["cupos"] > 0]

        if candidatos_disponibles:
            # Desempate al "extremo superior": para igual cupo, preferir el slot más tarde.
            seleccionado = max(
                candidatos_disponibles,
                key=lambda s: (
                    s["cupos"],
                    s["rango"][0] if s["rango"] else s["orden"],
                ),
            )
            fila_objetivo = seleccionado["fila"]
            cupos_objetivo = seleccionado["cupos"]
            if seleccionado["hora"] != hora_objetivo:
                print(
                    f"   [INFO] Reasignación adaptativa de hora: "
                    f"{hora_objetivo} -> {seleccionado['hora']} (Cupos={cupos_objetivo})"
                )
                registro["hora_rango"] = seleccionado["hora"]
            hora_objetivo = seleccionado["hora"]
        else:
            opciones_dbg = ", ".join([f"{s['hora']}({s['cupos']})" for s in candidatos])
            click_boton_limpiar_obligatorio()
            raise SinCupoError(
                "No hay cupos en horarios candidatos. "
                f"Objetivo: {hora_objetivo} | Candidatos: {opciones_dbg}"
            )

    if cupos_objetivo <= 0:
        click_boton_limpiar_obligatorio()
        raise SinCupoError(f"La hora '{hora_objetivo}' no tiene cupos disponibles (Cupos Libres={cupos_objetivo})")

    radio_box = fila_objetivo.locator("td.ui-selection-column div.ui-radiobutton-box")
    if radio_box.count() == 0:
        raise Exception("No se encontró radiobutton en la fila de la hora objetivo")

    radio_box.first.click()
    page.wait_for_timeout(250)

    clase_radio = (radio_box.first.get_attribute("class") or "")
    aria_fila = (fila_objetivo.get_attribute("aria-selected") or "").lower()
    if "ui-state-active" not in clase_radio and aria_fila != "true":
        raise Exception("No se confirmó la selección del radiobutton de la hora")

    registro["_hora_seleccionada_actual"] = hora_objetivo
    print(f"   [INFO] Hora seleccionada: {hora_objetivo} (Cupos Libres={cupos_objetivo})")

    boton_siguiente = page.locator(SEL["boton_siguiente"])
    boton_siguiente.wait_for(state="visible", timeout=7000)
    boton_siguiente.click()
    print("   [INFO] Click en botn 'Siguiente'")


def seleccionar_opcion_flexible_en_panel(page, panel_selector: str, texto_objetivo: str, nombre_campo: str):
    """Selecciona un li dentro de un panel PrimeFaces por coincidencia flexible de texto."""
    panel = page.locator(panel_selector)
    panel.wait_for(state="visible", timeout=7000)

    items = panel.locator("li.ui-selectonemenu-item")
    total = items.count()
    if total == 0:
        raise Exception(f"No hay opciones disponibles en {nombre_campo}")

    objetivo_norm = normalizar_texto_comparable(texto_objetivo)
    for i in range(total):
        item = items.nth(i)
        label = (item.get_attribute("data-label") or item.inner_text() or "").strip()
        label_norm = normalizar_texto_comparable(label)
        if objetivo_norm == label_norm or objetivo_norm in label_norm or label_norm in objetivo_norm:
            item.click()
            return label

    opciones = []
    for i in range(total):
        item = items.nth(i)
        opciones.append((item.get_attribute("data-label") or item.inner_text() or "").strip())
    raise Exception(
        f"No se encontró coincidencia para {nombre_campo}. "
        f"Objetivo: '{texto_objetivo}' | Opciones: {opciones}"
    )


def completar_paso_2_desde_registro(page, registro: dict):
    """
    Paso 2: tipo operación, doc. vigilante (autocomplete), seleccionar SI,
    y elegir número de solicitud por coincidencia con nro_solicitud del Excel.
    """
    tipo_operacion = registro.get("tipo_operacion", "").strip()
    doc_vigilante = registro.get("doc_vigilante", "").strip()
    nro_solicitud_excel = registro.get("nro_solicitud", "").strip()
    token_solicitud = extraer_token_solicitud(nro_solicitud_excel)

    print("\n Completando Paso 2 con datos del Excel...")

    # 2.1 Tipo de operación
    page.locator(SEL["tipo_operacion_trigger"]).wait_for(state="visible", timeout=12000)
    page.locator(SEL["tipo_operacion_trigger"]).click()
    page.locator(SEL["tipo_operacion_panel"]).wait_for(state="visible", timeout=7000)

    opcion_tipo = None
    items_tipo = page.locator(SEL["tipo_operacion_items"])
    total_tipo = items_tipo.count()
    objetivo_tipo = normalizar_texto_comparable(tipo_operacion)
    for i in range(total_tipo):
        item = items_tipo.nth(i)
        label = (item.get_attribute("data-label") or item.inner_text() or "").strip()
        label_norm = normalizar_texto_comparable(label)
        if objetivo_tipo == label_norm or objetivo_tipo in label_norm or label_norm in objetivo_tipo:
            item.click()
            opcion_tipo = label
            break
    if not opcion_tipo:
        raise Exception(f"No se encontró Tipo Operación '{tipo_operacion}' en el combo")

    page.wait_for_timeout(250)
    label_tipo = page.locator(SEL["tipo_operacion_label"]).inner_text().strip()
    if not label_tipo or label_tipo == "---":
        raise Exception("No se confirmó la selección de Tipo Operación")
    print(f"   [INFO] Tipo Operacin seleccionado: {opcion_tipo}")

    es_inicial = (
        "INICIAL" in normalizar_texto_comparable(label_tipo)
        or "INICIAL" in normalizar_texto_comparable(tipo_operacion)
    )

    def seleccionar_doc_vigilante_autocomplete():
        doc_input = page.locator(SEL["doc_vig_input"])
        doc_input.wait_for(state="visible", timeout=12000)
        doc_input.click()
        doc_input.fill("")
        doc_input.type(doc_vigilante, delay=20)

        panel_doc = page.locator(SEL["doc_vig_panel"])
        items_doc = page.locator(SEL["doc_vig_items"])

        elegido = False
        try:
            panel_doc.wait_for(state="visible", timeout=2500)
        except PlaywrightTimeoutError:
            # Fallback: algunos autocompletes solo abren panel si se navega por teclado.
            doc_input.press("ArrowDown")
            page.wait_for_timeout(350)

        if panel_doc.is_visible():
            try:
                items_doc.first.wait_for(state="visible", timeout=2500)
            except PlaywrightTimeoutError:
                page.wait_for_timeout(700)

            total_doc = items_doc.count()
            for i in range(total_doc):
                item = items_doc.nth(i)
                data_label = (item.get_attribute("data-item-label") or "").strip()
                data_value = (item.get_attribute("data-item-value") or "").strip()
                texto_item = item.inner_text().strip()
                if doc_vigilante in data_label or doc_vigilante in data_value or doc_vigilante in texto_item:
                    item.click()
                    elegido = True
                    break

            if not elegido and total_doc > 0:
                items_doc.first.click()
                elegido = True

        if not elegido:
            # Fallback final: forzar blur/change por si el valor exacto ya es aceptado por JSF.
            doc_input.evaluate(
                'el => { el.dispatchEvent(new Event("input", {bubbles:true})); el.dispatchEvent(new Event("change", {bubbles:true})); el.blur(); }'
            )

        page.wait_for_timeout(300)
        valor_doc = doc_input.input_value().strip()
        if doc_vigilante not in valor_doc:
            raise Exception(f"No se confirmó el documento vigilante. Esperado contiene '{doc_vigilante}' | Actual '{valor_doc}'")
        print(f"   [INFO] Documento vigilante seleccionado: {valor_doc}")

    # 2.2 Flujo especial solo para INICIAL DE LICENCIA DE USO:
    # Tipo de Licencia -> Documento Vigilante.
    if es_inicial:
        print("    Flujo INICIAL detectado: primero Tipo de Licencia, luego Documento Vigilante")

        trigger_tramite = page.locator(SEL["tipo_tramite_trigger"])
        label_tramite = page.locator(SEL["tipo_tramite_label"])

        # Tras elegir Tipo Operación, JSF puede tardar en habilitar tipoTramite.
        habilitado = False
        for _ in range(8):
            try:
                trigger_tramite.wait_for(state="visible", timeout=2000)
                label_tramite.wait_for(state="visible", timeout=2000)
                habilitado = True
                break
            except Exception:
                page.wait_for_timeout(400)

        if not habilitado:
            raise Exception("No apareció el desplegable 'Tipo de Licencia' para flujo INICIAL")

        trigger_tramite.click()
        page.locator(SEL["tipo_tramite_panel"]).wait_for(state="visible", timeout=7000)

        opcion_tramite = page.locator(SEL["tipo_tramite_seg_priv"])
        try:
            opcion_tramite.wait_for(state="visible", timeout=2500)
            opcion_tramite.first.click()
        except PlaywrightTimeoutError:
            seleccionar_opcion_flexible_en_panel(
                page,
                panel_selector=SEL["tipo_tramite_panel"],
                texto_objetivo="SEGURIDAD PRIVADA",
                nombre_campo="Tipo de Licencia"
            )

        page.wait_for_timeout(350)
        texto_tramite = page.locator(SEL["tipo_tramite_label"]).inner_text().strip()
        if normalizar_texto_comparable(texto_tramite) != "SEGURIDAD PRIVADA":
            raise Exception(f"No se confirmó Tipo de Licencia = SEGURIDAD PRIVADA. Actual: '{texto_tramite}'")
        print("   [INFO] Tipo de Licencia: SEGURIDAD PRIVADA")

        # Con Tipo de Licencia ya seteado, recien se habilita/visibiliza el DNI.
        seleccionar_doc_vigilante_autocomplete()
    else:
        # Flujo RENOVACION (u otros): Documento Vigilante directo.
        seleccionar_doc_vigilante_autocomplete()

    # 2.3 Seleccione Solicitud -> SI (siempre)
    page.locator(SEL["seleccione_solicitud_trigger"]).wait_for(state="visible", timeout=12000)
    page.locator(SEL["seleccione_solicitud_trigger"]).click()
    page.locator(SEL["seleccione_solicitud_panel"]).wait_for(state="visible", timeout=7000)
    page.locator(SEL["seleccione_solicitud_si"]).first.click()
    page.wait_for_timeout(350)
    label_si = page.locator(SEL["seleccione_solicitud_label"]).inner_text().strip().upper()
    if label_si.replace(" ", "") != "SI":
        raise Exception(f"No se confirmó Seleccione Solicitud = SI. Actual: '{label_si}'")
    print("   [INFO] Seleccione Solicitud: SI")

    if es_inicial:
        print("    Flujo INICIAL: tambin se seleccionar Nro Solicitud")

    # 2.4 Nro Solicitud por coincidencia parcial (ej. 90086)
    if not token_solicitud:
        raise Exception(f"No se pudo extraer token numérico de nro_solicitud: '{nro_solicitud_excel}'")

    page.locator(SEL["nro_solicitud_trigger"]).wait_for(state="visible", timeout=12000)
    page.locator(SEL["nro_solicitud_trigger"]).click()

    panel_nro = page.locator(SEL["nro_solicitud_panel"])
    panel_nro.wait_for(state="visible", timeout=7000)
    items_nro = page.locator(SEL["nro_solicitud_items"])
    total_nro = items_nro.count()
    if total_nro == 0:
        raise Exception("No hay opciones en el combo de Nro Solicitud")

    seleccionado_label = None
    for i in range(total_nro):
        item = items_nro.nth(i)
        label = (item.get_attribute("data-label") or item.inner_text() or "").strip()
        # Comparamos contra todos los bloques numéricos del label para encontrar el Nro Empoce
        bloques = re.findall(r"\d+", label)
        bloques_norm = [b.lstrip("0") or "0" for b in bloques]
        if token_solicitud in bloques_norm:
            item.click()
            seleccionado_label = label
            break

    if not seleccionado_label:
        disponibles = []
        for i in range(total_nro):
            item = items_nro.nth(i)
            disponibles.append((item.get_attribute("data-label") or item.inner_text() or "").strip())
        raise Exception(
            f"No se encontró Nro Solicitud con token '{token_solicitud}'. Opciones: {disponibles}"
        )

    page.wait_for_timeout(300)
    label_nro = page.locator(SEL["nro_solicitud_label"]).inner_text().strip()
    bloques_final = [b.lstrip("0") or "0" for b in re.findall(r"\d+", label_nro)]
    if token_solicitud not in bloques_final:
        raise Exception(
            f"No se confirmó Nro Solicitud. Esperado token '{token_solicitud}' | Actual '{label_nro}'"
        )
    print(f"   [INFO] Nro Solicitud seleccionado: {label_nro}")


def completar_tabla_tipos_arma_y_avanzar(page, registro: dict):
    """
    En Fase 2 completa la tabla dtTipoLic según tipo_arma del Excel y
    pulsa 'Siguiente' (botonSiguiente3).

    Reglas:
      - Si hay más de un registro del mismo usuario+fecha, se infiere misma programación
        y se aplican todos los tipos/armas encontrados.
      - Si hay solo un registro, se aplica solo ese.
    """
    print("\n Completando tabla de tipos de arma (Fase 2)...")

    objetivos_excel = registro.get("objetivos_arma", []) or []
    objetivos = []
    for item in objetivos_excel:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            tipo_fila = normalizar_tipo_arma_excel(item[0])
            arma_objetivo = normalizar_tipo_arma_excel(item[1])
            if tipo_fila and arma_objetivo and (tipo_fila, arma_objetivo) not in objetivos:
                objetivos.append((tipo_fila, arma_objetivo))

    if not objetivos:
        raise Exception("No se recibieron objetivos de arma válidos desde Excel (tipo_arma + arma)")

    # PrimeFaces puede renderizar filas sin el sufijo _data y en modo editable por celda.
    filas = page.locator('#tabGestion\\:creaCitaPolJurForm\\:dtTipoLic tbody tr')
    try:
        filas.first.wait_for(state="visible", timeout=9000)
    except PlaywrightTimeoutError:
        filas = page.locator('table[id^="tabGestion:creaCitaPolJurForm:dtTipoLic"] tbody tr')
        try:
            filas.first.wait_for(state="visible", timeout=4000)
        except PlaywrightTimeoutError:
            raise Exception("No se encontró la tabla de tipos de arma (dtTipoLic)")

    total_filas = filas.count()
    if total_filas == 0:
        raise Exception("La tabla dtTipoLic no tiene filas")

    aplicados = []
    for tipo_fila, arma_objetivo in objetivos:
        fila_match = None
        for i in range(total_filas):
            fila = filas.nth(i)
            celdas = fila.locator('td[role="gridcell"]')
            if celdas.count() == 0:
                celdas = fila.locator("td")

            textos = []
            for j in range(celdas.count()):
                texto_celda = normalizar_texto_comparable(celdas.nth(j).inner_text().strip())
                if texto_celda:
                    textos.append(texto_celda)

            tipo_texto = " ".join(textos)
            if tipo_fila in tipo_texto:
                fila_match = fila
                break

        if fila_match is None:
            raise Exception(f"No se encontró fila para tipo de arma '{tipo_fila}' en dtTipoLic")

        # La columna "Arma" es editable; activamos la celda para mostrar el select.
        celdas_editables = fila_match.locator("td.ui-editable-column")
        if celdas_editables.count() > 0:
            celdas_editables.last.click()
            page.wait_for_timeout(180)

        combo = fila_match.locator("select")
        if combo.count() == 0:
            raise Exception(f"No se encontró combo de Arma para tipo '{tipo_fila}'")

        combo.first.wait_for(state="visible", timeout=3500)
        combo.first.select_option(label=arma_objetivo)
        page.wait_for_timeout(350)

        try:
            page.wait_for_load_state("networkidle", timeout=3500)
        except Exception:
            pass

        seleccionado = combo.first.evaluate(
            "el => el.options[el.selectedIndex] ? el.options[el.selectedIndex].text.trim() : ''"
        )
        if normalizar_texto_comparable(seleccionado) != normalizar_texto_comparable(arma_objetivo):
            raise Exception(
                f"No se confirmó Arma para '{tipo_fila}'. Esperado '{arma_objetivo}' | Actual '{seleccionado}'"
            )

        aplicados.append(f"{tipo_fila} -> {seleccionado}")
        print(f"   [INFO] {tipo_fila}: {seleccionado}")

    if not aplicados:
        raise Exception("No se aplicó ninguna selección de arma en dtTipoLic")

    boton_siguiente_3 = page.locator('#tabGestion\\:creaCitaPolJurForm\\:botonSiguiente3')
    boton_siguiente_3.wait_for(state="visible", timeout=8000)
    boton_siguiente_3.click()
    print("   [INFO] Click en botn 'Siguiente' de Fase 2 (botonSiguiente3)")

    # Espera robusta del resultado de transición: Fase 3 o turno duplicado.
    esperar_transicion_a_fase3_o_turno_duplicado(page, timeout_ms=12000)


def _safe_int_env(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default)) or default).strip())
    except Exception:
        return default


def _as_bool_env(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or ("1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "si", "sí", "on"}


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


def _multihilo_scheduled_habilitado() -> bool:
    run_mode = os.getenv("RUN_MODE", "manual").strip().lower()
    if run_mode != "scheduled":
        return False
    if _as_bool_env("MULTIWORKER_CHILD", default=False):
        return False
    if _as_bool_env("PERSISTENT_SESSION", default=False):
        return False
    return _as_bool_env("SCHEDULED_MULTIWORKER", default=True)


def _ejecutar_scheduled_multihilo_orquestador():
    """
    Orquesta el modo scheduled multihilo ejecutando el flujo existente en procesos aislados.
    No altera la lógica de negocio del flujo por registro: cada worker invoca run_pipeline.py.
    """
    if pd is None:
        raise Exception("pandas no está disponible para preparar lotes multihilo")

    workers = max(1, min(4, _safe_int_env("SCHEDULED_WORKERS", 4)))
    max_units = _safe_int_env("SCHEDULED_MAX_UNITS", 0)
    worker_mode = str(os.getenv("SCHEDULED_WORKER_MODE", "sticky") or "sticky").strip().lower()
    if worker_mode not in {"dynamic", "sticky"}:
        worker_mode = "sticky"

    screen_w_eff, screen_h_eff = _detect_windows_screen_size()
    origen_excel = EXCEL_PATH
    if not os.path.exists(origen_excel):
        raise Exception(f"Excel no encontrado para multihilo: {origen_excel}")

    print(f"[INFO] SCHEDULED_MULTIWORKER activado | workers={workers} | mode={worker_mode}")
    print(f"[INFO] Excel origen multihilo: {origen_excel}")

    trabajos = obtener_trabajos_pendientes_excel(origen_excel)
    if not trabajos:
        print("[INFO] No hay trabajos pendientes para multihilo.")
        return

    unidades = []
    vistos_primarios = set()
    for t in trabajos:
        idx = int(t["idx_excel"])
        if idx in vistos_primarios:
            continue
        reg = cargar_primer_registro_pendiente_desde_excel(origen_excel, indice_excel_objetivo=idx)
        rel = sorted(set(int(x) for x in (reg.get("_excel_indices_relacionados", []) or [idx])))
        unidades.append(
            {
                "idx_principal": idx,
                "indices_relacionados": rel,
            }
        )
        vistos_primarios.add(idx)

    if max_units > 0:
        unidades = unidades[:max_units]

    if not unidades:
        print("[INFO] No se construyeron unidades multihilo.")
        return

    print(f"[INFO] Unidades multihilo a procesar: {len(unidades)}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_root = os.path.join(project_root, "logs", f".tmp_multihilo_flow_{stamp}")
    os.makedirs(temp_root, exist_ok=True)

    lock_results = threading.Lock()
    results = []

    base_cmd = [sys.executable, os.path.join(project_root, "run_pipeline.py"), "--mode", "scheduled"]

    def _make_worker_excel(worker_id: int, target_indices: set, tag: str) -> str:
        safe_tag = str(tag).replace(" ", "_")
        dst = os.path.join(temp_root, f"worker_{worker_id}_{safe_tag}.xlsx")
        shutil.copy2(origen_excel, dst)

        df = pd.read_excel(dst, dtype=str)
        df.columns = [str(c).strip() for c in df.columns]
        if "estado" not in df.columns:
            df["estado"] = ""
        for col in df.columns:
            df[col] = df[col].fillna("").astype(str)

        for i in df.index:
            if int(i) in target_indices:
                df.at[i, "estado"] = "PENDIENTE"
            else:
                df.at[i, "estado"] = "NO_EJECUTAR_TEST"

        df.to_excel(dst, index=False)
        return dst

    def _build_worker_env(worker_id: int, excel_worker: str, mode: str = "sticky") -> dict:
        env = os.environ.copy()
        env["EXCEL_PATH"] = excel_worker
        env["RUN_MODE"] = "scheduled"
        env["HOLD_BROWSER_OPEN"] = "0"
        env["MULTIWORKER_CHILD"] = "1"

        env["LOG_DIR"] = os.path.join(temp_root, f"logs_w{worker_id}")
        env["BROWSER_TILE_ENABLE"] = "1"
        env["BROWSER_TILE_TOTAL"] = str(workers)
        env["BROWSER_TILE_INDEX"] = str(worker_id - 1)
        env["BROWSER_TILE_SCREEN_W"] = str(_safe_int_env("BROWSER_TILE_SCREEN_W", screen_w_eff))
        env["BROWSER_TILE_SCREEN_H"] = str(_safe_int_env("BROWSER_TILE_SCREEN_H", screen_h_eff))
        env["BROWSER_TILE_TOP_OFFSET"] = str(_safe_int_env("BROWSER_TILE_TOP_OFFSET", 0))
        env["BROWSER_TILE_GAP"] = str(_safe_int_env("BROWSER_TILE_GAP", 6))
        env["BROWSER_TILE_FRAME_PAD"] = str(_safe_int_env("BROWSER_TILE_FRAME_PAD", 2))

        env["ADAPTIVE_HOUR_SELECTION"] = "1"
        env["ADAPTIVE_HOUR_NOON_FULL_BLOCK"] = "1"
        env["NRO_SOLICITUD_CONFIRM_ATTEMPTS"] = str(_safe_int_env("NRO_SOLICITUD_CONFIRM_ATTEMPTS", 2))

        if mode == "sticky":
            env["PERSISTENT_SESSION"] = "1"
        return env

    def _run_unit(worker_id: int, idx_label: str, excel_worker: str, mode: str = "sticky") -> int:
        started = time.time()
        env = _build_worker_env(worker_id, excel_worker, mode)
        proc = subprocess.run(
            base_cmd,
            cwd=project_root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        elapsed = time.time() - started
        with lock_results:
            results.append(
                {
                    "worker": worker_id,
                    "idx_principal": idx_label,
                    "exit_code": int(proc.returncode),
                    "elapsed_sec": round(elapsed, 2),
                    "stdout_tail": (proc.stdout or "")[-1200:],
                    "stderr_tail": (proc.stderr or "")[-1200:],
                }
            )

        if proc.returncode == 0:
            print(f"[INFO][W{worker_id}] {idx_label} finalizo OK en {elapsed:.2f}s")
        else:
            print(f"[ERROR][W{worker_id}] {idx_label} finalizo con codigo={proc.returncode} en {elapsed:.2f}s")
        return int(proc.returncode)

    worker_queues = {}

    def worker_loop(worker_id: int) -> int:
        q = worker_queues[worker_id]
        local_done = 0
        seq = 0
        while True:
            try:
                unit = q.get_nowait()
            except queue.Empty:
                break

            seq += 1
            idx = int(unit["idx_principal"])
            try:
                excel_worker = _make_worker_excel(
                    worker_id,
                    set(int(x) for x in unit["indices_relacionados"]),
                    f"unit_{seq}_idx_{idx}",
                )
                print(f"[INFO][W{worker_id}] Iniciando unidad idx={idx} rel={unit['indices_relacionados']}")
                _run_unit(worker_id, f"Unidad idx={idx}", excel_worker, worker_mode)
            except Exception as e:
                with lock_results:
                    results.append(
                        {
                            "worker": worker_id,
                            "idx_principal": idx,
                            "exit_code": -1,
                            "elapsed_sec": 0,
                            "stdout_tail": "",
                            "stderr_tail": f"EXCEPCION_WORKER: {e}\n{traceback.format_exc()}",
                        }
                    )
                print(f"[ERROR][W{worker_id}] Excepcion en unidad idx={idx}: {e}")
            finally:
                q.task_done()
                local_done += 1
        return local_done

    def worker_sticky(worker_id: int, assigned_units: list) -> int:
        if not assigned_units:
            return 0

        assigned_idx = [int(u["idx_principal"]) for u in assigned_units]
        target_indices = set()
        for u in assigned_units:
            target_indices.update(int(x) for x in u["indices_relacionados"])

        print(f"[INFO][W{worker_id}] Lote asignado idx={assigned_idx}")
        try:
            excel_worker = _make_worker_excel(
                worker_id,
                target_indices,
                f"batch_{len(assigned_units)}_idxs_{'_'.join(str(x) for x in assigned_idx)}",
            )
            _run_unit(worker_id, f"Lote idx={assigned_idx}", excel_worker, worker_mode)
        except Exception as e:
            with lock_results:
                results.append(
                    {
                        "worker": worker_id,
                        "idx_principal": f"batch:{assigned_idx}",
                        "exit_code": -1,
                        "elapsed_sec": 0,
                        "stdout_tail": "",
                        "stderr_tail": f"EXCEPCION_WORKER_STICKY: {e}\n{traceback.format_exc()}",
                    }
                )
            print(f"[ERROR][W{worker_id}] Excepcion en lote idx={assigned_idx}: {e}")

        return len(assigned_units)

    test_started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        if worker_mode == "sticky":
            assigned_by_worker = {wid: [] for wid in range(1, workers + 1)}
            for pos, unit in enumerate(unidades):
                wid = (pos % workers) + 1
                assigned_by_worker[wid].append(unit)
            futures = [
                ex.submit(worker_sticky, wid, assigned_by_worker[wid])
                for wid in range(1, workers + 1)
            ]
            counts = [f.result() for f in futures]
        else:
            global_queue = queue.Queue()
            for unit in unidades:
                global_queue.put(unit)
            worker_queues = {wid: global_queue for wid in range(1, workers + 1)}
            futures = [ex.submit(worker_loop, wid) for wid in range(1, workers + 1)]
            counts = [f.result() for f in futures]

    total_elapsed = time.time() - test_started
    total_unidades_procesadas = sum(int(x) for x in counts)
    print(f"[INFO] Conteo por worker: {counts}")
    print(f"[INFO] Unidades procesadas: {total_unidades_procesadas}/{len(unidades)}")
    print(f"[INFO] Tiempo total multihilo: {total_elapsed:.2f}s")

    failed = [r for r in results if int(r.get("exit_code", 1)) != 0]
    if failed:
        print(f"[ERROR] Unidades con fallo: {len(failed)}")
        for r in failed:
            print(
                f"[ERROR][W{r['worker']}] idx={r['idx_principal']} "
                f"exit={r['exit_code']} stderr_tail={r['stderr_tail']}"
            )
        raise Exception(f"Flujo multihilo finalizó con {len(failed)} fallos")

    print("[OK] Flujo multihilo scheduled finalizado sin fallos de proceso")


# ============================================================
# FLUJO PRINCIPAL
# ============================================================

def llenar_login_sel():
    print("[INFO] INICIANDO SCRIPT SEL - Login Automtico")

    if _multihilo_scheduled_habilitado():
        _ejecutar_scheduled_multihilo_orquestador()
        return

    run_mode = os.getenv("RUN_MODE", "manual").strip().lower()
    is_scheduled = run_mode == "scheduled"
    hold_browser_open = os.getenv("HOLD_BROWSER_OPEN", "0").strip().lower() in {"1", "true", "si", "sí", "yes"}

    tile_enabled = os.getenv("BROWSER_TILE_ENABLE", "0").strip().lower() in {"1", "true", "si", "sí", "yes"}
    try:
        tile_total = int(str(os.getenv("BROWSER_TILE_TOTAL", "1") or "1").strip())
    except Exception:
        tile_total = 1
    if tile_total < 1:
        tile_total = 1
    try:
        tile_index = int(str(os.getenv("BROWSER_TILE_INDEX", "0") or "0").strip())
    except Exception:
        tile_index = 0
    if tile_index < 0:
        tile_index = 0
    if tile_index >= tile_total:
        tile_index = tile_total - 1

    try:
        tile_screen_w = int(str(os.getenv("BROWSER_TILE_SCREEN_W", "1920") or "1920").strip())
    except Exception:
        tile_screen_w = 1920
    try:
        tile_screen_h = int(str(os.getenv("BROWSER_TILE_SCREEN_H", "1080") or "1080").strip())
    except Exception:
        tile_screen_h = 1080
    try:
        tile_top_offset = int(str(os.getenv("BROWSER_TILE_TOP_OFFSET", "0") or "0").strip())
    except Exception:
        tile_top_offset = 0
    try:
        tile_gap = int(str(os.getenv("BROWSER_TILE_GAP", "8") or "8").strip())
    except Exception:
        tile_gap = 8
    if tile_gap < 0:
        tile_gap = 0
    try:
        tile_frame_pad = int(str(os.getenv("BROWSER_TILE_FRAME_PAD", "24") or "24").strip())
    except Exception:
        tile_frame_pad = 24
    if tile_frame_pad < 0:
        tile_frame_pad = 0

    tile_x = 0
    tile_y = 0
    tile_w = 1920
    tile_h = 1080
    if tile_enabled:
        cols = 2 if tile_total == 2 else (1 if tile_total == 1 else 2)
        rows = (tile_total + cols - 1) // cols
        usable_h = max(480, tile_screen_h - max(0, tile_top_offset))
        cell_w = max(360, tile_screen_w // cols)
        cell_h = max(320, usable_h // rows)

        # Split sin solape: gap + compensacion de marco para Windows (DPI/bordes/titulo).
        tile_w = max(320, cell_w - (tile_gap * 2) - tile_frame_pad)
        tile_h = max(260, cell_h - (tile_gap * 2))
        col = tile_index % cols
        row = tile_index // cols
        tile_x = col * cell_w + tile_gap + (tile_frame_pad if col > 0 else 0)
        tile_y = max(0, tile_top_offset) + row * cell_h + tile_gap

    try:
        max_run_minutes = float(str(os.getenv("MAX_RUN_MINUTES", "0") or "0").strip())
    except Exception:
        max_run_minutes = 0.0
    if max_run_minutes < 0:
        max_run_minutes = 0.0

    try:
        max_login_retries_per_group = int(str(os.getenv("MAX_LOGIN_RETRIES_PER_GROUP", "12") or "12").strip())
    except Exception:
        max_login_retries_per_group = 12
    if max_login_retries_per_group < 1:
        max_login_retries_per_group = 1

    try:
        login_validation_timeout_ms = int(str(os.getenv("LOGIN_VALIDATION_TIMEOUT_MS", "6000") or "6000").strip())
    except Exception:
        login_validation_timeout_ms = 6000
    if login_validation_timeout_ms < 1000:
        login_validation_timeout_ms = 1000

    try:
        terminal_confirmaciones_requeridas = int(str(os.getenv("TERMINAL_CONFIRM_ATTEMPTS", "2") or "2").strip())
    except Exception:
        terminal_confirmaciones_requeridas = 2
    if terminal_confirmaciones_requeridas < 1:
        terminal_confirmaciones_requeridas = 1

    try:
        nro_solicitud_confirmaciones_requeridas = int(
            str(
                os.getenv(
                    "NRO_SOLICITUD_CONFIRM_ATTEMPTS",
                    str(terminal_confirmaciones_requeridas),
                )
                or str(terminal_confirmaciones_requeridas)
            ).strip()
        )
    except Exception:
        nro_solicitud_confirmaciones_requeridas = terminal_confirmaciones_requeridas
    if nro_solicitud_confirmaciones_requeridas < 1:
        nro_solicitud_confirmaciones_requeridas = 1

    try:
        sin_cupo_confirmaciones_requeridas = int(str(os.getenv("SIN_CUPO_CONFIRM_ATTEMPTS", "1") or "1").strip())
    except Exception:
        sin_cupo_confirmaciones_requeridas = 1
    if sin_cupo_confirmaciones_requeridas < 1:
        sin_cupo_confirmaciones_requeridas = 1

    try:
        max_unmapped_retries_per_record = int(
            str(os.getenv("MAX_UNMAPPED_RETRIES_PER_RECORD", "4") or "4").strip()
        )
    except Exception:
        max_unmapped_retries_per_record = 4
    if max_unmapped_retries_per_record < 0:
        max_unmapped_retries_per_record = 0

    try:
        max_hora_fallback_retries = int(str(os.getenv("MAX_HOUR_FALLBACK_RETRIES", "8") or "8").strip())
    except Exception:
        max_hora_fallback_retries = 8
    if max_hora_fallback_retries < 1:
        max_hora_fallback_retries = 1

    # Flag para mantener navegador abierto entre grupos (evita cerrar/reabrirlo por cada grupo)
    persistent_session = str(os.getenv("PERSISTENT_SESSION", "0")).strip().lower() in ("1", "true", "yes")
    if persistent_session:
        print("[INFO] PERSISTENT_SESSION activado - navegador se reutilizará entre grupos sin cerrarse")

    inicio_total_flujo = time.time()
    duracion_total_flujo = None

    playwright = sync_playwright().start()
    browser = None
    context = None
    login_exitoso = False
    total_ok = 0
    total_sin_cupo = 0
    total_error = 0

    def es_error_transitorio_para_relogin(error: BaseException) -> bool:
        """Detecta estados UI inconsistentes donde conviene reloguear en lugar de marcar error."""
        txt = str(error or "").lower()
        pistas = [
            "relogin_ui_desync",
            "tipo de trmite es obligatorio",
            "no se confirm la vista de 'reservas de citas'",
            "no se encontr el header 'citas'",
            "no se confirm la seleccin en el combo",
            "reserva_form no visible",
        ]
        return any(p in txt for p in pistas)

    def asegurar_contexto_reserva_operativo(page):
        """
        Valida que la UI est lista antes de procesar cada registro.
        Si detecta estado intermedio (ej. combo en ---), intenta recomponer una vez.
        """
        try:
            page.locator("form#gestionCitasForm").wait_for(state="visible", timeout=6000)
        except Exception as e:
            raise Exception("RELOGIN_UI_DESYNC: gestionCitasForm no visible") from e

        label_cita = ""
        try:
            label_cita = (page.locator(SEL["tipo_cita_label"]).inner_text() or "").strip().upper()
        except Exception:
            label_cita = ""

        if not label_cita or label_cita == "---":
            seleccionar_tipo_cita_poligono(page)
            page.wait_for_timeout(350)
            try:
                label_cita = (page.locator(SEL["tipo_cita_label"]).inner_text() or "").strip().upper()
            except Exception:
                label_cita = ""

        if not label_cita or label_cita == "---":
            raise Exception("RELOGIN_UI_DESYNC: combo 'Cita para' permanece en '---'")

        try:
            page.locator(SEL["reserva_form"]).wait_for(state="visible", timeout=7000)
        except Exception as e:
            raise Exception("RELOGIN_UI_DESYNC: reserva_form no visible") from e

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
                f"Faltan credenciales para grupo {etiqueta}: {faltantes}. "
                "Configúralas en .env"
            )

    def validar_tiempo_maximo():
        if max_run_minutes <= 0:
            return
        transcurrido = time.time() - inicio_total_flujo
        if transcurrido >= max_run_minutes * 60:
            raise KeyboardInterrupt(
                f"MAX_RUN_MINUTES alcanzado ({max_run_minutes} min)"
            )

    def clasificar_error_terminal_registro(error: BaseException) -> str:
        txt = str(error or "")
        txt_low = txt.lower()
        if isinstance(error, SinCupoError):
            return "SIN_CUPO"
        if isinstance(error, FechaNoDisponibleError):
            return "FECHA_NO_DISPONIBLE"
        if isinstance(error, TurnoDuplicadoError):
            return "TURNO_DUPLICADO"
        if "ya existe un turno registrado" in txt_low:
            return "TURNO_DUPLICADO"
        if "no se encontr" in txt_low and "nro solicitud" in txt_low:
            return "NRO_SOLICITUD"
        if "no hay opciones en el combo de nro solicitud" in txt_low:
            return "NRO_SOLICITUD"
        if "documento vigilante" in txt_low:
            return "DOC_VIGILANTE"
        if "no se encontró la hora objetivo en la tabla" in txt:
            return "HORA_NO_DISPONIBLE"
        return ""

    def observacion_terminal_por_categoria(categoria: str, registro_excel: dict, error: BaseException) -> str:
        if categoria == "SIN_CUPO":
            return f"No alcanzo cupo para horario {registro_excel.get('hora_rango', '')}"
        if categoria == "NRO_SOLICITUD":
            return f"No se encontró Nro Solicitud/Código de pago para token de {registro_excel.get('nro_solicitud', '')}"
        if categoria == "DOC_VIGILANTE":
            return (
                "Documento vigilante no disponible para esta razón social/RUC. "
                f"DNI={registro_excel.get('doc_vigilante', '')} | RUC={registro_excel.get('ruc', '')}"
            )
        if categoria == "HORA_NO_DISPONIBLE":
            return (
                "Horario no figura en la tabla de cupos: "
                f"{registro_excel.get('hora_rango', '')}"
            )
        if categoria == "FECHA_NO_DISPONIBLE":
            return (
                "Fecha no disponible en combo de Reserva de Cupos: "
                f"{registro_excel.get('fecha', '')}"
            )
        if categoria == "TURNO_DUPLICADO":
            return (
                "Ya existe un turno registrado para la misma persona y Tipo de Licencia. "
                f"DNI={registro_excel.get('doc_vigilante', '')} | "
                f"TipoOperacion={registro_excel.get('tipo_operacion', '')}"
            )
        return f"Error en procesamiento: {error}"

    def confirmaciones_requeridas_para_categoria(categoria: str) -> int:
        if categoria == "SIN_CUPO":
            return sin_cupo_confirmaciones_requeridas
        if categoria == "TURNO_DUPLICADO":
            return 1
        if categoria == "NRO_SOLICITUD":
            return nro_solicitud_confirmaciones_requeridas
        return terminal_confirmaciones_requeridas

    def observacion_error_no_mapeado(registro_excel: dict, error: BaseException, intentos: int) -> str:
        hora = registro_excel.get("hora_rango", "")
        token = registro_excel.get("nro_solicitud", "")
        return (
            "Error no mapeado persistente tras "
            f"{intentos} intentos (hora={hora}, token={token}): {error}"
        )

    try:
        validar_tiempo_maximo()
        trabajos_pendientes = obtener_trabajos_pendientes_excel(EXCEL_PATH)
        if not trabajos_pendientes:
            print("\n No hay registros pendientes para procesar. Todos los registros han sido procesados o marcados.")
            return

        print(f"\n Registros pendientes a procesar: {len(trabajos_pendientes)}")

        grupos_ordenados = ["SELVA", "JV", "OTRO"]
        trabajos_por_grupo = {g: [] for g in grupos_ordenados}
        for trabajo in trabajos_pendientes:
            grupo = trabajo.get("ruc_grupo", "OTRO")
            if grupo not in trabajos_por_grupo:
                grupo = "OTRO"
            trabajos_por_grupo[grupo].append(trabajo)

        for grupo_ruc in grupos_ordenados:
            validar_tiempo_maximo()
            trabajos_grupo = trabajos_por_grupo.get(grupo_ruc, [])
            if not trabajos_grupo:
                continue

            credenciales_grupo = resolver_credenciales_por_grupo_ruc(grupo_ruc)
            validar_credenciales_configuradas(credenciales_grupo, grupo_ruc)

            print(f"\n Procesando grupo RUC {grupo_ruc} - Registros: {len(trabajos_grupo)}")
            grupo_procesado = False

            intento_global = 0
            while True:
                validar_tiempo_maximo()
                start_time = time.time()
                intento_global += 1
                print(
                    f"\n[INFO] Intento login {intento_global}/{max_login_retries_per_group} "
                    f"para grupo {grupo_ruc}"
                )

                if max_login_retries_per_group > 0 and intento_global > max_login_retries_per_group:
                    raise Exception(
                        f"MAX_LOGIN_RETRIES_PER_GROUP alcanzado para grupo {grupo_ruc}: {max_login_retries_per_group}"
                    )

                # En persistent_session mode: NO cerrar navegador en el primer intento (reusar del grupo anterior)
                # En reintentos (intento_global > 1): SÍ cerrar para empezar limpio
                debe_cerrar_navegador = True
                if persistent_session and intento_global == 1 and browser is not None:
                    debe_cerrar_navegador = False
                    print("[DEBUG] PERSISTENT_SESSION: reutilizando navegador del grupo anterior")

                if debe_cerrar_navegador and browser is not None:
                    try:
                        browser.close()
                    except Exception:
                        pass

                launch_args = ["--disable-infobars"]
                if tile_enabled:
                    launch_args.extend([
                        f"--window-size={tile_w},{tile_h}",
                        f"--window-position={tile_x},{tile_y}",
                    ])
                    print(f"[TILE] Args launch: --window-size={tile_w},{tile_h} --window-position={tile_x},{tile_y}")
                else:
                    launch_args.extend([
                        "--start-maximized",
                        "--window-size=1920,1080",
                        "--window-position=0,0",
                    ])

                # En persistent_session mode: reutilizar navegador existente en primer intento
                if persistent_session and intento_global == 1 and browser is not None:
                    print("[DEBUG] PERSISTENT_SESSION: creando nuevo context/page en navegador existente")
                    context = browser.new_context(no_viewport=True, ignore_https_errors=True)
                    page = context.new_page()
                else:
                    print(f"[TILE] Lanzando Chromium con args: {launch_args}")
                    browser = playwright.chromium.launch(
                        headless=False,
                        slow_mo=0,
                        args=launch_args,
                    )
                    context = browser.new_context(no_viewport=True, ignore_https_errors=True)
                    page = context.new_page()
                    page.wait_for_timeout(300)

                if tile_enabled:
                    actual_dims = page.evaluate("""
                        () => {
                            return {
                                screenW: window.screen.availWidth || window.screen.width,
                                screenH: window.screen.availHeight || window.screen.height,
                                outerW: window.outerWidth,
                                outerH: window.outerHeight,
                                innerW: window.innerWidth,
                                innerH: window.innerHeight,
                            };
                        }
                    """)
                    print(
                        "[TILE] Geometría aplicada -> "
                        f"xy=({tile_x},{tile_y}) "
                        f"wh=({tile_w},{tile_h}) "
                        f"screen_cfg={tile_screen_w}x{tile_screen_h} "
                        f"screen_js={actual_dims.get('screenW')}x{actual_dims.get('screenH')} "
                        f"outer_js={actual_dims.get('outerW')}x{actual_dims.get('outerH')}"
                    )
                else:
                    page.evaluate("() => { window.moveTo(0, 0); window.resizeTo(screen.width, screen.height); }")
                activar_monitor_growl(page)

                try:
                    page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=45000)
                    esperar_hasta_servicio_disponible(page, URL_LOGIN, espera_segundos=8)
                    print("[INFO] Pagina de login cargada")

                    activar_pestana_autenticacion_tradicional(page)

                    page.locator(SEL["numero_documento"]).wait_for(state="visible", timeout=8000)

                    page.select_option(SEL["tipo_doc_select"], value=credenciales_grupo["tipo_documento_valor"])
                    page.wait_for_timeout(450)
                    page.locator(SEL["numero_documento"]).wait_for(state="visible", timeout=8000)
                    escribir_input_jsf(page, SEL["numero_documento"], credenciales_grupo["numero_documento"])
                    escribir_input_rapido(page, SEL["usuario"], credenciales_grupo["usuario"])
                    escribir_input_rapido(page, SEL["clave"], credenciales_grupo["contrasena"])
                    print(f"[INFO] Credenciales llenadas para grupo {grupo_ruc}")

                    captcha_text = solve_captcha_ocr(page)
                    if captcha_text and len(captcha_text) == 5:
                        escribir_input_rapido(page, SEL["captcha_input"], captcha_text)
                        print(f"[INFO] Captcha automatico: {captcha_text}")
                    else:
                        solve_captcha_manual(page)

                    print("[INFO] Enviando login...")
                    page.locator(SEL["ingresar"]).click(timeout=10000)

                    print("[INFO] Validando acceso...")
                    url_ok, mensaje_error, tiempo_espera = validar_resultado_login_por_ui(
                        page,
                        timeout_ms=login_validation_timeout_ms,
                    )

                    if not url_ok:
                        print("[ERROR] Login fallo - no se detecto sesion autenticada")
                        print(f"   -> URL actual: {page.url}")
                        if mensaje_error:
                            print(f"   -> Error detectado: {mensaje_error}")
                        print(f"[INFO] Tiempo validacion: {tiempo_espera:.2f} segundos")
                        raise Exception("CAPTCHA incorrecto o credenciales inválidas")

                    total_time = time.time() - start_time
                    print("[INFO] Acceso exitoso")
                    print(f"   -> URL: {page.url}")
                    print(f"[INFO] Tiempo total login: {total_time:.2f} segundos")
                    login_exitoso = True

                    navegar_reservas_citas(page)
                    seleccionar_tipo_cita_poligono(page)

                    cola_trabajos = deque(trabajos_grupo)
                    intentos_por_idx = {}
                    intentos_no_mapeados_por_idx = {}
                    intentos_replan_hora_por_idx = {}
                    confirmaciones_terminales = {}
                    iteracion = 0

                    while cola_trabajos:
                        validar_tiempo_maximo()
                        iteracion += 1
                        n = iteracion
                        trabajo = cola_trabajos.popleft()
                        idx_excel = trabajo["idx_excel"]
                        intentos_por_idx[idx_excel] = intentos_por_idx.get(idx_excel, 0) + 1
                        print(
                            f"\n-------- {grupo_ruc} Registro iterativo {n} "
                            f"(idx={idx_excel}, prioridad={trabajo.get('prioridad', 'Normal')}, "
                            f"intento={intentos_por_idx[idx_excel]}, en_cola={len(cola_trabajos)}) --------"
                        )

                        esperar_hasta_servicio_disponible(page, page.url, espera_segundos=8)

                        # Verifica estado de UI antes de cargar/procesar el registro.
                        asegurar_contexto_reserva_operativo(page)

                        try:
                            registro_excel = cargar_primer_registro_pendiente_desde_excel(
                                EXCEL_PATH,
                                indice_excel_objetivo=idx_excel,
                            )
                            registro_excel["_horas_descartadas"] = list(trabajo.get("_horas_descartadas", []) or [])
                        except Exception as e:
                            txt_carga = str(e or "")
                            if "no est en estado Pendiente" in txt_carga or "No hay registros con estado 'Pendiente'" in txt_carga:
                                print(f"[INFO] Registro idx={idx_excel} ya no est pendiente. Se omite.")
                                intentos_no_mapeados_por_idx.pop(idx_excel, None)
                                intentos_replan_hora_por_idx.pop(idx_excel, None)
                                confirmaciones_terminales.pop((idx_excel, "SIN_CUPO"), None)
                                confirmaciones_terminales.pop((idx_excel, "NRO_SOLICITUD"), None)
                                confirmaciones_terminales.pop((idx_excel, "DOC_VIGILANTE"), None)
                                confirmaciones_terminales.pop((idx_excel, "HORA_NO_DISPONIBLE"), None)
                                confirmaciones_terminales.pop((idx_excel, "FECHA_NO_DISPONIBLE"), None)
                                confirmaciones_terminales.pop((idx_excel, "TURNO_DUPLICADO"), None)
                                continue
                            raise

                        try:
                            try:
                                page.locator(SEL["reserva_form"]).wait_for(state="visible", timeout=2500)
                            except Exception:
                                seleccionar_tipo_cita_poligono(page)

                            seleccionar_sede_y_fecha_desde_registro(page, registro_excel)
                            seleccionar_hora_con_cupo_y_avanzar(page, registro_excel)
                            completar_paso_2_desde_registro(page, registro_excel)
                            validar_turno_duplicado_o_lanzar(page, max_wait_ms=900)
                            completar_tabla_tipos_arma_y_avanzar(page, registro_excel)
                            validar_turno_duplicado_o_lanzar(page, max_wait_ms=900)
                            completar_fase_3_resumen(page)
                            validar_turno_duplicado_o_lanzar(page, max_wait_ms=900)

                            # Generar la cita con reintentos si el captcha falla
                            generar_cita_final_con_reintento_rapido(page, max_intentos=5)

                            # Marcar como cita programada en Excel
                            registrar_cita_programada_en_excel(EXCEL_PATH, registro_excel)

                            limpiar_para_siguiente_registro(page, motivo="fin de flujo")
                            total_ok += 1
                            intentos_no_mapeados_por_idx.pop(idx_excel, None)
                            intentos_replan_hora_por_idx.pop(idx_excel, None)
                            confirmaciones_terminales.pop((idx_excel, "SIN_CUPO"), None)
                            confirmaciones_terminales.pop((idx_excel, "NRO_SOLICITUD"), None)
                            confirmaciones_terminales.pop((idx_excel, "DOC_VIGILANTE"), None)
                            confirmaciones_terminales.pop((idx_excel, "HORA_NO_DISPONIBLE"), None)
                            confirmaciones_terminales.pop((idx_excel, "FECHA_NO_DISPONIBLE"), None)
                            confirmaciones_terminales.pop((idx_excel, "TURNO_DUPLICADO"), None)

                        except Exception as e:
                            motivo_detencion = clasificar_motivo_detencion(e)
                            if motivo_detencion == "VENTANA_CERRADA":
                                print(
                                    f"🛑 Registro idx={idx_excel} no procesado: "
                                    "la ventana/contexto del navegador fue cerrada."
                                )
                                raise KeyboardInterrupt("Ventana del navegador cerrada durante procesamiento") from e

                            if es_error_transitorio_para_relogin(e):
                                print(
                                    f"[WARNING] Estado transitorio UI en idx={idx_excel}: {e}. "
                                    "Se reiniciar sesin para recuperar flujo."
                                )
                                raise Exception("RELOGIN_UI_DESYNC") from e

                            if isinstance(e, CuposOcupadosPostValidacionError):
                                hora_actual = normalizar_hora_rango(registro_excel.get("_hora_seleccionada_actual", ""))
                                descartadas = list(trabajo.get("_horas_descartadas", []) or [])
                                if hora_actual and hora_actual not in descartadas:
                                    descartadas.append(hora_actual)
                                trabajo["_horas_descartadas"] = descartadas

                                hits_hora = intentos_replan_hora_por_idx.get(idx_excel, 0) + 1
                                intentos_replan_hora_por_idx[idx_excel] = hits_hora

                                if hits_hora >= max_hora_fallback_retries:
                                    obs = (
                                        "Cupos ocupados al confirmar cita tras "
                                        f"{hits_hora} reintentos de horario. "
                                        f"Última hora evaluada: {hora_actual or registro_excel.get('hora_rango', '')}"
                                    )
                                    registrar_sin_cupo_en_excel(EXCEL_PATH, registro_excel, obs)
                                    total_sin_cupo += 1
                                    print(
                                        f"[INFO] Registro idx={idx_excel} marcado como SIN_CUPO por "
                                        f"cupos ocupados post-validación ({hits_hora}/{max_hora_fallback_retries})."
                                    )
                                else:
                                    intentos_no_mapeados_por_idx.pop(idx_excel, None)
                                    print(
                                        f"[INFO] Registro idx={idx_excel} con cupos ocupados post-validación. "
                                        f"Reintentando con otro horario ({hits_hora}/{max_hora_fallback_retries})..."
                                    )
                                    cola_trabajos.append(trabajo)

                                try:
                                    limpiar_para_siguiente_registro(page, motivo="replanificación por cupos ocupados")
                                except Exception:
                                    pass
                                time.sleep(1)
                                continue

                            # Fallback: si llegó un error genérico pero el growl indica turno duplicado,
                            # lo remapeamos para tratarlo como causal terminal controlada.
                            if not isinstance(e, TurnoDuplicadoError):
                                try:
                                    validar_turno_duplicado_o_lanzar(page, max_wait_ms=900)
                                except TurnoDuplicadoError as e_dup:
                                    e = e_dup

                            categoria_terminal = clasificar_error_terminal_registro(e)
                            print(f"[WARNING] Error en registro idx={idx_excel}: {e}")

                            if categoria_terminal:
                                intentos_no_mapeados_por_idx.pop(idx_excel, None)
                                intentos_replan_hora_por_idx.pop(idx_excel, None)
                                clave_conf = (idx_excel, categoria_terminal)
                                confirmaciones_terminales[clave_conf] = confirmaciones_terminales.get(clave_conf, 0) + 1
                                hits = confirmaciones_terminales[clave_conf]
                                requeridas = confirmaciones_requeridas_para_categoria(categoria_terminal)

                                if hits >= requeridas:
                                    obs = observacion_terminal_por_categoria(categoria_terminal, registro_excel, e)
                                    registrar_sin_cupo_en_excel(EXCEL_PATH, registro_excel, obs)
                                    if categoria_terminal == "SIN_CUPO":
                                        total_sin_cupo += 1
                                    else:
                                        total_error += 1
                                    print(
                                        f"[INFO] Registro idx={idx_excel} marcado como terminal '{categoria_terminal}' "
                                        f"tras {hits} confirmaciones"
                                    )
                                else:
                                    print(
                                        f"[INFO] Registro idx={idx_excel} con causal terminal '{categoria_terminal}' "
                                        f"pendiente de confirmación ({hits}/{requeridas}). Reencolando..."
                                    )
                                    cola_trabajos.append(trabajo)
                            else:
                                hits_no_mapeados = intentos_no_mapeados_por_idx.get(idx_excel, 0) + 1
                                intentos_no_mapeados_por_idx[idx_excel] = hits_no_mapeados
                                if (
                                    max_unmapped_retries_per_record > 0
                                    and hits_no_mapeados >= max_unmapped_retries_per_record
                                ):
                                    obs = observacion_error_no_mapeado(registro_excel, e, hits_no_mapeados)
                                    registrar_sin_cupo_en_excel(EXCEL_PATH, registro_excel, obs)
                                    total_error += 1
                                    print(
                                        f"[INFO] Registro idx={idx_excel} marcado con error no mapeado "
                                        f"tras {hits_no_mapeados} intentos"
                                    )
                                else:
                                    print(
                                        f"[INFO] Error transitorio/no clasificado en idx={idx_excel}. "
                                        f"Reencolando ({hits_no_mapeados}/"
                                        f"{max_unmapped_retries_per_record if max_unmapped_retries_per_record > 0 else 'sin limite'})..."
                                    )
                                    cola_trabajos.append(trabajo)

                            try:
                                limpiar_para_siguiente_registro(page, motivo="recuperación por error")
                            except Exception:
                                pass
                            time.sleep(1)
                            continue

                    grupo_procesado = True
                    
                    # En persistent_session mode: cerrar contexto pero mantener navegador abierto
                    if persistent_session:
                        try:
                            print("[DEBUG] PERSISTENT_SESSION: cerrando contexto anterior para siguiente grupo")
                            context.close()
                        except Exception as e_ctx:
                            print(f"[DEBUG] Error cerrando contexto: {e_ctx}")

                    break

                except Exception as e:
                    motivo_detencion = clasificar_motivo_detencion(e)
                    if motivo_detencion == "VENTANA_CERRADA":
                        print(
                            "🛑 Proceso detenido: se cerró la ventana/contexto del navegador "
                            f"durante el login del grupo {grupo_ruc}."
                        )
                        raise KeyboardInterrupt("Ventana del navegador cerrada") from e

                    if "CAPTCHA_MANUAL_REQUERIDO_EN_SCHEDULED" in str(e or ""):
                        print(
                            "[ERROR] En modo scheduled no se permite input manual de captcha. "
                            "Finalizando corrida para evitar bloqueo."
                        )
                        raise

                    if es_error_transitorio_para_relogin(e):
                        print(
                            f"[WARNING] Intento login {intento_global} para grupo {grupo_ruc}: "
                            "se detect desincronizacin de UI. Reintentando login..."
                        )
                        time.sleep(1)
                        continue

                    print(f"[ERROR] Intento login {intento_global} para grupo {grupo_ruc} fall: {e}")
                    if intento_global >= max_login_retries_per_group:
                        raise

                    print("   Reintentando login...")
                    espera_backoff = min(8, 1 + intento_global)
                    time.sleep(espera_backoff)

            if not grupo_procesado:
                total_error += len(trabajos_grupo)
                print(
                    f"[WARNING] No se pudo procesar el grupo {grupo_ruc}. "
                    f"Se contabilizan {len(trabajos_grupo)} registros con error."
                )

        duracion_total_flujo = time.time() - inicio_total_flujo
        print(f"\n Tiempo total del flujo: {duracion_total_flujo:.2f} segundos")
        print(f" Resumen: OK={total_ok} | SIN_CUPO={total_sin_cupo} | ERROR={total_error}")

        if login_exitoso:
            print("\n[INFO] Flujo completado.")
            if duracion_total_flujo is not None:
                print(f"    Duracin final del flujo: {duracion_total_flujo:.2f} segundos")
            if hold_browser_open and not is_scheduled:
                print("   Navegador abierto para uso manual.")
                print("   Presiona Ctrl+C o cierra la ventana cuando termines.")
                try:
                    while True:
                        time.sleep(60)
                except KeyboardInterrupt:
                    print("\n Interrupcin manual. Cerrando navegador...")
        else:
            print("\n[ERROR] No se pudo completar el login despus de todos los intentos.")
            if not is_scheduled:
                input("   Presiona ENTER para cerrar el navegador...")

    except KeyboardInterrupt as e:
        if duracion_total_flujo is None:
            duracion_total_flujo = time.time() - inicio_total_flujo
        print("\n Ejecucin interrumpida.")
        print(f"   -> Motivo: {e}")
        print(f"    Tiempo transcurrido: {duracion_total_flujo:.2f} segundos")
        print(f"    Resumen parcial: OK={total_ok} | SIN_CUPO={total_sin_cupo} | ERROR={total_error}")

    finally:
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
        try:
            playwright.stop()
        except Exception:
            pass
        print("Navegador cerrado.")


if __name__ == "__main__":
    llenar_login_sel()
