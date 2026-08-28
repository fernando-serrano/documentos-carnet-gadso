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


def test_hay_pendientes_true_si_alguna_columna_de_estado_esta_vacia():
    rows = {
        "https://sheet-a": [
            _fila("11111111", **{"ESTADO FOTO CARNÉ": "DESCARGADO"}),  # DJ FUT/Firma/Galenius siguen vacios
        ]
    }

    def _run():
        assert workbook_lock.hay_pendientes("https://sheet-a") is True

    _con_lecturas(rows, _run)


def test_hay_pendientes_false_si_las_4_columnas_ya_tienen_algun_valor():
    rows = {
        "https://sheet-a": [
            _fila(
                "11111111",
                **{
                    "ESTADO CERTIFICADO MEDICO": "DESCARGADO",
                    "ESTADO FOTO CARNÉ": "ERROR",
                    "ESTADO DJ FUT": "EN PROCESO W2",
                    "ESTADO FIRMA": "DESCARGADO",
                },
            ),
        ]
    }

    def _run():
        assert workbook_lock.hay_pendientes("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_pendientes_false_si_no_hay_filas_con_dni():
    rows = {"https://sheet-a": []}

    def _run():
        assert workbook_lock.hay_pendientes("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_pendientes_ignora_filas_sin_dni():
    rows = {"https://sheet-a": [_fila("", **{"ESTADO FOTO CARNÉ": ""})]}

    def _run():
        assert workbook_lock.hay_pendientes("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_pendientes_false_si_url_vacia():
    assert workbook_lock.hay_pendientes("") is False


def test_hay_pendientes_false_si_falla_la_lectura():
    def _lectura_que_falla(sheet_url, **_settings):
        raise RuntimeError("timeout")

    original = sheets.read_sheet_rows
    sheets.read_sheet_rows = _lectura_que_falla
    try:
        assert workbook_lock.hay_pendientes("https://sheet-a") is False
    finally:
        sheets.read_sheet_rows = original


def test_decidir_lane_usa_a_si_tiene_pendientes():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111")],  # todo vacio -> pendiente
        "https://sheet-b": [_fila("22222222")],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "A"

    _con_lecturas(rows, _run)


def test_decidir_lane_usa_b_si_a_no_tiene_pendientes_y_b_si():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [
            _fila(
                "11111111",
                **{
                    "ESTADO CERTIFICADO MEDICO": "DESCARGADO",
                    "ESTADO FOTO CARNÉ": "DESCARGADO",
                    "ESTADO DJ FUT": "DESCARGADO",
                    "ESTADO FIRMA": "DESCARGADO",
                },
            ),
        ],
        "https://sheet-b": [_fila("22222222")],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "B"

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_ninguna_tiene_pendientes():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    estados_completos = {
        "ESTADO CERTIFICADO MEDICO": "DESCARGADO",
        "ESTADO FOTO CARNÉ": "DESCARGADO",
        "ESTADO DJ FUT": "DESCARGADO",
        "ESTADO FIRMA": "DESCARGADO",
    }
    rows = {
        "https://sheet-a": [_fila("11111111", **estados_completos)],
        "https://sheet-b": [],
    }

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_b_no_esta_configurada_y_a_no_tiene_pendientes():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ.pop("GALENIUS_QUEUE_SHEET_URL_B", None)
    estados_completos = {
        "ESTADO CERTIFICADO MEDICO": "DESCARGADO",
        "ESTADO FOTO CARNÉ": "DESCARGADO",
        "ESTADO DJ FUT": "DESCARGADO",
        "ESTADO FIRMA": "DESCARGADO",
    }
    rows = {"https://sheet-a": [_fila("11111111", **estados_completos)]}

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


def test_hay_pendientes_prefiere_api_cuando_hay_credenciales():
    os.environ["DRIVE_CREDENTIALS_JSON"] = "C:/fake/creds.json"
    try:
        rows_api = {"https://sheet-a": [_fila("11111111")]}  # API: pendiente
        estados_completos = {
            "ESTADO CERTIFICADO MEDICO": "DESCARGADO",
            "ESTADO FOTO CARNÉ": "DESCARGADO",
            "ESTADO DJ FUT": "DESCARGADO",
            "ESTADO FIRMA": "DESCARGADO",
        }
        rows_csv = {"https://sheet-a": [_fila("11111111", **estados_completos)]}  # CSV "desactualizado": diria sin pendientes

        def _run():
            assert workbook_lock.hay_pendientes("https://sheet-a") is True

        _con_api(rows_api, fn=lambda: _con_lecturas(rows_csv, _run))
    finally:
        os.environ.pop("DRIVE_CREDENTIALS_JSON", None)


def test_hay_pendientes_cae_a_csv_si_la_api_falla():
    os.environ["DRIVE_CREDENTIALS_JSON"] = "C:/fake/creds.json"
    try:
        rows_csv = {"https://sheet-a": [_fila("11111111")]}

        def _run():
            assert workbook_lock.hay_pendientes("https://sheet-a") is True

        _con_api(excepcion=RuntimeError("API no disponible"), fn=lambda: _con_lecturas(rows_csv, _run))
    finally:
        os.environ.pop("DRIVE_CREDENTIALS_JSON", None)


def test_hay_pendientes_usa_csv_si_no_hay_credenciales():
    os.environ.pop("DRIVE_CREDENTIALS_JSON", None)
    rows_csv = {"https://sheet-a": [_fila("11111111")]}

    def _api_no_deberia_llamarse(*_args, **_kwargs):
        raise AssertionError("read_sheet_values_api no deberia invocarse sin credenciales")

    def _run():
        assert workbook_lock.hay_pendientes("https://sheet-a") is True

    original_api = sheets.read_sheet_values_api
    sheets.read_sheet_values_api = _api_no_deberia_llamarse
    try:
        _con_lecturas(rows_csv, _run)
    finally:
        sheets.read_sheet_values_api = original_api


if __name__ == "__main__":
    test_hay_pendientes_true_si_alguna_columna_de_estado_esta_vacia()
    test_hay_pendientes_false_si_las_4_columnas_ya_tienen_algun_valor()
    test_hay_pendientes_false_si_no_hay_filas_con_dni()
    test_hay_pendientes_ignora_filas_sin_dni()
    test_hay_pendientes_false_si_url_vacia()
    test_hay_pendientes_false_si_falla_la_lectura()
    test_decidir_lane_usa_a_si_tiene_pendientes()
    test_decidir_lane_usa_b_si_a_no_tiene_pendientes_y_b_si()
    test_decidir_lane_none_si_ninguna_tiene_pendientes()
    test_decidir_lane_none_si_b_no_esta_configurada_y_a_no_tiene_pendientes()
    test_hay_pendientes_prefiere_api_cuando_hay_credenciales()
    test_hay_pendientes_cae_a_csv_si_la_api_falla()
    test_hay_pendientes_usa_csv_si_no_hay_credenciales()
    print("OK - test_workbook_lock")
