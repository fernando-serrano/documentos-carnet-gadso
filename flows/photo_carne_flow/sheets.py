import csv
import importlib
import io
import os
import re
import time
import unicodedata
from http.client import IncompleteRead
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


def _normalizar_columna(texto: str) -> str:
    raw = str(texto or "").strip().lower()
    if not raw:
        return ""
    raw = unicodedata.normalize("NFD", raw)
    raw = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _extract_sheet_id_from_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise ValueError("URL de Google Sheet vacía")
    parsed = urlparse(raw)
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9-_]+)", parsed.path or "")
    if not match:
        raise ValueError("No se pudo extraer el ID del Google Sheet desde la URL")
    return match.group(1)


def _extract_gid_from_url(sheet_url: str) -> str:
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


def _build_google_sheet_csv_url(sheet_url: str) -> str:
    raw = str(sheet_url or "").strip()
    if not raw:
        raise ValueError("URL de Google Sheets vacía")

    sheet_id = _extract_sheet_id_from_url(raw)
    gid = _extract_gid_from_url(raw)
    ts = int(time.time() * 1000)
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}&t={ts}"


def _download_text(url: str, timeout_sec: int, retries: int, retry_base_ms: int) -> str:
    last_exc = None
    for intento in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; foto-carne-bot/1.0)"})
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


def read_google_sheet_rows(sheet_url: str) -> tuple[list[dict], list[str]]:
    csv_url = _build_google_sheet_csv_url(sheet_url)
    retries = max(1, int(str(os.getenv("FOTO_CARNE_GSHEET_READ_RETRIES", "4") or "4").strip()))
    timeout_sec = max(8, int(str(os.getenv("FOTO_CARNE_GSHEET_TIMEOUT_SEC", "25") or "25").strip()))
    retry_base_ms = max(200, int(str(os.getenv("FOTO_CARNE_GSHEET_RETRY_BASE_MS", "600") or "600").strip()))

    text = _download_text(csv_url, timeout_sec=timeout_sec, retries=retries, retry_base_ms=retry_base_ms)
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for index, row in enumerate(reader, start=2):
        clean_row = {str(key or ""): str(value or "").strip() for key, value in row.items()}
        clean_row["__row_number__"] = index
        rows.append(clean_row)
    return rows, list(reader.fieldnames or [])


def _google_sheets_service():
    try:
        service_account = importlib.import_module("google.oauth2.service_account")
        google_build = importlib.import_module("googleapiclient.discovery").build
    except Exception as exc:
        raise RuntimeError(
            "Faltan dependencias de Google Sheets API. Instala google-api-python-client y google-auth"
        ) from exc

    credentials_path = str(os.getenv("FOTO_CARNE_SHEETS_CREDENTIALS_JSON", os.getenv("FOTO_CARNE_DRIVE_CREDENTIALS_JSON", os.getenv("DRIVE_CREDENTIALS_JSON", ""))) or "").strip()
    if not credentials_path:
        raise RuntimeError("Falta FOTO_CARNE_SHEETS_CREDENTIALS_JSON o FOTO_CARNE_DRIVE_CREDENTIALS_JSON en .env")

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


def _resolver_columna(fieldnames: list[str], candidatos: list[str]) -> str:
    normalizados = {_normalizar_columna(name): name for name in fieldnames}
    for candidato in candidatos:
        candidato_norm = _normalizar_columna(candidato)
        if candidato_norm in normalizados:
            return normalizados[candidato_norm]
    return ""


def update_sheet_row(sheet_url: str, row_number: int, updates: dict[str, str], fieldnames: list[str] | None = None, sheet_title: str | None = None) -> None:
    service = _google_sheets_service()
    spreadsheet_id = _extract_sheet_id_from_url(sheet_url)
    gid = _extract_gid_from_url(sheet_url)

    if sheet_title is None:
        response = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title))",
        ).execute()
        target_gid = int(str(gid or "0").strip() or "0")
        for sheet in response.get("sheets", []) or []:
            props = sheet.get("properties", {}) or {}
            if int(props.get("sheetId", -1)) == target_gid:
                sheet_title = str(props.get("title", "")).strip()
                break
        if not sheet_title:
            raise RuntimeError(f"No se encontró pestaña con gid={gid} en el spreadsheet")

    if fieldnames is None:
        _, fieldnames = read_google_sheet_rows(sheet_url)

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
        safe_sheet_title = str(sheet_title or "").replace("'", "''")
        data.append({"range": f"'{safe_sheet_title}'!{column_a1}{row_number}", "values": [[str(value or "")]]})

    if not data:
        return

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()


def resolve_sheet_columns(fieldnames: list[str]) -> dict[str, str]:
    esquema = [
        ("dni", ["dni"]),
        ("estado_foto_carne", ["estado foto carné", "estado foto carne"]),
        ("observacion_foto_carne", ["observacion foto carné", "observacion foto carne"]),
        ("estado_dj_fut", ["estado dj fut"]),
        ("observacion_dj_fut", ["observacion dj fut"]),
        ("responsable", ["responsable"]),
        ("fecha_tramite", ["fecha tramite", "fecha trámite"]),
    ]
    return {nombre: _resolver_columna(fieldnames, candidatos) for nombre, candidatos in esquema}
