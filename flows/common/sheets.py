"""Núcleo compartido para la integración con Google Sheets.

Implementación canónica unificada de los antiguos ``flows/*_flow/sheets.py``
(la base proviene de ``galenius_flow/sheets.py``, la más completa y correcta
en normalización Unicode).

Las funciones aquí son agnósticas del flujo: reciben por parámetro el
User-Agent, los ajustes de reintento de lectura y la ruta de credenciales.
Cada subflujo expone un wrapper delgado (``read_google_sheet_rows`` /
``update_sheet_row``) que fija esos valores con sus propias variables de
entorno, de modo que el comportamiento previo se preserva exactamente.
"""

import csv
import importlib
import io
import re
import time
import unicodedata
from http.client import IncompleteRead
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


def normalizar_columna(texto: str) -> str:
    raw = str(texto or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def extract_sheet_id_from_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise ValueError("URL de Google Sheet vacía")
    parsed = urlparse(raw)
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path or "")
    if not match:
        raise ValueError("No se pudo extraer el ID del Google Sheet desde la URL")
    return match.group(1)


def extract_gid_from_url(sheet_url: str) -> str:
    parsed = urlparse(str(sheet_url or "").strip())
    gid = None
    query = parse_qs(parsed.query or "")
    if query.get("gid"):
        gid = query.get("gid")[0]
    if not gid and parsed.fragment:
        frag = parse_qs(parsed.fragment)
        if frag.get("gid"):
            gid = frag.get("gid")[0]
        elif "gid=" in parsed.fragment:
            gid = parsed.fragment.split("gid=", 1)[1].split("&", 1)[0]
    return str(gid or "0").strip() or "0"


def build_google_sheet_csv_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise ValueError("URL de Google Sheets vacía")

    sheet_id = extract_sheet_id_from_url(raw)
    gid = extract_gid_from_url(raw)
    ts = int(time.time() * 1000)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}&t={ts}"


def download_text(url: str, timeout_sec: int, retries: int, retry_base_ms: int, user_agent: str) -> str:
    last_exc = None
    for intento in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": user_agent})
            with urlopen(req, timeout=timeout_sec) as resp:
                content = resp.read()
            return content.decode("utf-8-sig", errors="replace")
        except (IncompleteRead, TimeoutError, OSError) as exc:
            last_exc = exc
            if intento >= retries:
                break
            wait_ms = min(8000, retry_base_ms * (2 ** (intento - 1)))
            time.sleep(wait_ms / 1000.0)
    raise RuntimeError(f"No se pudo leer la hoja remota: {last_exc}") from last_exc


def read_sheet_rows(
    sheet_url: str,
    *,
    user_agent: str,
    retries: int,
    timeout_sec: int,
    retry_base_ms: int,
) -> tuple[list[dict], list[str]]:
    csv_url = build_google_sheet_csv_url(sheet_url)
    text = download_text(
        csv_url,
        timeout_sec=timeout_sec,
        retries=retries,
        retry_base_ms=retry_base_ms,
        user_agent=user_agent,
    )
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader, start=2):
        clean_row = {str(key or ""): str(value or "").strip() for key, value in row.items()}
        clean_row["__row_number__"] = index
        rows.append(clean_row)
    return rows, list(reader.fieldnames or [])


def build_sheets_service(credentials_path: str):
    try:
        service_account = importlib.import_module("google.oauth2.service_account")
        google_build = importlib.import_module("googleapiclient.discovery").build
    except Exception as exc:
        raise RuntimeError(
            "Faltan dependencias de Google Sheets API. Instala google-api-python-client y google-auth"
        ) from exc

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = service_account.Credentials.from_service_account_file(credentials_path, scopes=scopes)
    return google_build("sheets", "v4", credentials=creds, cache_discovery=False)


def sheet_col_to_a1(index_zero_based: int) -> str:
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


def resolver_columna(fieldnames: list[str], candidatos: list[str]) -> str:
    normalizados = {normalizar_columna(name): name for name in fieldnames}
    for candidato in candidatos:
        candidato_norm = normalizar_columna(candidato)
        if candidato_norm in normalizados:
            return normalizados[candidato_norm]
    return ""


def sheet_title_from_gid(service, spreadsheet_id: str, gid: str) -> str:
    response = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    target_gid = int(str(gid or "0").strip() or "0")
    for sheet in response.get("sheets", []) or []:
        props = sheet.get("properties", {}) or {}
        if int(props.get("sheetId", -1)) == target_gid:
            return str(props.get("title", "")).strip()
    raise RuntimeError(f"No se encontró pestaña con gid={gid} en el spreadsheet")


def update_sheet_cells_by_row(service, spreadsheet_id: str, sheet_title: str, row_number: int, updates: dict[str, str], fieldnames: list[str]) -> None:
    data = []
    for field_name, value in updates.items():
        column_index = None
        for idx, candidate in enumerate(fieldnames):
            if normalizar_columna(candidate) == normalizar_columna(field_name):
                column_index = idx
                break
        if column_index is None:
            continue
        column_a1 = sheet_col_to_a1(column_index)
        safe_sheet_title = str(sheet_title or "").replace("'", "''")
        data.append(
            {
                "range": f"'{safe_sheet_title}'!{column_a1}{row_number}",
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


def update_sheet_row(
    sheet_url: str,
    row_number: int,
    updates: dict[str, str],
    *,
    credentials_path: str,
    read_sheet_settings: dict,
    fieldnames: list[str] | None = None,
    sheet_title: str | None = None,
) -> None:
    """Actualiza celdas de una fila por nombre de columna.

    ``read_sheet_settings`` debe contener ``user_agent``, ``retries``,
    ``timeout_sec`` y ``retry_base_ms`` para resolver ``fieldnames`` cuando no
    se pasan (idéntico a leer la hoja con la config del flujo).
    """
    service = build_sheets_service(credentials_path)
    spreadsheet_id = extract_sheet_id_from_url(sheet_url)
    gid = extract_gid_from_url(sheet_url)
    resolved_title = sheet_title or sheet_title_from_gid(service, spreadsheet_id, gid)
    if fieldnames is None:
        _, fieldnames = read_sheet_rows(sheet_url, **read_sheet_settings)
    update_sheet_cells_by_row(service, spreadsheet_id, resolved_title, row_number, updates, fieldnames)
