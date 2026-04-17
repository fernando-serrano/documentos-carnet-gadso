#!/usr/bin/env python3
"""Test de edición de fecha en PDF DJ FUT"""
import sys
from pathlib import Path
from flows.dj_fut_flow.dj_fut_flow import _actualizar_fecha_declaracion

# Usar el primer PDF generado
pdf_path = Path("lotes/lote-dj-fut-16-04-2026-16-51-13/41628670/djfut_41628670.pdf")

if not pdf_path.exists():
    print(f"❌ PDF no encontrado: {pdf_path}")
    sys.exit(1)

print(f"📄 Leyendo PDF: {pdf_path}")
pdf_bytes = pdf_path.read_bytes()
print(f"   Tamaño original: {len(pdf_bytes)} bytes")

# Intentar editar
print("\n🔧 Intentando editar fecha...")
result_bytes, detail, success = _actualizar_fecha_declaracion(pdf_bytes)

print(f"   Éxito: {success}")
print(f"   Detalle: {detail}")
print(f"   Tamaño resultado: {len(result_bytes)} bytes")

if result_bytes != pdf_bytes:
    print(f"   ✅ PDF fue modificado ({len(result_bytes)} vs {len(pdf_bytes)})")
else:
    print(f"   ⚠️  PDF NO fue modificado (mismos bytes)")

# Guardar resultado para inspección
output_path = Path("test_date_edit_output.pdf")
output_path.write_bytes(result_bytes)
print(f"\n💾 Resultado guardado en: {output_path}")
