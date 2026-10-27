from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from googleapiclient.errors import HttpError
from httplib2 import Response

from flows.common import sheets


def _http_error(status: int) -> HttpError:
    return HttpError(Response({"status": status}), b"quota")


class _RequestFalso:
    def __init__(self, fallos: list[int]):
        self.fallos = list(fallos)
        self.llamadas = 0

    def execute(self):
        self.llamadas += 1
        if self.fallos:
            raise _http_error(self.fallos.pop(0))
        return {"ok": True}


def test_reintenta_429_y_luego_ok():
    req = _RequestFalso([429, 429, 503])
    assert sheets.execute_con_reintento(req, espera_base_s=0) == {"ok": True}
    assert req.llamadas == 4


def test_agota_reintentos_y_propaga_429():
    req = _RequestFalso([429] * 10)
    try:
        sheets.execute_con_reintento(req, reintentos=3, espera_base_s=0)
    except HttpError as exc:
        assert exc.resp.status == 429
    else:
        raise AssertionError("debia propagar el 429")
    assert req.llamadas == 3


def test_no_reintenta_errores_no_transitorios():
    req = _RequestFalso([403])
    try:
        sheets.execute_con_reintento(req, espera_base_s=0)
    except HttpError as exc:
        assert exc.resp.status == 403
    else:
        raise AssertionError("debia propagar el 403")
    assert req.llamadas == 1


if __name__ == "__main__":
    test_reintenta_429_y_luego_ok()
    test_agota_reintentos_y_propaga_429()
    test_no_reintenta_errores_no_transitorios()
    print("OK - test_sheets_reintento_429")
