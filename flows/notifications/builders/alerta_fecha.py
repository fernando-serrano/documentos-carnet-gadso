"""Construccion del asunto y cuerpo HTML de la alerta de nueva fecha.

Dos variantes segun `hay_cupos`:
  - CON cupos:   "Nueva fecha ... con cupos: 24/06 (N cupos)" + tabla de horarios.
  - SIN cupos:   "Nueva fecha ABIERTA (sin cupos aun): 24/06" + nota informativa.
Ambas adjuntan la captura de la vista completa.
"""
from __future__ import annotations


def construir_asunto(prefix: str, sede: str, fecha_detectada: str, total_cupos: int, hay_cupos: bool) -> str:
    if hay_cupos:
        return (
            f"{prefix} | Nueva fecha de Examen de Tiro CON CUPOS: {fecha_detectada} "
            f"(Sede {sede}, {total_cupos} cupos)"
        )
    return f"{prefix} | Nueva fecha de Examen de Tiro ABIERTA (sin cupos aun): {fecha_detectada} (Sede {sede})"


def construir_cuerpo_html(
    sede: str,
    fecha_detectada: str,
    fecha_anterior: str,
    horarios: list[dict],
    total_cupos: int,
    hay_cupos: bool,
    fechas_disponibles: list[str],
    detectado_en: str,
    adjunta_captura: bool = False,
) -> str:
    lista_fechas = "".join(f"<li>{f}</li>" for f in fechas_disponibles) or "<li>(sin detalle)</li>"

    nota_captura = (
        '<p style="font-size:13px;color:#444;">📎 Se adjunta una <b>captura de la vista completa</b> '
        "del portal con la fecha seleccionada y los horarios.</p>"
        if adjunta_captura else ""
    )

    if hay_cupos:
        intro = ("Se detecto una <b>nueva fecha</b> de <b>Examen para Poligono de Tiro</b> "
                 "<b>con cupos disponibles</b> en el SEL de SUCAMEC.")
        fila_cupos = (f'<tr><td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;">'
                      f'<b>Cupos disponibles (total)</b></td>'
                      f'<td style="padding:6px 12px;border:1px solid #ddd;"><b>{total_cupos}</b></td></tr>')
        filas_horarios = "".join(
            f"<tr><td style='padding:5px 14px;border:1px solid #ddd;'>{h['hora']}</td>"
            f"<td style='padding:5px 14px;border:1px solid #ddd;text-align:center;'><b>{h['cupos']}</b></td></tr>"
            for h in horarios
        ) or "<tr><td colspan='2' style='padding:5px 14px;border:1px solid #ddd;'>(sin detalle)</td></tr>"
        bloque_horarios = f"""
    <p><b>Horarios con cupos disponibles:</b></p>
    <table style="border-collapse:collapse;margin:8px 0;">
      <tr>
        <td style="padding:5px 14px;background:#eee;border:1px solid #ddd;"><b>Horario</b></td>
        <td style="padding:5px 14px;background:#eee;border:1px solid #ddd;"><b>Cupos</b></td>
      </tr>
      {filas_horarios}
    </table>"""
        accion = ("<b>Accion sugerida:</b> los cupos pueden agotarse en minutos. "
                  "Ingresar al SEL y gestionar las reservas a la brevedad.")
    else:
        intro = ("Se detecto que SUCAMEC <b>abrio una nueva fecha</b> de <b>Examen para Poligono "
                 "de Tiro</b> en el SEL durante el horario laboral. La fecha aparece <b>ABIERTA "
                 "pero aun sin cupos disponibles</b> (probablemente carguen luego).")
        fila_cupos = ('<tr><td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;">'
                      '<b>Cupos disponibles</b></td>'
                      '<td style="padding:6px 12px;border:1px solid #ddd;background:#ffe0e0;">'
                      '<b>0 (aun sin cupos)</b></td></tr>')
        bloque_horarios = ('<p style="color:#444;">La fecha figura abierta en el desplegable pero la '
                           'tabla de horarios no muestra cupos por ahora. Revisar el portal: los cupos '
                           'podrian cargarse en cualquier momento.</p>')
        accion = ("<b>Accion sugerida:</b> estar atentos al SEL; la fecha ya esta abierta y los cupos "
                  "podrian habilitarse pronto.")

    return f"""\
<html>
  <body style="font-family:Segoe UI,Arial,sans-serif;color:#222;">
    <p>🤖 <b>Alerta automatica TIMER-SUCAMEC</b></p>
    <p>{intro}</p>

    <table style="border-collapse:collapse;margin:12px 0;">
      <tr>
        <td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;"><b>Sede</b></td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{sede}</td>
      </tr>
      <tr>
        <td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;"><b>Fecha NUEVA detectada</b></td>
        <td style="padding:6px 12px;border:1px solid #ddd;background:#fff7cc;"><b>{fecha_detectada}</b></td>
      </tr>
      {fila_cupos}
      <tr>
        <td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;"><b>Ultima fecha previa</b></td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{fecha_anterior or '(ninguna)'}</td>
      </tr>
      <tr>
        <td style="padding:6px 12px;background:#f3f3f3;border:1px solid #ddd;"><b>Detectado</b></td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{detectado_en} hras</td>
      </tr>
    </table>
{bloque_horarios}

    <p style="color:#a00;">{accion}</p>

    {nota_captura}

    <p style="font-size:12px;color:#666;"><b>Fechas en el desplegable al momento:</b></p>
    <ul style="font-size:12px;color:#666;">{lista_fechas}</ul>

    <hr/>
    <p style="font-size:12px;color:#888;">Correo generado automaticamente por el bot de vigilancia
       TIMER-SUCAMEC. No responder a este mensaje.</p>
  </body>
</html>"""
