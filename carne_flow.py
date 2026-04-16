import os
import csv
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from carnet_emision import (
    CREDENCIALES_JV,
    CREDENCIALES_SELVA,
    DEFAULT_GSHEET_COMPARE_URL,
    DEFAULT_GSHEET_URL,
    SEL,
    URL_LOGIN,
    _as_bool_env,
    _build_launch_args_for_window,
    _build_google_sheet_csv_url,
    _leer_google_sheet_rows,
    _normalizar_columna,
    _resolver_columna,
    activar_pestana_autenticacion_tradicional,
    escribir_input_rapido,
    esperar_hasta_servicio_disponible,
    navegar_dssp_carne_crear_solicitud,
    obtener_grupo_ruc,
    resolver_sede_atencion_desde_departamento,
    resolver_sede_para_dropdown,
    seleccionar_sede_atencion,
    setup_logger,
    solve_captcha_ocr,
    validar_vista_crear_solicitud_por_ui,
    validar_resultado_login_por_ui,
)


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STAGING_DIR = BASE_DIR / "logs" / ".cache_carne_flow"


def _prune_staging_csv_by_count(keep_files: int = 30) -> int:
    try:
        keep = max(1, int(keep_files or 1))
    except Exception:
        keep = 30

    try:
        archivos = [p for p in STAGING_DIR.glob("*.csv") if p.is_file()]
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


def _descargar_sheet_csv_a_local(sheet_url: str, logger, etiqueta: str) -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    try:
        keep_staging = max(1, int(str(os.getenv("CARNET_CARNE_FLOW_STAGING_KEEP_FILES", "30") or "30").strip()))
    except Exception:
        keep_staging = 30
    pruned = _prune_staging_csv_by_count(keep_files=keep_staging)
    if pruned > 0:
        logger.info("[CARNE_FLOW] Retención staging: %s CSV antiguos eliminados", pruned)

    csv_url = _build_google_sheet_csv_url(sheet_url)
    destino = STAGING_DIR / f"{etiqueta.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    req = Request(
        csv_url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; carne-flow/1.0)"},
    )
    with urlopen(req, timeout=30) as resp:
        content = resp.read()

    destino.write_bytes(content)
    logger.info("[%s] Sheet descargado a %s", etiqueta, destino)
    return destino


def _leer_google_sheet_rows_local(sheet_url: str, logger, etiqueta: str) -> tuple[list[dict], list[str]]:
    csv_local = _descargar_sheet_csv_a_local(sheet_url, logger, etiqueta)
    with csv_local.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])
    return rows, fieldnames


def _leer_google_sheet_rows_local_preservando_duplicados(sheet_url: str, logger, etiqueta: str) -> tuple[list[list[str]], list[str]]:
    csv_local = _descargar_sheet_csv_a_local(sheet_url, logger, etiqueta)
    with csv_local.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        filas = list(reader)

    if not filas:
        return [], []

    headers = [str(x or "") for x in filas[0]]
    return filas[1:], headers


def _resolver_indice_columna(fieldnames: list[str], candidatos: list[str]) -> int | None:
    candidatos_norm = {str(c or "").strip().lower() for c in candidatos if str(c or "").strip()}
    for idx, col in enumerate(fieldnames):
        if str(col or "").strip().lower() in candidatos_norm:
            return idx
    return None


def _indices_columna_exacta(fieldnames: list[str], nombre_columna: str) -> list[int]:
    objetivo = str(nombre_columna or "").strip().lower()
    salida = []
    for idx, col in enumerate(fieldnames):
        if str(col or "").strip().lower() == objetivo:
            salida.append(idx)
    return salida


def _valor_fila_por_indice(fila: list[str], idx: int | None) -> str:
    if idx is None:
        return ""
    if idx < 0 or idx >= len(fila):
        return ""
    return str(fila[idx] or "").strip()


def _estado_comparacion_es_objetivo(estado: str, estados_objetivo: set[str], permitir_vacio: bool) -> bool:
    valor = _normalizar_columna(estado)
    if not valor:
        return bool(permitir_vacio)
    return valor in estados_objetivo


def obtener_primer_registro_objetivo(logger):
    """
    Toma el primer registro de la hoja de comparacion cuyo estado cumpla criterio,
    cruza con hoja base por DNI y devuelve los datos para iniciar el flujo.
    """
    url_base = str(os.getenv("CARNET_GSHEET_URL", DEFAULT_GSHEET_URL) or DEFAULT_GSHEET_URL).strip()
    url_compare = str(os.getenv("CARNET_GSHEET_COMPARE_URL", DEFAULT_GSHEET_COMPARE_URL) or "").strip()

    if not url_compare:
        raise Exception("Falta CARNET_GSHEET_COMPARE_URL")

    rows_base, fields_base = _leer_google_sheet_rows_local_preservando_duplicados(url_base, logger, "hoja_base")
    rows_compare, fields_compare = _leer_google_sheet_rows_local_preservando_duplicados(url_compare, logger, "hoja_compare")

    col_base_dni = _resolver_indice_columna(fields_base, ["dni"])
    col_base_departamento = _resolver_indice_columna(
        fields_base,
        [
            "indicar el departamento donde labora o donde postulo",
            "indicar el departamento donde labora o donde postuló",
            "departamento",
        ],
    )

    col_cmp_dni = _resolver_indice_columna(fields_compare, ["dni"])
    col_cmp_estado = _resolver_indice_columna(fields_compare, ["estado_tramite"])
    col_cmp_compania = _resolver_indice_columna(fields_compare, ["compania", "compañia", "empresa"])

    if not col_base_dni or not col_base_departamento:
        raise Exception("La hoja base no contiene DNI y/o departamento")
    if not col_cmp_dni:
        raise Exception("La hoja de comparacion no contiene DNI")

    if col_cmp_estado is None:
        raise Exception("La hoja de comparacion no contiene la columna obligatoria ESTADO_TRAMITE")

    indices_estado_tramite = _indices_columna_exacta(fields_compare, "estado_tramite")
    logger.info(
        "[CARNE_FLOW] ESTADO_TRAMITE indices=%s | seleccionado=%s",
        indices_estado_tramite,
        col_cmp_estado,
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

    logger.info(
        "[CARNE_FLOW] Criterio ESTADO: objetivos=%s | permitir_vacio=%s",
        sorted(estados_objetivo),
        permitir_estado_vacio,
    )

    estados_detectados = Counter()
    for row in rows_compare:
        valor = _normalizar_columna(_valor_fila_por_indice(row, col_cmp_estado))
        estados_detectados[valor or "<vacío>"] += 1
    top_estados = ", ".join(f"{estado}={cantidad}" for estado, cantidad in estados_detectados.most_common(10))
    logger.info("[CARNE_FLOW] Distribución ESTADO (top 10): %s", top_estados or "<sin datos>")

    base_por_dni = {}
    for row in rows_base:
        dni = _valor_fila_por_indice(row, col_base_dni)
        if dni and dni not in base_por_dni:
            base_por_dni[dni] = row

    total = 0
    saltados_sin_dni = 0
    saltados_estado = 0
    saltados_sin_cruce = 0
    max_warn_sin_cruce = max(0, int(str(os.getenv("CARNET_CARNE_FLOW_MAX_WARN_SIN_CRUCE", "40") or "40").strip() or "40"))
    warns_sin_cruce_emitidos = 0
    warns_sin_cruce_suprimidos = 0

    for idx, row in enumerate(rows_compare, start=2):
        total += 1
        dni = _valor_fila_por_indice(row, col_cmp_dni)
        estado = _valor_fila_por_indice(row, col_cmp_estado)

        if not dni:
            saltados_sin_dni += 1
            continue

        if not _estado_comparacion_es_objetivo(estado, estados_objetivo, permitir_estado_vacio):
            saltados_estado += 1
            continue

        base_row = base_por_dni.get(dni)
        if not base_row:
            saltados_sin_cruce += 1
            if max_warn_sin_cruce == 0 or warns_sin_cruce_emitidos < max_warn_sin_cruce:
                logger.warning("[CARNE_FLOW] Fila %s DNI=%s cumple estado pero no existe en hoja base", idx, dni)
                warns_sin_cruce_emitidos += 1
            else:
                warns_sin_cruce_suprimidos += 1
            continue

        departamento = _valor_fila_por_indice(base_row, col_base_departamento)
        compania = _valor_fila_por_indice(row, col_cmp_compania)

        logger.info(
            "[CARNE_FLOW] Registro objetivo: fila=%s | DNI=%s | ESTADO=%s | COMPANIA=%s | DEPARTAMENTO=%s",
            idx,
            dni,
            estado,
            compania,
            departamento,
        )

        logger.info(
            "[CARNE_FLOW] Resumen parcial: total=%s | saltadas_sin_dni=%s | saltadas_estado=%s | saltadas_sin_cruce=%s",
            total,
            saltados_sin_dni,
            saltados_estado,
            saltados_sin_cruce,
        )

        return {
            "row_number": idx,
            "dni": dni,
            "estado": estado,
            "compania": compania,
            "departamento": departamento,
        }

    logger.warning(
        "[CARNE_FLOW] Sin registros objetivo: total=%s | saltadas_sin_dni=%s | saltadas_estado=%s | saltadas_sin_cruce=%s",
        total,
        saltados_sin_dni,
        saltados_estado,
        saltados_sin_cruce,
    )

    if estados_detectados:
        muestras = ", ".join(list(estados_detectados.keys())[:20])
        logger.warning("[CARNE_FLOW] Estados detectados en hoja compare: %s", muestras)
    if warns_sin_cruce_suprimidos > 0:
        logger.warning(
            "[CARNE_FLOW] Warns de sin cruce suprimidos: %s fila(s). Ajuste CARNET_CARNE_FLOW_MAX_WARN_SIN_CRUCE para ampliar detalle",
            warns_sin_cruce_suprimidos,
        )
    return None


def _credenciales_por_grupo(grupo: str) -> dict:
    if grupo == "SELVA":
        return CREDENCIALES_SELVA
    return CREDENCIALES_JV


def _validar_credenciales(credenciales: dict, grupo: str) -> None:
    faltantes = []
    if not str(credenciales.get("numero_documento", "") or "").strip():
        faltantes.append("numero_documento")
    if not str(credenciales.get("usuario", "") or "").strip():
        faltantes.append("usuario")
    if not str(credenciales.get("contrasena", "") or "").strip():
        faltantes.append("contrasena")
    if faltantes:
        raise Exception(f"Credenciales incompletas para grupo {grupo}: {faltantes}")


def _aplicar_sede_crear_solicitud(page, logger, registro: dict) -> str:
    """
    En vista CREAR SOLICITUD selecciona 'Sede de Atención' basado solo en departamento:
    1) mapeo directo departamento->sede sugerida
    2) validación contra opciones del dropdown
    3) fallback geográfico si no existe opción directa
    """
    departamento = str(registro.get("departamento", "") or "").strip()
    sede_sugerida, origen_mapeo = resolver_sede_atencion_desde_departamento(departamento)

    logger.info(
        "[CARNE_FLOW] Abriendo dropdown Sede de Atención para resolver opción | DNI=%s | DEPTO=%s | SUGERIDA=%s",
        registro.get("dni", ""),
        departamento,
        sede_sugerida,
    )
    sede_final, origen_dropdown = resolver_sede_para_dropdown(page, departamento, sede_sugerida)

    seleccionar_sede_atencion(page, sede_final)

    label_actual = ""
    try:
        label_actual = (page.locator(SEL["crear_solicitud_sede_label"]).inner_text() or "").strip()
    except Exception:
        label_actual = ""

    if not label_actual:
        raise Exception("No se pudo confirmar el label de Sede de Atención tras la selección")

    logger.info(
        "[CARNE_FLOW] Sede aplicada en CREAR SOLICITUD | DNI=%s | DEPTO=%s | SUGERIDA=%s | FINAL=%s | LABEL=%s | MAPEO=%s | DROPDOWN=%s",
        registro.get("dni", ""),
        departamento,
        sede_sugerida,
        sede_final,
        label_actual,
        origen_mapeo,
        origen_dropdown,
    )
    return sede_final


def ejecutar_hasta_crear_solicitud(logger, registro: dict | None) -> int:
    force_nav = _as_bool_env("CARNE_FLOW_FORCE_NAV_SIN_REGISTRO", default=False)
    if not registro and not force_nav:
        logger.warning("[CARNE_FLOW] No hay registro objetivo; no se abre navegador")
        return 0

    grupo = "JV"
    if registro:
        grupo = obtener_grupo_ruc(str(registro.get("compania", "") or ""))
        if grupo == "OTRO":
            grupo = "JV"

    credenciales = _credenciales_por_grupo(grupo)
    _validar_credenciales(credenciales, grupo)

    headless = _as_bool_env("CARNET_HEADLESS", default=False)
    login_validation_timeout_ms = int(os.getenv("LOGIN_VALIDATION_TIMEOUT_MS", "12000") or "12000")

    playwright = sync_playwright().start()
    browser = None
    context = None

    try:
        launch_args = _build_launch_args_for_window()
        logger.info("[CARNE_FLOW] Iniciando navegador | grupo=%s | headless=%s", grupo, headless)

        browser = playwright.chromium.launch(headless=headless, slow_mo=0, args=launch_args)
        context = browser.new_context(no_viewport=True, ignore_https_errors=True)
        page = context.new_page()

        page.goto(URL_LOGIN, wait_until="domcontentloaded", timeout=45000)
        esperar_hasta_servicio_disponible(page, URL_LOGIN, espera_segundos=8)

        activar_pestana_autenticacion_tradicional(page)

        page.select_option(SEL["tipo_doc_select"], value=credenciales["tipo_documento_valor"])
        page.wait_for_timeout(250)
        escribir_input_rapido(page, SEL["numero_documento"], credenciales["numero_documento"])
        escribir_input_rapido(page, SEL["usuario"], credenciales["usuario"])
        escribir_input_rapido(page, SEL["clave"], credenciales["contrasena"])

        try:
            captcha_text = solve_captcha_ocr(page, logger)
            escribir_input_rapido(page, SEL["captcha_input"], captcha_text)
            logger.info("[CARNE_FLOW] Captcha resuelto en automatico")
        except Exception as exc:
            run_mode = str(os.getenv("RUN_MODE", "manual") or "manual").strip().lower()
            if run_mode == "scheduled":
                raise
            logger.warning("[CARNE_FLOW] OCR captcha falló: %s", exc)
            logger.warning("[CARNE_FLOW] Completa captcha manualmente en el navegador y pulsa ENTER en consola")
            input()

        page.locator(SEL["ingresar"]).click(timeout=10000)
        ok, msg_error, tiempo = validar_resultado_login_por_ui(page, timeout_ms=max(1000, login_validation_timeout_ms))
        if not ok:
            raise Exception(f"Login fallido: {msg_error}")

        logger.info("[CARNE_FLOW] Login exitoso en %.2fs", tiempo)
        navegar_dssp_carne_crear_solicitud(page, logger)

        vista_ok = validar_vista_crear_solicitud_por_ui(page, timeout_ms=3200)
        if not vista_ok:
            # Fallback práctico: confirmamos la vista por presencia del control objetivo.
            try:
                page.locator(SEL["crear_solicitud_sede_trigger"]).first.wait_for(state="visible", timeout=7000)
                vista_ok = True
                logger.warning(
                    "[CARNE_FLOW] Vista CREAR SOLICITUD no confirmada por validador genérico, "
                    "pero el dropdown de Sede está visible"
                )
            except Exception:
                try:
                    page.locator(SEL["crear_solicitud_sede_label"]).first.wait_for(state="visible", timeout=3000)
                    vista_ok = True
                    logger.warning(
                        "[CARNE_FLOW] Vista CREAR SOLICITUD confirmada por label de Sede (fallback)"
                    )
                except Exception:
                    vista_ok = False

        if not vista_ok:
            raise Exception("No se confirmó la vista CREAR SOLICITUD por UI tras navegación")

        if registro is not None:
            _aplicar_sede_crear_solicitud(page, logger, registro)

        logger.info("[CARNE_FLOW] Vista CREAR SOLICITUD alcanzada | URL=%s", page.url)
        return 0
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


def main() -> int:
    logger = setup_logger("carne_flow", suffix=datetime.now().strftime("%H%M%S"))
    logger.info("[CARNE_FLOW] Inicio")

    registro = obtener_primer_registro_objetivo(logger)
    return ejecutar_hasta_crear_solicitud(logger, registro)


if __name__ == "__main__":
    raise SystemExit(main())
