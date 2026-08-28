from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import flows.common.sheets as sheets
from flows.common import workbook_lock

_FIELDNAMES = [
    "DNI",
    "ESTADO CERTIFICADO MEDICO",
    "ESTADO FOTO CARNÉ",
    "ESTADO DJ FUT",
    "ESTADO FIRMA",
    "RESPONSABLE",
    "FECHA TRAMITE",
]


def _fila(dni: str, **estados: str) -> dict:
    row = {campo: "" for campo in _FIELDNAMES}
    row["DNI"] = dni
    row.update(estados)
    return row


def _fake_read_sheet_rows(rows_por_url: dict[str, list[dict]]):
    def _fake(sheet_url, **_settings):
        return rows_por_url.get(sheet_url, []), _FIELDNAMES
    return _fake


def _con_lecturas(rows_por_url, fn):
    original = sheets.read_sheet_rows
    sheets.read_sheet_rows = _fake_read_sheet_rows(rows_por_url)
    try:
        fn()
    finally:
        sheets.read_sheet_rows = original


def test_hay_en_proceso_false_cuando_todo_resuelto_o_pendiente():
    rows = {
        "https://sheet-a": [
            _fila("11111111"),  # todo vacio, pendiente normal
            _fila("22222222", **{"ESTADO FOTO CARNÉ": "DESCARGADO"}),  # ya resuelto
        ]
    }

    def _run():
        assert workbook_lock.hay_en_proceso("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_en_proceso_true_si_alguna_columna_esta_en_proceso():
    rows = {
        "https://sheet-a": [
            _fila("11111111", **{"ESTADO DJ FUT": "EN PROCESO W2"}),
        ]
    }

    def _run():
        assert workbook_lock.hay_en_proceso("https://sheet-a") is True

    _con_lecturas(rows, _run)


def test_hay_en_proceso_false_si_url_vacia():
    assert workbook_lock.hay_en_proceso("") is False


def test_hay_en_proceso_false_si_falla_la_lectura():
    def _lectura_que_falla(sheet_url, **_settings):
        raise RuntimeError("timeout")

    original = sheets.read_sheet_rows
    sheets.read_sheet_rows = _lectura_que_falla
    try:
        assert workbook_lock.hay_en_proceso("https://sheet-a") is False
    finally:
        sheets.read_sheet_rows = original


def test_decidir_lane_usa_a_si_no_esta_en_proceso():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {"https://sheet-a": [_fila("11111111")]}

    def _run():
        assert workbook_lock.decidir_lane() == "A"

    _con_lecturas(rows, _run)


def test_decidir_lane_usa_b_si_a_esta_en_proceso_aunque_b_no_tenga_dnis():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO W1"})],
        "https://sheet-b": [],  # B vacia -- igual se elige, sin punto medio
    }

    def _run():
        assert workbook_lock.decidir_lane() == "B"

    _con_lecturas(rows, _run)


def test_decidir_lane_usa_b_aunque_a_tenga_trabajo_pendiente_real():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [
            _fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO W1"}),
            _fila("22222222"),  # fila con trabajo pendiente real (todo vacio)
        ],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "B"

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_a_en_proceso_y_b_no_configurada():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ.pop("GALENIUS_QUEUE_SHEET_URL_B", None)
    rows = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO W1"})]}

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


def _con_api(rows_por_url=None, excepcion=None, fn=None):
    original_api = sheets.read_sheet_values_api

    def _fake_api(sheet_url, credentials_path):
        if excepcion is not None:
            raise excepcion
        return (rows_por_url or {}).get(sheet_url, []), _FIELDNAMES

    sheets.read_sheet_values_api = _fake_api
    try:
        fn()
    finally:
        sheets.read_sheet_values_api = original_api


def test_hay_en_proceso_prefiere_api_cuando_hay_credenciales():
    os.environ["DRIVE_CREDENTIALS_JSON"] = "C:/fake/creds.json"
    try:
        rows_api = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})]}
        rows_csv = {"https://sheet-a": [_fila("11111111")]}  # CSV "desactualizado": diria libre

        def _run():
            assert workbook_lock.hay_en_proceso("https://sheet-a") is True

        _con_api(rows_api, fn=lambda: _con_lecturas(rows_csv, _run))
    finally:
        os.environ.pop("DRIVE_CREDENTIALS_JSON", None)


def test_hay_en_proceso_cae_a_csv_si_la_api_falla():
    os.environ["DRIVE_CREDENTIALS_JSON"] = "C:/fake/creds.json"
    try:
        rows_csv = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})]}

        def _run():
            assert workbook_lock.hay_en_proceso("https://sheet-a") is True

        _con_api(excepcion=RuntimeError("API no disponible"), fn=lambda: _con_lecturas(rows_csv, _run))
    finally:
        os.environ.pop("DRIVE_CREDENTIALS_JSON", None)


def test_hay_en_proceso_usa_csv_si_no_hay_credenciales():
    os.environ.pop("DRIVE_CREDENTIALS_JSON", None)
    rows_csv = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})]}

    def _api_no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("read_sheet_values_api no deberia invocarse sin credenciales")

    def _run():
        assert workbook_lock.hay_en_proceso("https://sheet-a") is True

    original_api = sheets.read_sheet_values_api
    sheets.read_sheet_values_api = _api_no_deberia_llamarse
    try:
        _con_lecturas(rows_csv, _run)
    finally:
        sheets.read_sheet_values_api = original_api


if __name__ == "__main__":
    test_hay_en_proceso_false_cuando_todo_resuelto_o_pendiente()
    test_hay_en_proceso_true_si_alguna_columna_esta_en_proceso()
    test_hay_en_proceso_false_si_url_vacia()
    test_hay_en_proceso_false_si_falla_la_lectura()
    test_decidir_lane_usa_a_si_no_esta_en_proceso()
    test_decidir_lane_usa_b_si_a_esta_en_proceso_aunque_b_no_tenga_dnis()
    test_decidir_lane_usa_b_aunque_a_tenga_trabajo_pendiente_real()
    test_decidir_lane_none_si_a_en_proceso_y_b_no_configurada()
    test_hay_en_proceso_prefiere_api_cuando_hay_credenciales()
    test_hay_en_proceso_cae_a_csv_si_la_api_falla()
    test_hay_en_proceso_usa_csv_si_no_hay_credenciales()
    print("OK - test_workbook_lock")
