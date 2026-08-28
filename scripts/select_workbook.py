"""CLI: decide que lane (A/B) de la cola compartida BOT DOCUMENTOS usar en esta corrida.

Imprime 'A' o 'B' a stdout y sale con codigo 0 si esa lane tiene DNIs
pendientes (fila con DNI y alguna columna de estado todavia vacia). Si
ninguna de las 2 lanes tiene pendientes (o no se pudieron leer), no imprime
nada util y sale con codigo 3 -- no es necesariamente un error, puede ser
que simplemente no haya nada que procesar hoy. No escribe en ninguna hoja.

Uso (desde run.bat):
    for /f "delims=" %%W in ('".venv\\Scripts\\python.exe" scripts\\select_workbook.py') do set "ACTIVE_WORKBOOK=%%W"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from flows.common.workbook_lock import decidir_lane


def main() -> int:
    load_dotenv()
    lane = decidir_lane()
    if lane is None:
        print("[SELECT_WORKBOOK] Ninguna lane tiene DNIs pendientes (o no se pudieron leer).", file=sys.stderr)
        return 3

    print(lane)
    return 0


if __name__ == "__main__":
    sys.exit(main())
