from dotenv import load_dotenv
from run_firma_digital import load_firma_digital_config
from flows.firma_digital_flow.sheets import read_google_sheet_rows
from flows.firma_digital_flow.firma_flow import _resolver_columna, _normalizar_dni

load_dotenv()
cfg = load_firma_digital_config()
rows, fieldnames = read_google_sheet_rows(cfg.source_sheet_url)
dni_col = _resolver_columna(fieldnames, ['dni'])
firma_col = _resolver_columna(fieldnames, ['cargar firma digital','firma digital','cargar firma','url 1','url1','link firma digital','link firma','firma'])

objetivo = {'42352709','61182389','70751088','73014108','75823237','74767018'}
by = {k: [] for k in objetivo}
for i,row in enumerate(rows, start=2):
    dni = _normalizar_dni(row.get(dni_col,''))
    if dni in objetivo:
        by[dni].append((i, str(row.get(firma_col,'') or '').strip()))

for dni in sorted(objetivo):
    vals = by[dni]
    print('\nDNI',dni,'matches=',len(vals))
    for r,u in vals[:10]:
        print(' row',r,'url',u)
    if len(vals)>10:
        print(' ...')
