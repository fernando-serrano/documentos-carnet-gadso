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


def test_hay_registros_en_proceso_false_cuando_todo_pendiente():
    rows = {"https://sheet-a": [_fila("11111111"), _fila("22222222")]}

    def _run():
        assert workbook_lock.hay_registros_en_proceso("https://sheet-a") is False

    _con_lecturas(rows, _run)


def test_hay_registros_en_proceso_true_si_alguna_columna_esta_en_proceso():
    rows = {
        "https://sheet-a": [
            _fila("11111111"),
            _fila("22222222", **{"ESTADO DJ FUT": "EN PROCESO W2"}),
        ]
    }

    def _run():
        assert workbook_lock.hay_registros_en_proceso("https://sheet-a") is True

    _con_lecturas(rows, _run)


def test_hay_registros_en_proceso_true_si_url_vacia():
    assert workbook_lock.hay_registros_en_proceso("") is True


def test_decidir_lane_usa_a_si_esta_libre():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111")],
        "https://sheet-b": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "A"

    _con_lecturas(rows, _run)


def test_decidir_lane_usa_b_si_a_ocupada_y_b_libre():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
        "https://sheet-b": [_fila("11111111")],
    }

    def _run():
        assert workbook_lock.decidir_lane() == "B"

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_ambas_ocupadas():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ["GALENIUS_QUEUE_SHEET_URL_B"] = "https://sheet-b"
    rows = {
        "https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})],
        "https://sheet-b": [_fila("22222222", **{"ESTADO DJ FUT": "EN PROCESO W1"})],
    }

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


def test_decidir_lane_none_si_a_ocupada_y_b_no_configurada():
    os.environ["GALENIUS_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ.pop("GALENIUS_QUEUE_SHEET_URL_B", None)
    rows = {"https://sheet-a": [_fila("11111111", **{"ESTADO FOTO CARNÉ": "EN PROCESO"})]}

    def _run():
        assert workbook_lock.decidir_lane() is None

    _con_lecturas(rows, _run)


if __name__ == "__main__":
    test_hay_registros_en_proceso_false_cuando_todo_pendiente()
    test_hay_registros_en_proceso_true_si_alguna_columna_esta_en_proceso()
    test_hay_registros_en_proceso_true_si_url_vacia()
    test_decidir_lane_usa_a_si_esta_libre()
    test_decidir_lane_usa_b_si_a_ocupada_y_b_libre()
    test_decidir_lane_none_si_ambas_ocupadas()
    test_decidir_lane_none_si_a_ocupada_y_b_no_configurada()
    print("OK - test_workbook_lock")
