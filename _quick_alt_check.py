from dotenv import load_dotenv
from run_firma_digital import load_firma_digital_config
from flows.firma_digital_flow.firma_flow import (
    cargar_fuente_firma_por_dni,_extraer_drive_file_id,_descargar_drive_bytes,_abrir_imagen_procesable,
    _cargar_cv2_numpy,_generar_mascara_firma,_limpiar_ruido_conservador,_suprimir_artefactos_de_borde,
    _filtrar_cluster_principal_firma,_detectar_no_firma_morfologica,_mascara_por_tinta_color,_recortar_firma,_detectar_firma_fragmentada
)
import numpy as np, cv2
load_dotenv(); cfg=load_firma_digital_config()
class L:
    def info(self,*a,**k): pass
m=cargar_fuente_firma_por_dni(cfg.source_sheet_url,L())
for dni in ['42352709','61182389']:
    fid=_extraer_drive_file_id(m[dni]); content,mime=_descargar_drive_bytes(fid,cfg.drive_credentials_json)
    img=_abrir_imagen_procesable(content)
    cv2_mod,np_mod=_cargar_cv2_numpy(); arr=np_mod.array(img.convert('RGB')); gray=cv2_mod.cvtColor(arr,cv2_mod.COLOR_RGB2GRAY)
    for name,mask in [('main',_generar_mascara_firma(gray,cv2_mod,np_mod)[0]),('alt',_mascara_por_tinta_color(arr,gray,cv2_mod,np_mod))]:
        m1,d1=_limpiar_ruido_conservador(mask,cv2_mod,np_mod); m2,d2=_suprimir_artefactos_de_borde(m1,cv2_mod,np_mod); m3,d3=_filtrar_cluster_principal_firma(m2,cv2_mod,np_mod); ns,nsd=_detectar_no_firma_morfologica(m3,cv2_mod,np_mod)
        print('\n',dni,name,d1,'|',d2,'|',d3,'|',nsd)
        try:
            g,mk=_recortar_firma(gray,m3,cv2_mod,np_mod); frag,fd=_detectar_firma_fragmentada(mk,cv2_mod,np_mod); h,w=mk.shape[:2]; r=np_mod.count_nonzero(mk)/max(1,h*w)
            print(' crop',w,'x',h,'ratio',round(r,4),'frag',frag,fd,'ns',ns)
        except Exception as e:
            print(' crop_error',e,'ns',ns)
