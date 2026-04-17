from dotenv import load_dotenv
from run_firma_digital import load_firma_digital_config
from flows.firma_digital_flow.firma_flow import (
    cargar_fuente_firma_por_dni,_extraer_drive_file_id,_descargar_drive_bytes,_abrir_imagen_procesable,
    _cargar_cv2_numpy,_generar_mascara_firma,_limpiar_ruido_conservador,_suprimir_artefactos_de_borde,
    _filtrar_cluster_principal_firma,_detectar_no_firma_morfologica,_engrosar_si_tenue,_recortar_firma,
    _detectar_firma_fragmentada,_mascara_por_tinta_color
)
from pathlib import Path
from PIL import Image
import numpy as np
import cv2

load_dotenv(); cfg=load_firma_digital_config()
class L: 
    def info(self,*a,**k): pass
m = cargar_fuente_firma_por_dni(cfg.source_sheet_url, L())
out=Path('data/firma_digital/debug_more'); out.mkdir(parents=True, exist_ok=True)

for dni in ['73014108','74767018']:
    print('\n===',dni,'===')
    fid=_extraer_drive_file_id(m[dni])
    content,mime=_descargar_drive_bytes(fid,cfg.drive_credentials_json)
    img=_abrir_imagen_procesable(content)
    img.save(out/f'{dni}_source.png')

    cv2_mod,np_mod=_cargar_cv2_numpy()
    arr=np_mod.array(img.convert('RGB'))
    gray=cv2_mod.cvtColor(arr,cv2_mod.COLOR_RGB2GRAY)

    mask_raw,d_raw=_generar_mascara_firma(gray,cv2_mod,np_mod)
    mask_clean,d_clean=_limpiar_ruido_conservador(mask_raw,cv2_mod,np_mod)
    mask_border,d_border=_suprimir_artefactos_de_borde(mask_clean,cv2_mod,np_mod)
    mask_cluster,d_cluster=_filtrar_cluster_principal_firma(mask_border,cv2_mod,np_mod)
    ns,nsd=_detectar_no_firma_morfologica(mask_cluster,cv2_mod,np_mod)

    raw_fg=int(np_mod.count_nonzero(mask_raw)); clean_fg=int(np_mod.count_nonzero(mask_cluster))
    raw_ratio=raw_fg/max(1,mask_raw.size); keep_ratio=clean_fg/max(1,raw_fg)

    print('main:',d_raw,'|',d_clean,'|',d_border,'|',d_cluster,'|',nsd)
    print('raw_fg',raw_fg,'clean_fg',clean_fg,'raw_ratio',round(raw_ratio,5),'keep_ratio',round(keep_ratio,5))

    Image.fromarray(mask_raw).save(out/f'{dni}_mask_raw.png')
    Image.fromarray(mask_cluster).save(out/f'{dni}_mask_cluster.png')

    try:
        mask_final,d_thick,thick=_engrosar_si_tenue(mask_cluster,cv2_mod,np_mod)
        gray_crop,mask_crop=_recortar_firma(gray,mask_final,cv2_mod,np_mod)
        frag,fd=_detectar_firma_fragmentada(mask_crop,cv2_mod,np_mod)
        ch,cw=mask_crop.shape[:2]
        crop_fg=int(np_mod.count_nonzero(mask_crop)); crop_ratio=crop_fg/max(1,ch*cw)
        print('crop_main:',d_thick,'frag',frag,fd,'crop_ratio',round(crop_ratio,5),'size',cw,'x',ch)
        Image.fromarray(mask_crop).save(out/f'{dni}_mask_crop_main.png')
    except Exception as e:
        print('crop_main_error',e)

    mask_alt=_mascara_por_tinta_color(arr,gray,cv2_mod,np_mod)
    mask_alt,da1=_limpiar_ruido_conservador(mask_alt,cv2_mod,np_mod)
    mask_alt,da2=_suprimir_artefactos_de_borde(mask_alt,cv2_mod,np_mod)
    mask_alt,da3=_filtrar_cluster_principal_firma(mask_alt,cv2_mod,np_mod)
    ns2,nsd2=_detectar_no_firma_morfologica(mask_alt,cv2_mod,np_mod)
    print('alt :',da1,'|',da2,'|',da3,'|',nsd2)
    Image.fromarray(mask_alt).save(out/f'{dni}_mask_alt.png')

    try:
        g2,m2=_recortar_firma(gray,mask_alt,cv2_mod,np_mod)
        f2,d2=_detectar_firma_fragmentada(m2,cv2_mod,np_mod)
        ch,cw=m2.shape[:2]
        crop_fg=int(np_mod.count_nonzero(m2)); crop_ratio=crop_fg/max(1,ch*cw)
        print('crop_alt: frag',f2,d2,'crop_ratio',round(crop_ratio,5),'size',cw,'x',ch)
        Image.fromarray(m2).save(out/f'{dni}_mask_crop_alt.png')
    except Exception as e:
        print('crop_alt_error',e)

print('\nSaved in',out)
