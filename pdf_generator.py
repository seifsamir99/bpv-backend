"""
PDF generation for Purchase & Accounting documents
Generates professional PDFs for quotations, LPOs, invoices, delivery orders
"""

import os
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.platypus.flowables import HRFlowable
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class PDFGenerator:
    """Generate professional PDF documents"""

    def __init__(self):
        self.company_name = os.getenv('COMPANY_NAME', 'Your Company Name')
        self.company_trn = os.getenv('COMPANY_TRN', 'TRN: XXXXX')
        self.company_address = os.getenv('COMPANY_ADDRESS', 'Dubai, UAE')
        self.company_logo_path = os.getenv('COMPANY_LOGO_PATH', None)

        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='CompanyName',
            parent=self.styles['Heading1'],
            fontSize=20,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            alignment=TA_LEFT
        ))

        self.styles.add(ParagraphStyle(
            name='DocumentTitle',
            parent=self.styles['Heading1'],
            fontSize=16,
            textColor=colors.HexColor('#2563eb'),
            spaceAfter=12,
            alignment=TA_CENTER
        ))

        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            spaceBefore=12
        ))

        # Normal style already exists in base styles, just use it directly

    def _add_header(self, elements, doc_title):
        """Add company header to document"""
        # Company logo if available
        if self.company_logo_path and os.path.exists(self.company_logo_path):
            logo = Image(self.company_logo_path, width=2*inch, height=0.8*inch)
            elements.append(logo)
            elements.append(Spacer(1, 6))

        # Company name
        elements.append(Paragraph(self.company_name, self.styles['CompanyName']))
        elements.append(Paragraph(self.company_address, self.styles['Normal']))
        elements.append(Paragraph(self.company_trn, self.styles['Normal']))
        elements.append(Spacer(1, 12))

        # Document title
        elements.append(Paragraph(doc_title, self.styles['DocumentTitle']))
        elements.append(Spacer(1, 12))

    def _add_supplier_info(self, elements, supplier_data):
        """Add supplier information section"""
        elements.append(Paragraph("Vendor Information", self.styles['SectionHeader']))

        supplier_info = [
            ["Supplier Name:", supplier_data.get('name', '')],
            ["Contact Person:", supplier_data.get('contact', '')],
            ["Email:", supplier_data.get('email', '')],
            ["Phone:", supplier_data.get('phone', '')],
            ["Address:", supplier_data.get('address', '')]
        ]

        if supplier_data.get('trn'):
            supplier_info.append(["TRN:", supplier_data['trn']])

        table = Table(supplier_info, colWidths=[1.5*inch, 4*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#374151')),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 12))

    def _add_line_items_table(self, elements, line_items, show_delivered=False):
        """Add line items table"""
        elements.append(Paragraph("Line Items", self.styles['SectionHeader']))

        # Build header
        if show_delivered:
            header = ["#", "Description", "Qty Ordered", "Qty Delivered", "Unit",
                     "Unit Price (AED)", "Total (AED)"]
            col_widths = [0.3*inch, 2.5*inch, 0.8*inch, 0.8*inch, 0.5*inch,
                         1*inch, 1*inch]
        else:
            header = ["#", "Description", "Quantity", "Unit",
                     "Unit Price (AED)", "Total (AED)"]
            col_widths = [0.3*inch, 3*inch, 0.8*inch, 0.6*inch, 1*inch, 1.2*inch]

        data = [header]

        # Add items
        for i, item in enumerate(line_items, 1):
            if show_delivered:
                row = [
                    str(i),
                    item.get('description', ''),
                    str(item.get('quantity_ordered', item.get('quantity', ''))),
                    str(item.get('quantity_delivered', '')),
                    item.get('unit', ''),
                    f"{float(item.get('unit_price', 0)):,.2f}",
                    f"{float(item.get('total_price', 0)):,.2f}"
                ]
            else:
                row = [
                    str(i),
                    item.get('description', ''),
                    str(item.get('quantity', '')),
                    item.get('unit', ''),
                    f"{float(item.get('unit_price', 0)):,.2f}",
                    f"{float(item.get('total_price', 0)):,.2f}"
                ]
            data.append(row)

        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            # Header styling
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),

            # Body styling
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#374151')),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # # column
            ('ALIGN', (-2, 0), (-1, -1), 'RIGHT'),  # Price columns

            # Alternating row colors
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#f9fafb')]),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 12))

    def _add_totals(self, elements, subtotal, vat_percent, vat_amount, total):
        """Add totals section"""
        totals_data = [
            ["Subtotal:", f"{float(subtotal):,.2f} AED"],
            [f"VAT ({vat_percent}%):", f"{float(vat_amount):,.2f} AED"],
            ["Total Amount:", f"{float(total):,.2f} AED"]
        ]

        table = Table(totals_data, colWidths=[4.5*inch, 1.5*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, 1), 'Helvetica-Bold'),
            ('FONTNAME', (1, 0), (1, 1), 'Helvetica'),
            ('FONTNAME', (0, 2), (-1, 2), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
            ('TEXTCOLOR', (0, 0), (-1, 1), colors.HexColor('#374151')),
            ('TEXTCOLOR', (0, 2), (-1, 2), colors.HexColor('#1a1a1a')),
            ('LINEABOVE', (0, 2), (-1, 2), 1.5, colors.HexColor('#2563eb')),
            ('TOPPADDING', (0, 2), (-1, 2), 8),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 12))

    def generate_quotation(self, quotation_data, output_path):
        """Generate quotation PDF"""
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                              topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []

        # Header
        self._add_header(elements, "QUOTATION")

        # Quotation details
        details = [
            ["Quotation No:", quotation_data.get('quotation_id', '')],
            ["Date:", quotation_data.get('quotation_date', '')],
            ["Valid Until:", quotation_data.get('validity_date', '')],
            ["Payment Terms:", quotation_data.get('payment_terms', '')]
        ]

        table = Table(details, colWidths=[1.5*inch, 2*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 12))

        # Vendor info
        self._add_supplier_info(elements, quotation_data.get('supplier', {}))

        # Line items
        self._add_line_items_table(elements, quotation_data.get('line_items', []))

        # Totals
        self._add_totals(
            elements,
            quotation_data.get('subtotal', 0),
            quotation_data.get('vat_percent', 5),
            quotation_data.get('vat_amount', 0),
            quotation_data.get('total_amount', 0)
        )

        # Notes
        if quotation_data.get('notes'):
            elements.append(Paragraph("Notes", self.styles['SectionHeader']))
            elements.append(Paragraph(quotation_data['notes'], self.styles['Normal']))

        # Build PDF
        doc.build(elements)
        return output_path

    @staticmethod
    def _format_date(date_str):
        """Convert YYYY-MM-DD to '9th April, 2026' format"""
        if not date_str:
            return ''
        try:
            from datetime import datetime as dt
            d = dt.strptime(str(date_str).strip(), '%Y-%m-%d')
            day = d.day
            suffix = 'th' if 11 <= day <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(day % 10, 'th')
            return f"{day}{suffix} {d.strftime('%B, %Y')}"
        except Exception:
            return str(date_str)

    def generate_lpo(self, lpo_data, output_path):
        """Generate LPO PDF matching exact company format (LPO-1558 style)"""
        from PIL import Image as PILImage
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # Full letterhead (A4-sized image with header + footer)
        lh_full  = os.path.join(base_dir, '..', 'assets', 'letterhead.jpg')
        lh_hdr   = os.path.join(base_dir, '..', 'assets', 'letterhead_header.jpg')

        # Measure header height from letterhead_header.jpg
        try:
            with PILImage.open(lh_hdr) as _img:
                _iw, _ih = _img.size
            top_margin = A4[0] * (_ih / _iw) + 4   # header height + small gap
        except Exception:
            top_margin = 1.8 * inch

        # Estimate footer height: letterhead.jpg is A4-sized, footer ≈ bottom 10%
        try:
            with PILImage.open(lh_full) as _img:
                _fw, _fh = _img.size
            footer_frac = 0.10          # tweak if footer is taller/shorter
            bottom_margin = A4[1] * footer_frac + 4
        except Exception:
            bottom_margin = 0.9 * inch

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=top_margin, bottomMargin=bottom_margin,
                                leftMargin=0.5*inch, rightMargin=0.5*inch)
        page_w = A4[0] - inch  # usable content width
        elements = []

        # Draw full letterhead.jpg as page background on every page
        # (covers both header and footer automatically)
        _lf = lh_full if os.path.exists(lh_full) else lh_hdr
        def _draw_bg(canvas, doc):
            canvas.saveState()
            canvas.drawImage(_lf, 0, 0, width=A4[0], height=A4[1],
                             preserveAspectRatio=False, mask='auto')
            canvas.restoreState()

        elements.append(Spacer(1, 2))

        # ── EXTRACT DATA ─────────────────────────────────────────────
        lpo = lpo_data.get('lpo', lpo_data)
        supplier = lpo_data.get('supplier', {})
        line_items = lpo_data.get('line_items', [])

        lpo_num          = lpo.get('lpo_id', lpo.get('LPO ID', ''))
        lpo_date         = self._format_date(lpo.get('lpo_date', lpo.get('LPO Date', '')))
        quote_ref        = lpo.get('quote_ref', lpo.get('Quote Ref', ''))
        project          = lpo.get('project', lpo.get('Project', ''))
        delivery_site    = lpo.get('delivery_site', lpo.get('site', lpo.get('Delivery Site', '')))
        delivery_contact = lpo.get('delivery_contact', lpo.get('Delivery Contact', ''))
        delivery_phone   = lpo.get('delivery_phone', lpo.get('Delivery Phone', ''))
        payment_terms    = lpo.get('payment_terms', lpo.get('Payment Terms', ''))
        vat_percent      = float(lpo.get('vat_percent', lpo.get('VAT %', 5)))
        supplier_name    = supplier.get('name', supplier.get('Supplier Name', ''))

        # ── INFO TABLE: LPO No/Supplier | Date/Quote Ref ─────────────
        s_lbl = ParagraphStyle('lpo_lbl', fontName='Helvetica-Bold', fontSize=9)
        s_val = ParagraphStyle('lpo_val', fontName='Helvetica', fontSize=9)

        info_data = [
            [Paragraph('LPO No:', s_lbl), str(lpo_num),
             Paragraph('Supplier:', s_lbl), supplier_name],
            [Paragraph('Date:', s_lbl), lpo_date,
             Paragraph('Quote Ref:', s_lbl), quote_ref],
            [Paragraph('Newell TRN:', s_lbl), str(self.company_trn), '', ''],
        ]
        info_tbl = Table(info_data,
                         colWidths=[0.85*inch, 2.1*inch, 1.0*inch, 3.32*inch])
        info_tbl.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
            ('FONTNAME', (3, 0), (3, -1), 'Helvetica'),
            ('VALIGN',   (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(info_tbl)
        elements.append(Spacer(1, 4))

        # ── PROJECT BOX ──────────────────────────────────────────────
        if project:
            s_proj = ParagraphStyle('lpo_proj', fontName='Helvetica', fontSize=9,
                                    leading=13)
            proj_cell = Paragraph(f'<b>PROJECT:</b> {project}', s_proj)
            proj_tbl = Table([[proj_cell]], colWidths=[page_w])
            proj_tbl.setStyle(TableStyle([
                ('BOX',           (0, 0), (-1, -1), 1, colors.black),
                ('TOPPADDING',    (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING',   (0, 0), (-1, -1), 6),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
            ]))
            elements.append(proj_tbl)
            elements.append(Spacer(1, 4))

        # ── LINE ITEMS TABLE ─────────────────────────────────────────
        # SN | Description | Qty | Unit | Rate (AED) | Total (AED)
        col_w = [0.35*inch, 3.22*inch, 0.5*inch, 0.5*inch, 1.1*inch, 1.1*inch]

        s_hdr = ParagraphStyle('lpo_hdr', fontName='Helvetica-Bold',
                               fontSize=9, alignment=TA_CENTER)
        s_item = ParagraphStyle('lpo_item', fontName='Helvetica',
                                fontSize=7.5, leading=9.5)
        s_tot_lbl = ParagraphStyle('lpo_tlbl', fontName='Helvetica-Bold',
                                   fontSize=9, alignment=TA_RIGHT, leading=12)
        s_tot_val = ParagraphStyle('lpo_tval', fontName='Helvetica-Bold',
                                   fontSize=9, alignment=TA_RIGHT)

        tbl_data = [[
            Paragraph('SN', s_hdr),
            Paragraph('Description', s_hdr),
            Paragraph('Qty', s_hdr),
            Paragraph('Unit', s_hdr),
            Paragraph('Rate (AED)', s_hdr),
            Paragraph('Total (AED)', s_hdr),
        ]]

        subtotal = 0
        for i, item in enumerate(line_items, 1):
            up   = float(item.get('unit_price',  item.get('Unit Price (AED)', 0)))
            qty  = float(item.get('quantity',    item.get('Quantity Ordered', 0)))
            tot  = float(item.get('total_price', item.get('Total Price (AED)', up * qty)))
            subtotal += tot
            qty_str = str(int(qty)) if qty == int(qty) else str(qty)
            desc = item.get('description', item.get('Item Description', ''))
            unit = item.get('unit', item.get('Unit', ''))
            tbl_data.append([
                str(i),
                Paragraph(desc, s_item),
                qty_str,
                unit,
                f'{up:,.2f}',
                f'{tot:,.2f}',
            ])

        vat_amount   = subtotal * vat_percent / 100
        total_amount = subtotal + vat_amount
        vat_label    = f'VAT {int(vat_percent) if vat_percent == int(vat_percent) else vat_percent}%:'

        n_items = len(tbl_data)  # header + item rows

        tbl_data.append(['', '', '', '',
                         Paragraph('Grand Total<br/>(AED):', s_tot_lbl),
                         Paragraph(f'{subtotal:,.2f}', s_tot_val)])
        tbl_data.append(['', '', '', '',
                         Paragraph(vat_label, s_tot_lbl),
                         Paragraph(f'{vat_amount:,.2f}', s_tot_val)])
        tbl_data.append(['', '', '', '',
                         Paragraph('<b>TOTAL<br/>(AED):</b>', s_tot_lbl),
                         Paragraph(f'<b>{total_amount:,.2f}</b>', s_tot_val)])

        n = len(tbl_data)
        items_table = Table(tbl_data, colWidths=col_w)
        items_table.setStyle(TableStyle([
            # Header row
            ('BACKGROUND',    (0, 0), (-1, 0),        colors.HexColor('#e0e0e0')),
            ('FONTNAME',      (0, 0), (-1, 0),        'Helvetica-Bold'),
            ('ALIGN',         (0, 0), (-1, 0),        'CENTER'),
            ('TOPPADDING',    (0, 0), (-1, 0),        4),
            ('BOTTOMPADDING', (0, 0), (-1, 0),        4),
            # Item rows
            ('FONTNAME',      (0, 1), (-1, n_items-1), 'Helvetica'),
            ('FONTSIZE',      (0, 1), (-1, n_items-1), 7.5),
            ('TOPPADDING',    (0, 1), (-1, n_items-1), 2),
            ('BOTTOMPADDING', (0, 1), (-1, n_items-1), 2),
            # Alignment
            ('ALIGN',  (0, 1), (0, n_items-1), 'CENTER'),  # SN
            ('ALIGN',  (2, 1), (3, n_items-1), 'CENTER'),  # Qty, Unit
            ('ALIGN',  (4, 1), (5, n_items-1), 'RIGHT'),   # Amounts
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            # Grid for header + item rows
            ('GRID', (0, 0), (-1, n_items-1), 0.75, colors.black),
            ('BOX',  (0, 0), (-1, n_items-1), 1.5,  colors.black),
            # Totals box (last 3 rows, last 2 cols only)
            ('BOX',       (4, n_items), (5, n-1),   1,   colors.black),
            ('INNERGRID', (4, n_items), (5, n-1),   0.5, colors.black),
            # TOTAL row background
            ('BACKGROUND', (4, n-1), (5, n-1), colors.HexColor('#e0e0e0')),
            # Totals padding
            ('TOPPADDING',    (4, n_items), (5, n-1), 3),
            ('BOTTOMPADDING', (4, n_items), (5, n-1), 3),
            ('RIGHTPADDING',  (4, n_items), (5, n-1), 6),
        ]))
        elements.append(items_table)
        elements.append(Spacer(1, 6))

        # ── TERMS & CONDITIONS ───────────────────────────────────────
        s_tc_hdr = ParagraphStyle('lpo_tch', fontName='Helvetica-Bold',
                                  fontSize=8, spaceAfter=2)
        s_tc = ParagraphStyle('lpo_tc', fontName='Helvetica', fontSize=8,
                              leftIndent=8, spaceAfter=1, leading=11)

        elements.append(Paragraph('<b>Terms &amp; Conditions:</b>', s_tc_hdr))
        cnum = 1
        if delivery_site:
            elements.append(Paragraph(
                f'{cnum}. Delivery: At Site \u2014 {delivery_site}', s_tc))
            cnum += 1
        if delivery_contact or delivery_phone:
            contact_part = delivery_contact or ''
            phone_part   = f' on {delivery_phone}' if delivery_phone else ''
            elements.append(Paragraph(
                f'{cnum}. For delivery arrangements contact: {contact_part}{phone_part}', s_tc))
            cnum += 1
        if payment_terms:
            elements.append(Paragraph(
                f'{cnum}. Payment Terms: {payment_terms}', s_tc))
            cnum += 1
        elements.append(Paragraph(
            f'{cnum}. Supplier must provide original tax invoice along with delivery of goods.',
            s_tc))

        elements.append(Spacer(1, 8))

        # ── APPROVED BY ──────────────────────────────────────────────
        s_appr = ParagraphStyle('lpo_appr', fontName='Helvetica-Bold',
                                fontSize=8, spaceAfter=1)
        s_appr_n = ParagraphStyle('lpo_apprn', fontName='Helvetica',
                                  fontSize=8, spaceAfter=1)
        elements.append(Paragraph('<b>Approved By</b>', s_appr))
        elements.append(Paragraph('Hesham Youssef', s_appr_n))
        elements.append(Paragraph('Managing Partner', s_appr_n))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(
            'Signature: ___________________________________', s_appr_n))

        doc.build(elements, onFirstPage=_draw_bg, onLaterPages=_draw_bg)
        return output_path

    def generate_invoice(self, invoice_data, output_path):
        """Generate tax invoice PDF"""
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                              topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []

        # Header
        self._add_header(elements, "TAX INVOICE")

        # Invoice details
        details = [
            ["Invoice No:", invoice_data.get('invoice_id', '')],
            ["Invoice Date:", invoice_data.get('invoice_date', '')],
            ["Due Date:", invoice_data.get('due_date', '')],
            ["LPO Reference:", invoice_data.get('lpo_id', '')],
            ["Vendor Invoice No:", invoice_data.get('supplier_invoice_number', '')]
        ]

        table = Table(details, colWidths=[1.5*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 12))

        # Vendor info
        self._add_supplier_info(elements, invoice_data.get('supplier', {}))

        # Line items
        self._add_line_items_table(elements, invoice_data.get('line_items', []))

        # Totals
        self._add_totals(
            elements,
            invoice_data.get('subtotal', 0),
            invoice_data.get('vat_percent', 5),
            invoice_data.get('vat_amount', 0),
            invoice_data.get('total_amount', 0)
        )

        # Payment status
        if invoice_data.get('status'):
            elements.append(Spacer(1, 12))
            elements.append(Paragraph(
                f"<b>Status:</b> {invoice_data['status']}",
                self.styles['Normal']
            ))

        doc.build(elements)
        return output_path

    def generate_delivery_order(self, delivery_data, output_path):
        """Generate delivery order PDF"""
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                              topMargin=0.5*inch, bottomMargin=0.5*inch)
        elements = []

        # Header
        self._add_header(elements, "DELIVERY ORDER")

        # Delivery details
        details = [
            ["Delivery Order No:", delivery_data.get('delivery_id', '')],
            ["Delivery Date:", delivery_data.get('delivery_date', '')],
            ["LPO Reference:", delivery_data.get('lpo_id', '')],
            ["Received By:", delivery_data.get('received_by', '')],
            ["Status:", delivery_data.get('status', '')]
        ]

        table = Table(details, colWidths=[1.5*inch, 2.5*inch])
        table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 12))

        # Line items with condition
        elements.append(Paragraph("Delivered Items", self.styles['SectionHeader']))

        header = ["#", "Description", "Qty Delivered", "Unit", "Condition"]
        col_widths = [0.3*inch, 3.5*inch, 1*inch, 0.7*inch, 1*inch]

        data = [header]
        for i, item in enumerate(delivery_data.get('line_items', []), 1):
            row = [
                str(i),
                item.get('description', ''),
                str(item.get('quantity_delivered', '')),
                item.get('unit', ''),
                item.get('condition', 'Good')
            ]
            data.append(row)

        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#f9fafb')]),
        ]))

        elements.append(table)

        # Notes
        if delivery_data.get('notes'):
            elements.append(Spacer(1, 12))
            elements.append(Paragraph("Notes", self.styles['SectionHeader']))
            elements.append(Paragraph(delivery_data['notes'], self.styles['Normal']))

        doc.build(elements)
        return output_path

    def generate_bpv(self, bpv_data, output_path):
        """Generate Bank Payment Voucher PDF"""
        doc = SimpleDocTemplate(output_path, pagesize=A4,
                              topMargin=0.4*inch, bottomMargin=0.4*inch,
                              leftMargin=0.5*inch, rightMargin=0.5*inch)
        elements = []

        # Company header
        elements.append(Paragraph(f"<b>{self.company_name}</b>",
                                 ParagraphStyle('CompHeader', fontSize=12, alignment=TA_CENTER)))
        elements.append(Paragraph(f"TRN: {self.company_trn}",
                                 ParagraphStyle('CompTRN', fontSize=9, alignment=TA_CENTER)))
        elements.append(Paragraph("P.O BOX -88593  DUBAI  UNITED ARAB EMIRATES",
                                 ParagraphStyle('CompAddr', fontSize=9, alignment=TA_CENTER)))
        elements.append(Paragraph("TEL 04 -8843367",
                                 ParagraphStyle('CompTel', fontSize=9, alignment=TA_CENTER)))
        elements.append(Spacer(1, 12))

        # Title
        elements.append(Paragraph("<b>BANK PAYMENT VOUCHER</b>",
                                 ParagraphStyle('Title', fontSize=14, alignment=TA_CENTER,
                                               textColor=colors.HexColor('#1a1a1a'))))
        elements.append(Spacer(1, 10))

        # BPV Info box
        bpv_no = bpv_data.get('bpvNo', '')
        date = bpv_data.get('date', '')
        pdc_type = bpv_data.get('pdcType', 'PDC')

        # Create style for bold labels
        bold_style = ParagraphStyle('BoldLabel', fontSize=10, fontName='Helvetica-Bold')

        info_data = [
            [Paragraph("BPV NO:", bold_style), bpv_no, Paragraph(pdc_type, bold_style)],
            [Paragraph("Date:", bold_style), date, ""],
        ]

        info_table = Table(info_data, colWidths=[1*inch, 1.5*inch, 1*inch])
        info_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BACKGROUND', (2, 0), (2, 0),
             colors.HexColor('#3b82f6') if pdc_type == 'PDC' else colors.HexColor('#f97316')),
            ('TEXTCOLOR', (2, 0), (2, 0), colors.white),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(info_table)
        elements.append(Spacer(1, 12))

        # Line items table
        header = ["SR.NO", "DESCRIPTION", "COMPANY NAME", "CHQ", "CHQ DATE", "DEBIT", "CREDIT"]
        col_widths = [0.5*inch, 2.5*inch, 1.3*inch, 0.6*inch, 0.8*inch, 0.8*inch, 0.8*inch]

        # Style for wrapping text in cells
        cell_style = ParagraphStyle('CellWrap', fontSize=8, fontName='Helvetica')

        data = [header]

        line_items = bpv_data.get('lineItems', [])
        for item in line_items:
            row = [
                str(item.get('srNo', '')),
                Paragraph(item.get('description', ''), cell_style),
                Paragraph(item.get('companyName', ''), cell_style),
                item.get('chequeNo', ''),
                item.get('chequeDate', ''),
                f"{float(item.get('debit', 0)):,.2f}" if item.get('debit') else '',
                f"{float(item.get('credit', 0)):,.2f}" if item.get('credit') else ''
            ]
            data.append(row)

        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            # Header
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5e7eb')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Body
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 8),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # SR.NO
            ('ALIGN', (3, 1), (4, -1), 'CENTER'),  # CHQ columns
            ('ALIGN', (5, 1), (-1, -1), 'RIGHT'),  # Amount columns
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 1), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 4),

            # Grid
            ('GRID', (0, 0), (-1, -1), 1, colors.black),
            ('BOX', (0, 0), (-1, -1), 2, colors.black),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 8))

        # Total row
        total_amount = bpv_data.get('totalAmount', 0)
        total_label_style = ParagraphStyle('TotalLabel', fontSize=10, fontName='Helvetica-Bold')
        total_data = [[
            "",
            Paragraph("TOTAL AMOUNT AED:", total_label_style),
            Paragraph(f"{float(total_amount):,.2f}", total_label_style)
        ]]
        total_table = Table(total_data, colWidths=[4.3*inch, 1.8*inch, 1.2*inch])
        total_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (1, 0), (1, 0), 'RIGHT'),
            ('ALIGN', (2, 0), (2, 0), 'CENTER'),
            ('BACKGROUND', (2, 0), (2, 0), colors.HexColor('#fef08a')),  # Yellow highlight
            ('GRID', (1, 0), (-1, -1), 1, colors.black),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(total_table)
        elements.append(Spacer(1, 20))

        # Signature section (2 columns: Received By on left, Approved By on right)
        signature_data = [
            ["Received By", "Approved By"],
            ["", ""],
            ["_______________", "_______________"]
        ]

        sig_table = Table(signature_data, colWidths=[3.3*inch, 3.3*inch])
        sig_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('TOPPADDING', (0, 2), (-1, 2), 30),  # Space for signature
        ]))
        elements.append(sig_table)

        doc.build(elements)
        return output_path


# CLI for testing
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 4:
        print("Usage: python pdf_generator.py <quotation|lpo|invoice|delivery> <data.json> <output.pdf>")
        sys.exit(1)

    doc_type = sys.argv[1]
    data_file = sys.argv[2]
    output_file = sys.argv[3]

    with open(data_file, 'r') as f:
        data = json.load(f)

    generator = PDFGenerator()

    if doc_type == 'quotation':
        generator.generate_quotation(data, output_file)
    elif doc_type == 'lpo':
        generator.generate_lpo(data, output_file)
    elif doc_type == 'invoice':
        generator.generate_invoice(data, output_file)
    elif doc_type == 'delivery':
        generator.generate_delivery_order(data, output_file)
    else:
        print(f"Unknown document type: {doc_type}")
        sys.exit(1)

    print(f"PDF generated: {output_file}")
