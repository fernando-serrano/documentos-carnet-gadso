import os
import cv2
import numpy as np


def limpiar_firma_perfecta(ruta_imagen_entrada, ruta_imagen_salida):
    # 1. Cargar la imagen original
    imagen = cv2.imread(ruta_imagen_entrada)
    if imagen is None:
        raise FileNotFoundError(
            f"No se pudo leer el archivo en: {ruta_imagen_entrada}"
        )

    # 2. Extracción del canal rojo (es el que mejor absorbe el azul del bolígrafo)
    canal_rojo = imagen[:, :, 2]

    # 3. Nivelación del fondo (Flat-field correction masivo)
    # Generamos un mapa de iluminación para remover las sombras pesadas del papel
    fondo_estimado = cv2.boxFilter(canal_rojo, -1, (101, 101))
    imagen_aplanada = cv2.divide(canal_rojo, fondo_estimado, scale=255)

    # 4. Umbralización adaptativa de Otsu
    # Encuentra el punto óptimo entre la tinta y el papel aplanado
    _, mascara_binaria = cv2.threshold(
        imagen_aplanada, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # 5. Operación Morfológica de Cierre (Une los trazos discontinuos)
    # Este kernel actúa como un puente conectando los píxeles intermitentes
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    mascara_cerrada = cv2.morphologyEx(
        mascara_binaria, cv2.MORPH_CLOSE, kernel, iterations=1
    )

    # 6. Anti-aliasing avanzado para trazos uniformes
    # Difuminamos ligeramente la máscara y la usamos como mapa de opacidad (Alfa)
    mascara_suavizada = cv2.GaussianBlur(mascara_cerrada, (3, 3), 0)
    alfa = mascara_suavizada.astype(float) / 255.0
    alfa_3d = np.stack([alfa, alfa, alfa], axis=-1)

    # 7. Reconstrucción en Lienzo Limpio (Firma Negra Pura [0,0,0] sobre Blanco [255,255,255])
    firma_negra = np.zeros_like(imagen)
    fondo_blanco = np.ones_like(imagen) * 255

    resultado_final = (
        firma_negra * alfa_3d + fondo_blanco * (1.0 - alfa_3d)
    ).astype(np.uint8)

    # 8. Guardar resultado final
    cv2.imwrite(ruta_imagen_salida, resultado_final)
    print(f"\n[ÉXITO] Archivo procesado desde: {os.path.basename(ruta_imagen_entrada)}")
    print(f"Resultado guardado en: {ruta_imagen_salida}")


if __name__ == "__main__":
    carpeta_actual = os.path.dirname(os.path.abspath(__file__))

    # Buscamos de forma estricta los formatos soportados
    extensiones = [".jpg", ".jpeg", ".png", ".JPG", ".JPEG", ".PNG"]
    nombre_base = "imagen"

    ruta_entrada = None
    for ext in extensiones:
        ruta_posible = os.path.join(carpeta_actual, f"{nombre_base}{ext}")
        if os.path.exists(ruta_posible):
            ruta_entrada = ruta_posible
            break

    if ruta_entrada is None:
        print(
            f"\n[ERROR] No se encontró la foto original ('imagen.jpg' o 'imagen.png') en: {carpeta_actual}"
        )
    else:
        ruta_salida = os.path.join(carpeta_actual, "firma_limpia_definitiva.png")
        try:
            limpiar_firma_perfecta(ruta_entrada, ruta_salida)
        except Exception as e:
            print(f"\n[ERROR] Ocurrió un fallo en el procesamiento: {e}")