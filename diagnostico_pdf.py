#!/usr/bin/env python3
"""Diagnóstico: ubicación exacta de números en PDF"""
import sys
from pathlib import Path
import importlib

pdf_path = Path("lotes/lote-dj-fut-16-04-2026-16-51-13/41628670/djfut_41628670.pdf")

if not pdf_path.exists():
    print(f"❌ PDF no encontrado: {pdf_path}")
    sys.exit(1)

fitz = importlib.import_module("fitz")
doc = fitz.open(pdf_path)

print(f"📄 PDF: {pdf_path.name}")
print(f"   Páginas: {len(doc)}\n")

for page_num, page in enumerate(doc):
    print(f"📖 Página {page_num}:")
    print(f"   Dimensiones: {page.rect.width:.0f} x {page.rect.height:.0f}")
    print(f"   Altura total: {page.rect.height}\n")
    
    # Buscar anclajes
    anchors = page.search_for("FECHA DE LA DECLARACION")
    if anchors:
        anchor = anchors[0]
        print(f"   ✅ 'FECHA DE LA DECLARACION' en y0={anchor.y0:.0f}, y1={anchor.y1:.0f}")
    else:
        print(f"   ❌ No encontró 'FECHA DE LA DECLARACION'")
    
    anchors2 = page.search_for("PARA PERSONA NATURAL")
    if anchors2:
        anchor2 = anchors2[0]
        print(f"   ✅ 'PARA PERSONA NATURAL' en y0={anchor2.y0:.0f}, y1={anchor2.y1:.0f}\n")
    else:
        print(f"   ❌ No encontró 'PARA PERSONA NATURAL'\n")
    
    # Mostrar todos los números y su contexto
    words = page.get_text("words") or []
    print(f"   Todos los números en el PDF:")
    print(f"   {'Texto':<8} {'x0':<6} {'y0':<6} {'x1':<6} {'y1':<6}")
    print(f"   {'-'*32}")
    
    for word in words:
        x0, y0, x1, y1, texto = word[:5]
        if not (len(texto) <= 4 and texto.isdigit()):
            continue
        print(f"   {texto:<8} {x0:<6.0f} {y0:<6.0f} {x1:<6.0f} {y1:<6.0f}")
    
    print(f"\n   Porcentaje de altura (0-100):")
    for word in words:
        x0, y0, x1, y1, texto = word[:5]
        if not (len(texto) <= 4 and texto.isdigit()):
            continue
        pct = (y0 / page.rect.height) * 100
        print(f"     {texto}: {pct:.1f}%")
    
    print()

doc.close()

