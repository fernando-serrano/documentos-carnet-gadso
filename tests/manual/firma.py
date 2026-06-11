import os
import cv2
import numpy as np


def limpiar_firma_subtil(ruta_imagen_entrada, ruta_imagen_salida):
    # 1. Cargar la imagen original
    imagen = cv2.imread(ruta_imagen_entrada)
    if imagen is None:
        raise FileNotFoundError(
            f"No se pudo leer el archivo en: {ruta_imagen_entrada}"
        )

    # 2. CONVERSIÓN A GRISES INTELIGENTE (Para tinta tenue)
    # Explicación: Preserva mejor las sutiles transiciones de tinta azul en papel gris.
    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)

    # 3. ESTIMACIÓN Y "APLANADO" DE FONDO (Elimina gradientes de luz)
    # Explicación: Creamos una versión de fondo suavizado masivamente (101x101).
    background_flat = cv2.boxFilter(gris, -1, (101, 101))

    # Explicación: Dividimos la imagen por su fondo para aplanar la iluminación.
    flattened = cv2.divide(gris, background_flat, scale=255)

    # 4. REALCE DE CONTRASTE NO LINEAL (NUEVO: Clave para trazos tenues)
    # Explicación: Aplicamos un ajuste agresivo para que los trazos claros
    # se vuelvan mucho más oscuros antes de la binarización.
    # Usamos una técnica simple de estiramiento de contraste local (mín-máx estirado).
    min_val, max_val, _, _ = cv2.minMaxLoc(flattened)
    if max_val > min_val:  # Evita división por cero
        enhanced = cv2.normalize(
            flattened, None, alpha=0, beta=255, norm_type=cv2.NORM_MINMAX
        )
    else:
        enhanced = flattened

    # 5. UMBRALIZACIÓN ADAPTATIVA LOCAL (NUEVO: Solución definitiva)
    # Explicación: Calcula un umbral localmente para cada píxel. Encuentra la tinta
    # tenue comparándola con el papel de SU alrededor, no con un valor global.
    mascara_binaria = cv2.adaptiveThreshold(
        enhanced, 
        255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY_INV, 
        15,  # Tamaño de la vecindad local (bloque cuadrado 15x15)
        4    # Constante restada de la media (limpia ruido fino)
    )

    # 6. CIERRE MORFOLÓGICO Y LIMPIEZA
    # Explicación: Rellena huecos en los trazos finos y une líneas discontinuas.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mascara_limpia = cv2.morphologyEx(
        mascara_binaria, cv2.MORPH_CLOSE, kernel, iterations=1
    )

    # 7. SUAVIZADO DE BORDES FINAL (Anti-aliasing para trazos uniformes)
    mascara_suavizada = cv2.GaussianBlur(mascara_limpia, (3, 3), 0)

    # 8. TRANSPARENCIA Y CANAL ALFA (Aislamiento de precisión)
    alfa = mascara_suavizada.astype(float) / 255.0
    alfa_3d = np.stack([alfa, alfa, alfa], axis=-1)

    # 9. CREACIÓN DEL LIENZO FINAL (Firma Negra en Fondo Blanco)
    # Explicación: Definimos color de firma negro y fondo blanco para diferenciar totalmente.
    firma_negra = np.zeros_like(imagen)
    fondo_blanco = np.ones_like(imagen) * 255

    resultado_final = (
        firma_negra * alfa_3d + fondo_blanco * (1.0 - alfa_3d)
    ).astype(np.uint8)

    # 10. Guardar el resultado en alta calidad
    cv2.imwrite(ruta_imagen_salida, resultado_final)
    print(
        f"¡Proceso completado con éxito!\nFirma guardada en: {ruta_imagen_salida}"
    )


if __name__ == "__main__":
    # --- MANEJO DE RUTAS AUTOMÁTICO Y MULTI-FORMATO ---
    carpeta_actual = os.path.dirname(os.path.abspath(__file__))
    extensiones_soportadas = [".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]
    nombre_archivo_base = "imagen"

    ruta_entrada = None
    archivo_encontrado = None

    for ext in extensiones_soportadas:
        ruta_posible = os.path.join(carpeta_actual, f"{nombre_archivo_base}{ext}")
        if os.path.exists(ruta_posible):
            ruta_entrada = ruta_posible
            archivo_encontrado = f"{nombre_archivo_base}{ext}"
            break

    if ruta_entrada is None:
        print(f"\n[ERROR] No se encontró ningún archivo compatible en: {carpeta_actual}")
    else:
        ruta_salida = os.path.join(carpeta_actual, "firma_limpia_subtil.png")
        print(f"\nArchivo detectado: {archivo_encontrado}")
        try:
            limpiar_firma_subtil(ruta_entrada, ruta_salida)
        except Exception as e:
            print(f"\n[ERROR] {e}")