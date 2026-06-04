import csv
from datetime import timedelta
from io import BytesIO

from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .models import Informe
from core_apps.common.permissions import is_admin_user


@login_required(login_url="/login/")
def lista_informes(request):
    if not is_admin_user(request.user):
        return HttpResponseForbidden("Solo un administrador puede ver informes.")

    informes = Informe.objects.order_by('-fecha')
    critical_count = informes.filter(epp_correcto=False).count()

    return render(request, 'informes/index.html', {
        'informes': informes,
        'critical_count': critical_count,
        'segment': 'lista_informes',
    })


def obtener_rango_fechas(periodo):
    """
    Retorna el rango de fechas según el período seleccionado.
    semanal: semana actual
    mensual: mes actual
    anual: año actual
    """
    ahora = timezone.localtime()
    inicio_hoy = ahora.replace(hour=0, minute=0, second=0, microsecond=0)

    if periodo == "semanal":
        inicio = inicio_hoy - timedelta(days=inicio_hoy.weekday())
        nombre_periodo = "Semanal"

    elif periodo == "mensual":
        inicio = inicio_hoy.replace(day=1)
        nombre_periodo = "Mensual"

    elif periodo == "anual":
        inicio = inicio_hoy.replace(month=1, day=1)
        nombre_periodo = "Anual"

    else:
        return None, None, None

    fin = ahora
    return inicio, fin, nombre_periodo


def obtener_informes_por_periodo(periodo):
    inicio, fin, nombre_periodo = obtener_rango_fechas(periodo)

    if not inicio:
        return None, None, None, None

    informes = Informe.objects.filter(
        fecha__gte=inicio,
        fecha__lte=fin
    ).order_by('-fecha')

    return informes, inicio, fin, nombre_periodo


def estado_epp(informe):
    return "EPP correcto" if informe.epp_correcto else "Alerta / Sin EPP"


@login_required(login_url="/login/")
def exportar_informes(request, periodo, formato):
    if not is_admin_user(request.user):
        return HttpResponseForbidden("Solo un administrador puede generar reportes.")

    formatos_permitidos = ["csv", "xlsx", "pdf"]

    if formato not in formatos_permitidos:
        return HttpResponseBadRequest("Formato de reporte no válido.")

    informes, inicio, fin, nombre_periodo = obtener_informes_por_periodo(periodo)

    if informes is None:
        return HttpResponseBadRequest("Período de reporte no válido.")

    fecha_actual = timezone.localtime().strftime("%Y%m%d_%H%M%S")
    nombre_archivo = f"reporte_informes_{periodo}_{fecha_actual}"

    if formato == "csv":
        return generar_reporte_csv(informes, nombre_archivo, nombre_periodo, inicio, fin)

    if formato == "xlsx":
        return generar_reporte_excel(informes, nombre_archivo, nombre_periodo, inicio, fin)

    if formato == "pdf":
        return generar_reporte_pdf(informes, nombre_archivo, nombre_periodo, inicio, fin)


def generar_reporte_csv(informes, nombre_archivo, nombre_periodo, inicio, fin):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}.csv"'

    # BOM para que Excel abra correctamente caracteres con tilde y ñ
    response.write("\ufeff")

    writer = csv.writer(response)
    writer.writerow([f"Reporte {nombre_periodo} de Informes"])
    writer.writerow([
        "Desde",
        timezone.localtime(inicio).strftime("%d/%m/%Y %H:%M:%S"),
        "Hasta",
        timezone.localtime(fin).strftime("%d/%m/%Y %H:%M:%S")
    ])
    writer.writerow([])
    writer.writerow(["Fecha", "Cámara", "Persona detectada", "Estado EPP", "Descripción"])

    for informe in informes:
        writer.writerow([
            timezone.localtime(informe.fecha).strftime("%d/%m/%Y %H:%M:%S"),
            informe.camara,
            informe.persona_detectada or "No identificada",
            estado_epp(informe),
            informe.descripcion or ""
        ])

    return response


def generar_reporte_excel(informes, nombre_archivo, nombre_periodo, inicio, fin):
    wb = Workbook()
    ws = wb.active
    ws.title = f"Reporte {nombre_periodo}"

    ws.merge_cells("A1:E1")
    ws["A1"] = f"Reporte {nombre_periodo} de Informes"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A1"].alignment = Alignment(horizontal="center")

    ws["A2"] = "Desde:"
    ws["B2"] = timezone.localtime(inicio).strftime("%d/%m/%Y %H:%M:%S")
    ws["D2"] = "Hasta:"
    ws["E2"] = timezone.localtime(fin).strftime("%d/%m/%Y %H:%M:%S")

    encabezados = ["Fecha", "Cámara", "Persona detectada", "Estado EPP", "Descripción"]

    ws.append([])
    ws.append(encabezados)

    fila_encabezado = 4

    for col in range(1, len(encabezados) + 1):
        celda = ws.cell(row=fila_encabezado, column=col)
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill(
            start_color="386FA4",
            end_color="386FA4",
            fill_type="solid"
        )
        celda.alignment = Alignment(horizontal="center")

    for informe in informes:
        ws.append([
            timezone.localtime(informe.fecha).strftime("%d/%m/%Y %H:%M:%S"),
            informe.camara,
            informe.persona_detectada or "No identificada",
            estado_epp(informe),
            informe.descripcion or ""
        ])

    # Ajuste de ancho de columnas corregido
    anchos = {
        "A": 22,
        "B": 25,
        "C": 30,
        "D": 20,
        "E": 60,
    }

    for columna, ancho in anchos.items():
        ws.column_dimensions[columna].width = ancho

    # Permite que la descripción no se corte visualmente
    for fila in ws.iter_rows(min_row=5, max_row=ws.max_row):
        for celda in fila:
            celda.alignment = Alignment(vertical="top", wrap_text=True)

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}.xlsx"'

    return response


def generar_reporte_pdf(informes, nombre_archivo, nombre_periodo, inicio, fin):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30,
        leftMargin=30,
        topMargin=30,
        bottomMargin=30
    )

    styles = getSampleStyleSheet()
    elementos = []

    titulo = Paragraph(f"<b>Reporte {nombre_periodo} de Informes</b>", styles["Title"])
    rango = Paragraph(
        f"Desde: {timezone.localtime(inicio).strftime('%d/%m/%Y %H:%M:%S')} "
        f" - Hasta: {timezone.localtime(fin).strftime('%d/%m/%Y %H:%M:%S')}",
        styles["Normal"]
    )

    elementos.append(titulo)
    elementos.append(rango)
    elementos.append(Spacer(1, 12))

    data = [["Fecha", "Cámara", "Persona", "Estado EPP", "Descripción"]]

    for informe in informes:
        data.append([
            timezone.localtime(informe.fecha).strftime("%d/%m/%Y %H:%M:%S"),
            informe.camara,
            informe.persona_detectada or "No identificada",
            estado_epp(informe),
            Paragraph(informe.descripcion or "", styles["BodyText"])
        ])

    if not informes.exists():
        data.append(["Sin registros", "-", "-", "-", "No existen informes en este período."])

    tabla = Table(
        data,
        repeatRows=1,
        colWidths=[100, 100, 120, 100, 320]
    )

    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#386FA4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F6F9FC")]),
    ]))

    elementos.append(tabla)
    doc.build(elementos)

    buffer.seek(0)

    response = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{nombre_archivo}.pdf"'

    return response