from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from io import BytesIO

def export_cp_to_pdf(cp_data: dict) -> BytesIO:
    """Экспортирует КП в PDF"""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=30, leftMargin=30, topMargin=30, bottomMargin=18)
    
    # Попытка зарегистрировать русский шрифт (опционально)
    try:
        pdfmetrics.registerFont(TTFont('DejaVuSans', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'))
        pdfmetrics.registerFont(TTFont('DejaVuSans-Bold', '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'))
        font_name = 'DejaVuSans'
        font_name_bold = 'DejaVuSans-Bold'
    except:
        font_name = 'Helvetica'
        font_name_bold = 'Helvetica-Bold'
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=font_name_bold,
        fontSize=18,
        textColor=colors.HexColor('#1f4788'),
        spaceAfter=30,
        alignment=1  # center
    )
    
    heading_style = ParagraphStyle(
        'CustomHeading',
        parent=styles['Heading2'],
        fontName=font_name_bold,
        fontSize=12,
        spaceAfter=12
    )
    
    normal_style = ParagraphStyle(
        'CustomNormal',
        parent=styles['Normal'],
        fontName=font_name,
        fontSize=10,
        spaceAfter=6
    )
    
    story = []
    
    # Заголовок
    story.append(Paragraph("КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ", title_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Информация о поставщике
    story.append(Paragraph(f"<b>Поставщик:</b> {cp_data.get('supplier_name', '')}", normal_style))
    story.append(Paragraph(f"<b>ИНН:</b> {cp_data.get('supplier_inn', '')}", normal_style))
    story.append(Paragraph(f"<b>Контакт:</b> {cp_data.get('contact', '')}", normal_style))
    story.append(Spacer(1, 0.2*inch))
    
    # Информация о тендере
    story.append(Paragraph(f"<b>Тендер:</b> {cp_data.get('tender_name', '')}", normal_style))
    if cp_data.get('tender_number'):
        story.append(Paragraph(f"<b>Номер:</b> {cp_data.get('tender_number', '')}", normal_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Таблица
    table_data = [['№', 'Наименование', 'Произв.', 'Модель', 'Кол-во', 'Ед.', 'Цена', 'Сумма']]
    
    for item in cp_data.get('items', []):
        table_data.append([
            str(item.get('position', '')),
            item.get('name', '')[:40],
            item.get('manufacturer', '')[:15],
            item.get('model', '')[:15],
            str(item.get('quantity', 1)),
            item.get('unit', 'шт'),
            f"{item.get('price', 0):,.0f}",
            f"{item.get('sum', 0):,.0f}"
        ])
    
    table = Table(table_data, colWidths=[0.4*inch, 2*inch, 1*inch, 1*inch, 0.6*inch, 0.5*inch, 0.8*inch, 0.9*inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#4472C4')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), font_name_bold),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -1), colors.beige),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('FONTNAME', (0, 1), (-1, -1), font_name),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
    ]))
    
    story.append(table)
    story.append(Spacer(1, 0.3*inch))
    
    # Итого
    story.append(Paragraph(f"<b>Итого без НДС:</b> {cp_data.get('total', 0):,.2f} ₽", normal_style))
    story.append(Paragraph(f"<b>НДС 20%:</b> {cp_data.get('vat', 0):,.2f} ₽", normal_style))
    story.append(Paragraph(f"<b><font size=12>Всего с НДС: {cp_data.get('total_with_vat', 0):,.2f} ₽</font></b>", normal_style))
    story.append(Spacer(1, 0.3*inch))
    
    # Условия
    story.append(Paragraph("<b>Условия поставки:</b>", heading_style))
    story.append(Paragraph(f"• Срок поставки: {cp_data.get('delivery_days', '')} дней (до {cp_data.get('delivery_date', '')})", normal_style))
    story.append(Paragraph(f"• Условия оплаты: {cp_data.get('payment_terms', '')}", normal_style))
    story.append(Paragraph(f"• Гарантия: {cp_data.get('warranty', '')}", normal_style))
    story.append(Spacer(1, 0.3*inch))
    
    story.append(Paragraph(f"<i>Дата формирования: {cp_data.get('generated_at', '')}</i>", normal_style))
    
    doc.build(story)
    buffer.seek(0)
    return buffer
