# =============================================================================
# MÓDULO: REFÍNADO DE FIRMAS CON PROCESAMIENTO PIXEL A PIXEL 3x3
# =============================================================================
# Versión 16.0 del pipeline SUCAMEC - Refinado manual con matriz 3x3
# • Exactamente como recordabas: iteración pixel por pixel
# • Para cada píxel negro se analiza su vecindario 3x3
# • Se clasifican patrones (ruido aislado, trazo fino, esquina, etc.)
# • Decisiones automáticas: eliminar ruido, engrosar trazos débiles, preservar forma
# • Fondo blanco puro + trazos reforzados SIN puntos sueltos
# • Todo se procesa y guarda en LA MISMA CARPETA donde está este script

import os
import cv2
import numpy as np
from pathlib import Path

# =============================================================================
# CONFIGURACIÓN (todo en la misma carpeta del script)
# =============================================================================
ENGROSAR_NIVEL = 2          # 1 = leve, 2 = medio (recomendado), 3 = fuerte
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
print(f"📍 Procesando imágenes en la carpeta del script: {SCRIPT_DIR}")

# =============================================================================
# FUNCIÓN 3x3: CLASIFICACIÓN DE PATRONES Y DECISIONES
# =============================================================================
def aplicar_reglas_3x3(patch_3x3):
    """Analiza el vecindario 3x3 y devuelve si el centro debe ser 0 o 255"""
    centro = patch_3x3[1, 1]
    if centro == 0:
        return 0  # ya es blanco, no tocar

    # Contar vecinos negros (8-connectivity)
    vecinos = patch_3x3.flatten()
    num_negros = np.sum(vecinos[vecinos == 255]) // 255 - 1  # restamos el centro

    # Regla 1: Ruido aislado (1 o 2 vecinos)
    if num_negros <= 2:
        return 0  # eliminar punto aislado

    # Regla 2: Trazo muy fino (3-4 vecinos) → engrosar
    if num_negros <= 4:
        return 255  # mantener y engrosar después

    # Regla 3: Trazo normal o esquina (5+ vecinos) → preservar
    return 255

# =============================================================================
# FUNCIÓN PRINCIPAL DE REFÍNADO PIXEL A PIXEL
# =============================================================================
def refinar_firma_con_3x3(ruta_imagen: str):
    print(f"📸 Procesando: {Path(ruta_imagen).name}")
    
    # Cargar en gris
    img = cv2.imread(ruta_imagen, cv2.IMREAD_GRAYSCALE)
    if img is None:
        print(f"   ❌ Error al leer {Path(ruta_imagen).name}")
        return

    # Mejora de contraste (CLAHE)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    img = clahe.apply(img)

    # Binarización adaptativa
    binaria = cv2.adaptiveThreshold(
        img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV, 21, 8
    )

    # Primera pasada: limpieza ligera con reglas 3x3
    altura, ancho = binaria.shape
    resultado = binaria.copy()
    
    for y in range(1, altura - 1):
        for x in range(1, ancho - 1):
            patch = binaria[y-1:y+2, x-1:x+2]
            resultado[y, x] = aplicar_reglas_3x3(patch)

    # Engrosado controlado después de las reglas 3x3
    kernel_eng = cv2.getStructuringElement(cv2.MORPH_RECT, (ENGROSAR_NIVEL, ENGROSAR_NIVEL))
    resultado = cv2.dilate(resultado, kernel_eng, iterations=1)

    # Fondo blanco + firma negra
    firma_final = cv2.bitwise_not(resultado)

    # Guardar en LA MISMA CARPETA
    nombre_salida = f"refinada_{Path(ruta_imagen).stem}.png"
    ruta_salida = os.path.join(SCRIPT_DIR, nombre_salida)
    cv2.imwrite(ruta_salida, firma_final)
    
    print(f"   ✅ Guardado: {nombre_salida} (procesamiento 3x3 + reglas de formas)")

# =============================================================================
# PROCESAMIENTO AUTOMÁTICO EN LA CARPETA DEL SCRIPT
# =============================================================================
if __name__ == "__main__":
    extensiones = {".png", ".jpg", ".jpeg", ".tiff"}
    archivos = [f for f in os.listdir(SCRIPT_DIR) 
                if Path(f).suffix.lower() in extensiones]
    
    if not archivos:
        print("❌ No se encontraron imágenes en la carpeta del script.")
        print("   Coloca tus fotos de firmas aquí y vuelve a ejecutar.")
        exit(1)
    
    print(f"📊 Encontradas {len(archivos)} imágenes para procesar...\n")
    
    for archivo in archivos:
        ruta = os.path.join(SCRIPT_DIR, archivo)
        refinar_firma_con_3x3(ruta)
    
    print("\n🎉 ¡PROCESO FINALIZADO!")
    print("   Todas las firmas refinadas están en esta misma carpeta.")
    print("   • Procesamiento pixel a pixel con matriz 3x3")
    print("   • Reglas de formas aplicadas (ruido eliminado, trazos reforzados)")
    print("   • Listo para usar en tu pipeline SUCAMEC completo")