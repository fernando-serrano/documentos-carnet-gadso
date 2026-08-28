from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_dj_fut
import run_firma_digital
import run_foto_carne


def _fila(row_number: int, dni: str, estado_col: str, estado: str) -> dict:
    return {"__row_number__": row_number, "DNI": dni, estado_col: estado}


def test_foto_carne_salta_filas_ya_resueltas():
    cfg = run_foto_carne.load_foto_carne_config()
    fieldnames = ["DNI", "ESTADO FOTO CARNÉ"]
    queue_rows = [
        _fila(2, "11111111", "ESTADO FOTO CARNÉ", ""),
        _fila(3, "22222222", "ESTADO FOTO CARNÉ", cfg.estado_descargado),
        _fila(4, "33333333", "ESTADO FOTO CARNÉ", cfg.estado_error),
        _fila(5, "44444444", "ESTADO FOTO CARNÉ", cfg.estado_sin_registros),
        _fila(6, "55555555", "ESTADO FOTO CARNÉ", cfg.estado_revision_manual),
        _fila(7, "66666666", "ESTADO FOTO CARNÉ", "EN PROCESO W1"),
    ]

    items = run_foto_carne._construir_queue_items_pendientes(queue_rows, fieldnames, cfg)

    assert ("11111111", 2) in items
    assert ("66666666", 7) in items
    assert ("22222222", 3) not in [(d, r) for d, r in items]
    assert len(items) == 2


def test_dj_fut_salta_filas_ya_resueltas():
    cfg = run_dj_fut.load_dj_fut_config()
    fieldnames = ["DNI", "ESTADO DJ FUT"]
    queue_rows = [
        _fila(2, "11111111", "ESTADO DJ FUT", ""),
        _fila(3, "22222222", "ESTADO DJ FUT", cfg.estado_descargado),
        _fila(4, "33333333", "ESTADO DJ FUT", cfg.estado_error),
        _fila(5, "44444444", "ESTADO DJ FUT", cfg.estado_sin_registros),
    ]

    items = run_dj_fut._construir_queue_items_pendientes(queue_rows, fieldnames, cfg)

    assert items == [("11111111", 2)]


def test_firma_digital_salta_filas_ya_resueltas():
    cfg = run_firma_digital.load_firma_digital_config()
    fieldnames = ["DNI", "ESTADO FIRMA"]
    queue_rows = [
        _fila(2, "11111111", "ESTADO FIRMA", ""),
        _fila(3, "22222222", "ESTADO FIRMA", cfg.estado_procesado),
        _fila(4, "33333333", "ESTADO FIRMA", cfg.estado_cargado),
        _fila(5, "44444444", "ESTADO FIRMA", cfg.estado_revision_manual),
        _fila(6, "55555555", "ESTADO FIRMA", cfg.estado_error),
        _fila(7, "66666666", "ESTADO FIRMA", cfg.estado_sin_registros),
    ]

    items = run_firma_digital._construir_queue_items_pendientes(queue_rows, fieldnames, cfg)

    assert items == [("11111111", 2)]


if __name__ == "__main__":
    test_foto_carne_salta_filas_ya_resueltas()
    test_dj_fut_salta_filas_ya_resueltas()
    test_firma_digital_salta_filas_ya_resueltas()
    print("OK - test_queue_filtro_estado")
