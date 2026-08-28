from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# El .env real de este proyecto define explicitamente la URL de cola en los
# 4 flujos (igual a GALENIUS_QUEUE_SHEET_URL) en vez de dejarlas vacias, asi
# que `load_dotenv()` (que corre al importar cada run_*.py) NO las
# sobreescribe si ya estan seteadas aqui (override=False por defecto).
for _prefix in ("GALENIUS", "FOTO_CARNE", "DJ_FUT", "FIRMA_DIGITAL"):
    os.environ[f"{_prefix}_QUEUE_SHEET_URL"] = "https://sheet-a"
    os.environ[f"{_prefix}_QUEUE_SHEET_URL_B"] = "https://sheet-b"

import run_dj_fut
import run_firma_digital
import run_foto_carne
from flows.galenius_flow.config import load_galenius_config


def test_lane_a_por_defecto():
    os.environ.pop("ACTIVE_WORKBOOK", None)
    assert load_galenius_config().queue_sheet_url == "https://sheet-a"
    assert run_foto_carne.load_foto_carne_config().queue_sheet_url == "https://sheet-a"
    assert run_dj_fut.load_dj_fut_config().queue_sheet_url == "https://sheet-a"
    assert run_firma_digital.load_firma_digital_config().queue_sheet_url == "https://sheet-a"


def test_lane_b_cuando_active_workbook_es_b():
    os.environ["ACTIVE_WORKBOOK"] = "B"
    try:
        assert load_galenius_config().queue_sheet_url == "https://sheet-b"
        assert run_foto_carne.load_foto_carne_config().queue_sheet_url == "https://sheet-b"
        assert run_dj_fut.load_dj_fut_config().queue_sheet_url == "https://sheet-b"
        assert run_firma_digital.load_firma_digital_config().queue_sheet_url == "https://sheet-b"
    finally:
        os.environ.pop("ACTIVE_WORKBOOK", None)


def test_source_sheet_no_cambia_con_la_lane():
    os.environ["FOTO_CARNE_SOURCE_SHEET_URL"] = "https://source-foto"
    os.environ["ACTIVE_WORKBOOK"] = "B"
    try:
        assert run_foto_carne.load_foto_carne_config().source_sheet_url == "https://source-foto"
    finally:
        os.environ.pop("ACTIVE_WORKBOOK", None)
        os.environ.pop("FOTO_CARNE_SOURCE_SHEET_URL", None)


if __name__ == "__main__":
    test_lane_a_por_defecto()
    test_lane_b_cuando_active_workbook_es_b()
    test_source_sheet_no_cambia_con_la_lane()
    print("OK - test_active_workbook_queue_urls")
