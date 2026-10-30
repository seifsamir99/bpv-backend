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


def _opt_float(value):
    """Parse an optional numeric override. Returns 0.0 for None/''/garbage so
    callers can treat "not set" and "zero" the same way — a zero-value override
    is meaningless on a purchase order."""
    try:
        return float(value) if value not in (None, '') else 0.0
    except (TypeError, ValueError):
        return 0.0


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

        # Employee guide styles
        self.styles.add(ParagraphStyle(
            name='GuideTitle',
            parent=self.styles['Heading1'],
            fontSize=26,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=10,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold',
        ))
        self.styles.add(ParagraphStyle(
            name='GuideSubtitle',
            parent=self.styles['Normal'],
            fontSize=13,
            textColor=colors.HexColor('#6b7280'),
            spaceAfter=6,
            alignment=TA_CENTER,
        ))
        self.styles.add(ParagraphStyle(
            name='ChapterTitle',
            parent=self.styles['Heading1'],
            fontSize=18,
            textColor=colors.HexColor('#2563eb'),
            spaceAfter=10,
            spaceBefore=24,
            fontName='Helvetica-Bold',
        ))
        self.styles.add(ParagraphStyle(
            name='GuideSection',
            parent=self.styles['Heading2'],
            fontSize=13,
            textColor=colors.HexColor('#1a1a1a'),
            spaceAfter=6,
            spaceBefore=16,
            fontName='Helvetica-Bold',
        ))
        self.styles.add(ParagraphStyle(
            name='GuideBody',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#374151'),
            spaceAfter=8,
            leading=15,
        ))
        self.styles.add(ParagraphStyle(
            name='GuideBullet',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#374151'),
            spaceAfter=4,
            leading=14,
            leftIndent=16,
            bulletIndent=6,
        ))
        self.styles.add(ParagraphStyle(
            name='GuideCallout',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#1e40af'),
            spaceAfter=8,
            leading=14,
            leftIndent=10,
            rightIndent=10,
            borderPadding=(6, 8, 6, 8),
        ))
        self.styles.add(ParagraphStyle(
            name='GuideTableHeader',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.white,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
        ))
        self.styles.add(ParagraphStyle(
            name='GuideTableCell',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=colors.HexColor('#374151'),
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
        # Look in same dir first (Railway: execution/ is deployed as /app),
        # then parent dir (local: assets/ is sibling of execution/)
        _assets = os.path.join(base_dir, 'assets')
        if not os.path.isdir(_assets):
            _assets = os.path.join(base_dir, '..', 'assets')
        lh_full  = os.path.join(_assets, 'letterhead.jpg')
        lh_hdr   = os.path.join(_assets, 'letterhead_header.jpg')

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
        discount         = float(lpo.get('discount', lpo.get('Discount', 0)) or 0)
        extra_terms      = lpo.get('extra_terms', lpo.get('Extra Terms', [])) or []
        notes            = str(lpo.get('notes', lpo.get('Notes', '')) or '').strip()
        # Optional lump-sum overrides typed on the /lpo page. Only one is ever
        # set: total_override is VAT-inclusive, subtotal_override is ex-VAT.
        sub_override     = _opt_float(lpo.get('subtotal_override'))
        total_override   = _opt_float(lpo.get('total_override'))
        # How the order is priced: per unit rate, per line total, or one lump
        # sum for the whole order. Anything unrecognised (including LPOs saved
        # before this existed) falls back to the original unit-rate layout.
        pricing_mode     = str(lpo.get('pricing_mode') or 'unit').lower()
        if pricing_mode not in ('unit', 'total', 'lump'):
            pricing_mode = 'unit'
        # Print option, not stored LPO data: stamp Hesham's signature or leave
        # the signature line blank for a wet signature.
        signed           = lpo.get('signed') in (True, 'true', '1', 1)
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

        # ── LINE ITEMS TABLE ────────────────────────────
        # The columns depend on how the LPO was priced (pricing_mode):
        #   'unit'  SN | Description | Qty | Unit | Rate | Total   (Rate x Qty)
        #   'total' SN | Description | Qty | Unit | Total          (per-line lump)
        #   'lump'  SN | Description                               (one figure for
        #           the whole order — the price lives in the totals box only)
        # Widths always add up to 6.77" so the table keeps the same footprint.
        if pricing_mode == 'lump':
            headers = ['SN', 'Description']
            col_w   = [0.35*inch, 6.42*inch]
        elif pricing_mode == 'total':
            headers = ['SN', 'Description', 'Qty', 'Unit', 'Total (AED)']
            col_w   = [0.35*inch, 4.22*inch, 0.5*inch, 0.5*inch, 1.2*inch]
        else:
            headers = ['SN', 'Description', 'Qty', 'Unit', 'Rate (AED)', 'Total (AED)']
            col_w   = [0.35*inch, 3.22*inch, 0.5*inch, 0.5*inch, 1.1*inch, 1.1*inch]

        s_hdr = ParagraphStyle('lpo_hdr', fontName='Helvetica-Bold',
                               fontSize=9, alignment=TA_CENTER)
        s_item = ParagraphStyle('lpo_item', fontName='Helvetica',
                                fontSize=7.5, leading=9.5)
        s_qty = ParagraphStyle('lpo_qty', fontName='Helvetica', fontSize=7.5,
                               leading=9.5, alignment=TA_CENTER)
        s_tot_lbl = ParagraphStyle('lpo_tlbl', fontName='Helvetica-Bold',
                                   fontSize=9, alignment=TA_RIGHT, leading=12)
        s_tot_val = ParagraphStyle('lpo_tval', fontName='Helvetica-Bold',
                                   fontSize=9, alignment=TA_RIGHT)

        tbl_data = [[Paragraph(h, s_hdr) for h in headers]]

        subtotal = 0
        for i, item in enumerate(line_items, 1):
            up   = float(item.get('unit_price',  item.get('Unit Price (AED)', 0)) or 0)
            # Qty may be text such as "Per Sqft" (a rate-only line). In 'unit'
            # mode such a line has no Total and adds nothing; in 'total' mode
            # the typed Total still counts (mirrors lpo_records_service and
            # calcTotals in LPOPage.jsx).
            raw_qty = item.get('quantity', item.get('Quantity Ordered', 0))
            try:
                qty, qty_text = float(raw_qty or 0), None
            except (TypeError, ValueError):
                qty, qty_text = 0, str(raw_qty).strip()
            if qty_text and pricing_mode == 'unit':
                tot = 0
            else:
                tot = float(item.get('total_price', item.get('Total Price (AED)', up * qty)) or 0)
            subtotal += tot
            qty_cell = (Paragraph(qty_text, s_qty) if qty_text
                        else str(int(qty)) if qty == int(qty) else str(qty))
            desc = item.get('description', item.get('Item Description', ''))
            unit = item.get('unit', item.get('Unit', ''))
            row = [str(i), Paragraph(desc, s_item)]
            if pricing_mode != 'lump':
                row += [qty_cell, unit]
                if pricing_mode == 'unit':
                    row.append(f'{up:,.2f}')
                row.append('-' if qty_text and pricing_mode == 'unit' else f'{tot:,.2f}')
            tbl_data.append(row)

        # A lump-sum order carries no per-line money, so its value can only come
        # from the figure the office typed into the totals box.
        if pricing_mode == 'lump':
            subtotal = 0

        # A lump-sum override replaces the figure derived from the line rates.
        # An ex-VAT override just replaces the subtotal; a VAT-inclusive one is
        # worked BACKWARDS (same rule as payment certificates) so the bottom
        # line is exactly the figure the office typed, to the cent.
        if total_override:
            total_amount = round(total_override, 2)
            net_amount   = round(total_amount / (1 + vat_percent / 100), 2)
            vat_amount   = round(total_amount - net_amount, 2)
            subtotal     = round(net_amount + discount, 2)
        else:
            if sub_override:
                subtotal = round(sub_override, 2)
            net_amount   = subtotal - discount
            vat_amount   = net_amount * vat_percent / 100
            total_amount = net_amount + vat_amount
        vat_label = f'VAT {int(vat_percent) if vat_percent == int(vat_percent) else vat_percent}%:'

        n_cols = len(col_w)

        items_table = Table(tbl_data, colWidths=col_w)
        item_style = [
            # Header row
            ('BACKGROUND',    (0, 0), (-1, 0), colors.HexColor('#e0e0e0')),
            ('FONTNAME',      (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN',         (0, 0), (-1, 0), 'CENTER'),
            ('TOPPADDING',    (0, 0), (-1, 0), 4),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 4),
            # Item rows
            ('FONTNAME',      (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE',      (0, 1), (-1, -1), 7.5),
            ('TOPPADDING',    (0, 1), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
            ('ALIGN',  (0, 1), (0, -1), 'CENTER'),   # SN
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.75, colors.black),
            ('BOX',  (0, 0), (-1, -1), 1.5,  colors.black),
        ]
        if pricing_mode != 'lump':
            item_style += [
                ('ALIGN', (2, 1), (3, -1), 'CENTER'),          # Qty, Unit
                ('ALIGN', (4, 1), (n_cols - 1, -1), 'RIGHT'),  # money columns
            ]
        items_table.setStyle(TableStyle(item_style))
        elements.append(items_table)

        # ── TOTALS BOX ────────────────────────────────
        # Its own right-aligned table sitting directly under the items table, so
        # the columns above can change with pricing_mode without the totals
        # having to follow them. When a discount applies, show
        # Subtotal → Discount → Grand Total (net); otherwise just Grand Total.
        # VAT is always charged on the net amount.
        tot_rows = []
        if discount > 0:
            tot_rows.append([Paragraph('Subtotal<br/>(AED):', s_tot_lbl),
                             Paragraph(f'{subtotal:,.2f}', s_tot_val)])
            tot_rows.append([Paragraph('Discount<br/>(AED):', s_tot_lbl),
                             Paragraph(f'-{discount:,.2f}', s_tot_val)])
        tot_rows.append([Paragraph('Grand Total<br/>(AED):', s_tot_lbl),
                         Paragraph(f'{net_amount:,.2f}', s_tot_val)])
        tot_rows.append([Paragraph(vat_label, s_tot_lbl),
                         Paragraph(f'{vat_amount:,.2f}', s_tot_val)])
        tot_rows.append([Paragraph('<b>TOTAL<br/>(AED):</b>', s_tot_lbl),
                         Paragraph(f'<b>{total_amount:,.2f}</b>', s_tot_val)])

        totals_table = Table(tot_rows, colWidths=[1.1*inch, 1.1*inch], hAlign='RIGHT')
        totals_table.setStyle(TableStyle([
            ('BOX',           (0, 0), (-1, -1), 1,   colors.black),
            ('INNERGRID',     (0, 0), (-1, -1), 0.5, colors.black),
            ('BACKGROUND',    (0, -1), (-1, -1), colors.HexColor('#e0e0e0')),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
        ]))
        elements.append(totals_table)
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
        cnum += 1

        # User-added custom terms & conditions (XML-escaped for reportlab)
        for term in extra_terms:
            term = str(term).strip()
            if not term:
                continue
            safe = term.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            elements.append(Paragraph(f'{cnum}. {safe}', s_tc))
            cnum += 1

        # ── NOTES ────────────────────────────────────────────────────
        # Free text from the Notes box on the /lpo page; line breaks kept.
        if notes:
            safe = notes.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            elements.append(Spacer(1, 4))
            elements.append(Paragraph('<b>Notes:</b>', s_tc_hdr))
            elements.append(Paragraph(safe.replace('\n', '<br/>'), s_tc))

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
        if signed:
            # "Signed" print: Hesham's scanned signature beside the label,
            # in place of the blank line.
            sig_path = os.path.join(_assets, 'hesham_signature_lpo.png')
            if not os.path.exists(sig_path):
                raise ValueError('Signature image not found (assets/hesham_signature_lpo.png)')
            with PILImage.open(sig_path) as _img:
                _sw, _sh = _img.size
            sig_h = 0.55 * inch
            sig_w = min(sig_h * _sw / _sh, 2.4 * inch)
            sig_h = sig_w * _sh / _sw
            sig_row = Table(
                [[Paragraph('Signature:', s_appr_n), Image(sig_path, width=sig_w, height=sig_h)]],
                colWidths=[0.6 * inch, sig_w + 4], hAlign='LEFT')
            sig_row.setStyle(TableStyle([
                ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
                ('TOPPADDING', (0, 0), (-1, -1), 0),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
            ]))
            elements.append(sig_row)
        else:
            elements.append(Paragraph(
                'Signature: ___________________________________', s_appr_n))

        doc.build(elements, onFirstPage=_draw_bg, onLaterPages=_draw_bg)
        return output_path

    def generate_job_order(self, job_order_data, output_path):
        """Generate Job Order PDF (subcontractor work order) matching the
        company's standard format — same letterhead/table conventions as
        generate_lpo, but with a free-text Terms & Conditions block and a
        two-party (Newell / subcontractor) signature block."""
        import re
        from PIL import Image as PILImage
        base_dir = os.path.dirname(os.path.abspath(__file__))

        _assets = os.path.join(base_dir, 'assets')
        if not os.path.isdir(_assets):
            _assets = os.path.join(base_dir, '..', 'assets')
        lh_full  = os.path.join(_assets, 'letterhead.jpg')
        lh_hdr   = os.path.join(_assets, 'letterhead_header.jpg')

        try:
            with PILImage.open(lh_hdr) as _img:
                _iw, _ih = _img.size
            top_margin = A4[0] * (_ih / _iw) + 4
        except Exception:
            top_margin = 1.8 * inch

        try:
            with PILImage.open(lh_full) as _img:
                _fw, _fh = _img.size
            footer_frac = 0.10
            bottom_margin = A4[1] * footer_frac + 4
        except Exception:
            bottom_margin = 0.9 * inch

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=top_margin, bottomMargin=bottom_margin,
                                leftMargin=0.5*inch, rightMargin=0.5*inch)
        page_w = A4[0] - inch
        elements = []

        _lf = lh_full if os.path.exists(lh_full) else lh_hdr
        def _draw_bg(canvas, doc):
            canvas.saveState()
            canvas.drawImage(_lf, 0, 0, width=A4[0], height=A4[1],
                             preserveAspectRatio=False, mask='auto')
            canvas.restoreState()

        elements.append(Spacer(1, 2))

        # ── EXTRACT DATA ─────────────────────────────────────────────
        jo            = job_order_data.get('job_order', job_order_data)
        subcontractor = job_order_data.get('subcontractor', {})
        line_items    = job_order_data.get('line_items', [])
        terms_text    = job_order_data.get('terms_text', '') or ''

        ref_number    = jo.get('ref_number', '')
        jo_date       = self._format_date(jo.get('date', ''))
        project       = jo.get('project', '')
        sub_scope     = jo.get('sub', '')
        quote_date    = self._format_date(jo.get('quote_date', ''))
        discount      = float(jo.get('discount', 0) or 0)
        vat_enabled   = bool(jo.get('vat_enabled', True))
        vat_percent   = float(jo.get('vat_percent', 5) or 0)
        has_unit_pricing = bool(jo.get('has_unit_pricing', False))
        # A schedule-of-rates job order has no agreed quantities, so no
        # meaningful grand total: the whole totals block is left off.
        show_totals   = bool(jo.get('show_totals', True))

        sub_name    = subcontractor.get('name', '')
        sub_contact = subcontractor.get('contact', '')
        sub_phone   = subcontractor.get('phone', '')
        sub_address = subcontractor.get('address', '')

        # ── TITLE ─────────────────────────────────────────────────────
        s_title = ParagraphStyle('jo_title', fontName='Helvetica-Bold', fontSize=14,
                                 alignment=TA_CENTER, spaceAfter=6)
        elements.append(Paragraph('Job Order', s_title))

        # ── REF / DATE ────────────────────────────────────────────────
        s_lbl = ParagraphStyle('jo_lbl', fontName='Helvetica-Bold', fontSize=9)
        s_val = ParagraphStyle('jo_val', fontName='Helvetica', fontSize=9)

        ref_tbl = Table(
            [[Paragraph(f'Ref.{ref_number}', s_val), Paragraph(jo_date, s_val)]],
            colWidths=[page_w * 0.7, page_w * 0.3])
        ref_tbl.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN',    (1, 0), (1, 0), 'RIGHT'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ]))
        elements.append(ref_tbl)
        elements.append(Spacer(1, 4))

        # ── "TO," BLOCK ───────────────────────────────────────────────
        elements.append(Paragraph('To,', s_val))
        elements.append(Paragraph(f'M/s. {sub_name}', s_lbl))
        if sub_address:
            elements.append(Paragraph(sub_address, s_val))
        if sub_phone:
            elements.append(Paragraph(f'Tel. No. {sub_phone}', s_val))
        if sub_contact:
            elements.append(Paragraph(f'Att: {sub_contact}', s_val))
        elements.append(Spacer(1, 4))

        # ── PROJECT / SUB BOX ────────────────────────────────────────
        if project or sub_scope:
            s_proj = ParagraphStyle('jo_proj', fontName='Helvetica', fontSize=9, leading=13)
            proj_lines = []
            if project:
                proj_lines.append(Paragraph(f'<b>PROJECT:</b> {project}', s_proj))
            if sub_scope:
                proj_lines.append(Paragraph(f'<b>Sub:</b> {sub_scope}', s_proj))
            proj_tbl = Table([[proj_lines]], colWidths=[page_w])
            proj_tbl.setStyle(TableStyle([
                ('BOX',           (0, 0), (-1, -1), 1, colors.black),
                ('TOPPADDING',    (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING',   (0, 0), (-1, -1), 6),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 6),
            ]))
            elements.append(proj_tbl)
            elements.append(Spacer(1, 4))

        # ── INTRO LINE ───────────────────────────────────────────────
        intro = ('Kindly carry out the following work as per the agreed contract price '
                 'mentioned below at aforesaid project')
        if quote_date:
            intro += f' and as Per your agreed Quotation dated {quote_date}.'
        else:
            intro += '.'
        elements.append(Paragraph(intro, s_val))
        elements.append(Spacer(1, 6))

        # ── LINE ITEMS TABLE ─────────────────────────────────────────
        s_hdr = ParagraphStyle('jo_hdr', fontName='Helvetica-Bold', fontSize=9, alignment=TA_CENTER)
        s_item = ParagraphStyle('jo_item', fontName='Helvetica', fontSize=7.5, leading=9.5)
        s_qty = ParagraphStyle('jo_qty', fontName='Helvetica', fontSize=7.5, leading=9.5,
                               alignment=TA_CENTER)
        s_tot_lbl = ParagraphStyle('jo_tlbl', fontName='Helvetica-Bold', fontSize=9,
                                   alignment=TA_RIGHT, leading=12)
        s_tot_val = ParagraphStyle('jo_tval', fontName='Helvetica-Bold', fontSize=9, alignment=TA_RIGHT)

        if has_unit_pricing:
            col_w = [0.35*inch, 3.72*inch, 0.6*inch, 1.1*inch, 1.1*inch]
            tbl_data = [[
                Paragraph('SN', s_hdr), Paragraph('Description', s_hdr),
                Paragraph('Qty', s_hdr), Paragraph('Unit Price (AED)', s_hdr),
                Paragraph('Amount (AED)', s_hdr),
            ]]
        else:
            col_w = [0.35*inch, 5.32*inch, 1.2*inch]
            tbl_data = [[
                Paragraph('SN', s_hdr), Paragraph('Description', s_hdr),
                Paragraph('Amount (AED)', s_hdr),
            ]]

        n_amount_col = len(col_w) - 1  # index of the Amount column

        subtotal = 0
        for i, item in enumerate(line_items, 1):
            # Qty may be text such as "Per Sqft" on a rate-only line: no amount.
            raw_qty = item.get('quantity', 0)
            try:
                qty, qty_text = float(raw_qty or 0), None
            except (TypeError, ValueError):
                qty, qty_text = 0, str(raw_qty).strip()
            up  = float(item.get('unit_price', 0) or 0)
            amt = 0 if qty_text else float(item.get('amount', up * qty))
            subtotal += amt
            desc = item.get('description', '')
            if has_unit_pricing:
                if qty_text:
                    tbl_data.append([str(i), Paragraph(desc, s_item),
                                     Paragraph(qty_text, s_qty), f'{up:,.2f}', '-'])
                    continue
                qty_str = str(int(qty)) if qty == int(qty) else str(qty)
                tbl_data.append([str(i), Paragraph(desc, s_item), qty_str,
                                  f'{up:,.2f}', f'{amt:,.2f}'])
            else:
                tbl_data.append([str(i), Paragraph(desc, s_item), f'{amt:,.2f}'])

        net_amount = subtotal - discount
        vat_amount = (net_amount * vat_percent / 100) if vat_enabled else 0
        total_amount = net_amount + vat_amount
        vat_label = f'VAT {int(vat_percent) if vat_percent == int(vat_percent) else vat_percent}%:'

        n_items = len(tbl_data)  # header + item rows

        blank_row = lambda: ['' for _ in range(len(col_w) - 2)]
        if show_totals:
            if discount > 0:
                tbl_data.append(blank_row() + [Paragraph('Subtotal<br/>(AED):', s_tot_lbl),
                                                Paragraph(f'{subtotal:,.2f}', s_tot_val)])
                tbl_data.append(blank_row() + [Paragraph('Discount<br/>(AED):', s_tot_lbl),
                                                Paragraph(f'-{discount:,.2f}', s_tot_val)])
            if vat_enabled:
                tbl_data.append(blank_row() + [Paragraph('Grand Total<br/>(AED):', s_tot_lbl),
                                                Paragraph(f'{net_amount:,.2f}', s_tot_val)])
                tbl_data.append(blank_row() + [Paragraph(vat_label, s_tot_lbl),
                                                Paragraph(f'{vat_amount:,.2f}', s_tot_val)])
                tbl_data.append(blank_row() + [Paragraph('<b>TOTAL<br/>(AED):</b>', s_tot_lbl),
                                                Paragraph(f'<b>{total_amount:,.2f}</b>', s_tot_val)])
            else:
                tbl_data.append(blank_row() + [Paragraph('<b>Total<br/>(AED):</b>', s_tot_lbl),
                                                Paragraph(f'<b>{total_amount:,.2f}</b>', s_tot_val)])

        n = len(tbl_data)
        c0, c1 = len(col_w) - 2, len(col_w) - 1  # totals label/value columns
        items_table = Table(tbl_data, colWidths=col_w)
        style_cmds = [
            ('BACKGROUND',    (0, 0), (-1, 0),        colors.HexColor('#e0e0e0')),
            ('FONTNAME',      (0, 0), (-1, 0),        'Helvetica-Bold'),
            ('ALIGN',         (0, 0), (-1, 0),        'CENTER'),
            ('TOPPADDING',    (0, 0), (-1, 0),        4),
            ('BOTTOMPADDING', (0, 0), (-1, 0),        4),
            ('FONTNAME',      (0, 1), (-1, n_items-1), 'Helvetica'),
            ('FONTSIZE',      (0, 1), (-1, n_items-1), 7.5),
            ('TOPPADDING',    (0, 1), (-1, n_items-1), 2),
            ('BOTTOMPADDING', (0, 1), (-1, n_items-1), 2),
            ('ALIGN',  (0, 1), (0, n_items-1), 'CENTER'),
            ('ALIGN',  (c0, 1), (c1, n_items-1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, n_items-1), 0.75, colors.black),
            ('BOX',  (0, 0), (-1, n_items-1), 1.5,  colors.black),
        ]
        if n > n_items:  # totals rows present — box and shade them
            style_cmds += [
                ('BOX',       (c0, n_items), (c1, n-1),   1,   colors.black),
                ('INNERGRID', (c0, n_items), (c1, n-1),   0.5, colors.black),
                ('BACKGROUND', (c0, n-1), (c1, n-1), colors.HexColor('#e0e0e0')),
                ('TOPPADDING',    (c0, n_items), (c1, n-1), 3),
                ('BOTTOMPADDING', (c0, n_items), (c1, n-1), 3),
                ('RIGHTPADDING',  (c0, n_items), (c1, n-1), 6),
            ]
        if has_unit_pricing:
            style_cmds.append(('ALIGN', (2, 1), (2, n_items-1), 'CENTER'))  # Qty
        items_table.setStyle(TableStyle(style_cmds))
        elements.append(items_table)
        elements.append(Spacer(1, 8))

        # ── TERMS & CONDITIONS (free text, edited per job order) ──────
        s_tc_hdr = ParagraphStyle('jo_tch', fontName='Helvetica-Bold', fontSize=8, spaceAfter=2)
        s_tc_clause = ParagraphStyle('jo_tcc', fontName='Helvetica-Bold', fontSize=8,
                                     spaceBefore=4, spaceAfter=1, leading=11)
        s_tc = ParagraphStyle('jo_tc', fontName='Helvetica', fontSize=8,
                              leftIndent=10, spaceAfter=1, leading=11)

        if terms_text.strip():
            elements.append(Paragraph('<b>Terms &amp; Conditions</b>', s_tc_hdr))
            clause_re = re.compile(r'^\d+[.)]')
            for raw_line in terms_text.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                safe = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                if clause_re.match(line):
                    elements.append(Paragraph(f'<b>{safe}</b>', s_tc_clause))
                elif line.startswith(('-', '•', '*')):
                    elements.append(Paragraph(safe.lstrip('-•* ').strip(), s_tc))
                else:
                    elements.append(Paragraph(safe, s_tc))

        elements.append(Spacer(1, 10))

        # ── SIGNATURE BLOCK (Newell / subcontractor, two columns) ─────
        s_sig_hdr = ParagraphStyle('jo_sigh', fontName='Helvetica-Bold', fontSize=8, spaceAfter=2)
        s_sig = ParagraphStyle('jo_sig', fontName='Helvetica', fontSize=8, spaceAfter=1)

        left_col = [
            Paragraph('For: NEWELL ELECTROMECHANICAL WORKS LLC', s_sig_hdr),
            Spacer(1, 12),
            Paragraph('Mr. Hesham Youssef', s_sig),
            Paragraph('Managing Partner', s_sig),
            Spacer(1, 10),
            Paragraph('_________________', s_sig),
        ]
        right_col = [
            Paragraph('Accepted by:', s_sig_hdr),
            Spacer(1, 12),
            Paragraph(f'M/S {sub_name}', s_sig),
            Spacer(1, 12),
            Spacer(1, 10),
            Paragraph('______________________', s_sig),
        ]
        sig_tbl = Table([[left_col, right_col]], colWidths=[page_w * 0.5, page_w * 0.5])
        sig_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(sig_tbl)

        doc.build(elements, onFirstPage=_draw_bg, onLaterPages=_draw_bg)
        return output_path

    def generate_payment_certificate(self, cert_data, output_path):
        """Generate a Sub-Contractor Payment Certificate PDF.

        Newell issues this DOWNWARD to a subcontractor. Same letterhead and
        table conventions as generate_job_order; the body is the certification
        block rather than a priced scope.

        cert_data is what payment_cert_service.certificate_pdf_payload()
        returns: {'certificate': {...}, 'contract': {...}, 'cumulative': {...}}.
        """
        from PIL import Image as PILImage
        base_dir = os.path.dirname(os.path.abspath(__file__))

        _assets = os.path.join(base_dir, 'assets')
        if not os.path.isdir(_assets):
            _assets = os.path.join(base_dir, '..', 'assets')
        lh_full = os.path.join(_assets, 'letterhead.jpg')
        lh_hdr = os.path.join(_assets, 'letterhead_header.jpg')

        try:
            with PILImage.open(lh_hdr) as _img:
                _iw, _ih = _img.size
            top_margin = A4[0] * (_ih / _iw) + 4
        except Exception:
            top_margin = 1.8 * inch

        try:
            with PILImage.open(lh_full) as _img:
                _fw, _fh = _img.size
            bottom_margin = A4[1] * 0.10 + 4
        except Exception:
            bottom_margin = 0.9 * inch

        doc = SimpleDocTemplate(output_path, pagesize=A4,
                                topMargin=top_margin, bottomMargin=bottom_margin,
                                leftMargin=0.5*inch, rightMargin=0.5*inch)
        page_w = A4[0] - inch
        elements = []

        _lf = lh_full if os.path.exists(lh_full) else lh_hdr
        def _draw_bg(canvas, doc):
            canvas.saveState()
            canvas.drawImage(_lf, 0, 0, width=A4[0], height=A4[1],
                             preserveAspectRatio=False, mask='auto')
            canvas.restoreState()

        # -- EXTRACT DATA ---------------------------------------------
        cert = cert_data.get('certificate', {})
        contract = cert_data.get('contract', {})
        cum = cert_data.get('cumulative', {})

        def money(v):
            return '{:,.2f}'.format(float(v or 0))

        vat_pct = float(contract.get('vat_percent', 5) or 0)
        ret_pct = float(contract.get('retention_percent', 0) or 0)
        is_final = str(cert.get('bill_type', 'interim')).lower() == 'final'

        # -- STYLES ----------------------------------------------------
        s_title = ParagraphStyle('pc_title', fontName='Helvetica-Bold', fontSize=13,
                                 alignment=TA_CENTER, spaceAfter=2)
        s_sub = ParagraphStyle('pc_sub', fontName='Helvetica', fontSize=8.5,
                               alignment=TA_CENTER, spaceAfter=6)
        s_lbl = ParagraphStyle('pc_lbl', fontName='Helvetica-Bold', fontSize=8, leading=11)
        s_val = ParagraphStyle('pc_val', fontName='Helvetica', fontSize=8, leading=11)
        s_hdr = ParagraphStyle('pc_hdr', fontName='Helvetica-Bold', fontSize=8,
                               alignment=TA_CENTER, leading=10)
        s_item = ParagraphStyle('pc_item', fontName='Helvetica', fontSize=8, leading=10)
        s_num = ParagraphStyle('pc_num', fontName='Helvetica', fontSize=8,
                               alignment=TA_RIGHT, leading=10)
        s_num_b = ParagraphStyle('pc_numb', fontName='Helvetica-Bold', fontSize=8,
                                 alignment=TA_RIGHT, leading=10)
        s_memo = ParagraphStyle('pc_memo', fontName='Helvetica-Oblique', fontSize=7,
                                leading=9, textColor=colors.HexColor('#444444'))

        GRID = colors.HexColor('#333333')
        SHADE = colors.HexColor('#EFEFEF')

        elements.append(Spacer(1, 2))
        elements.append(Paragraph('SUB-CONTRACTOR PAYMENT CERTIFICATE', s_title))
        if cert.get('ref_number'):
            elements.append(Paragraph('Ref. %s' % cert['ref_number'], s_sub))

        # -- HEADER GRID -----------------------------------------------
        # Left: what the work is. Right: which bill this is.
        bill_type_txt = ('Interim  [ ]&nbsp;&nbsp;&nbsp;Final  [X]' if is_final
                         else 'Interim  [X]&nbsp;&nbsp;&nbsp;Final  [ ]')
        hdr_rows = [
            [Paragraph('PROJECT NAME', s_lbl), Paragraph(contract.get('project', ''), s_val),
             Paragraph('Bill No.', s_lbl), Paragraph(str(cert.get('cert_no', '')), s_val)],
            [Paragraph('SUBCONTRACTOR', s_lbl), Paragraph(contract.get('subcontractor', ''), s_val),
             Paragraph('Invoice Date', s_lbl),
             Paragraph(self._format_date(cert.get('invoice_date', '')), s_val)],
            [Paragraph('SCOPE OF WORK', s_lbl), Paragraph(contract.get('scope', ''), s_val),
             Paragraph('Bill Month', s_lbl), Paragraph(cert.get('bill_month', ''), s_val)],
            [Paragraph('JOB ORDER REF', s_lbl), Paragraph(contract.get('job_order_ref', ''), s_val),
             Paragraph('Bill Type', s_lbl), Paragraph(bill_type_txt, s_val)],
        ]
        hdr_tbl = Table(hdr_rows, colWidths=[page_w*0.19, page_w*0.41, page_w*0.16, page_w*0.24])
        hdr_tbl.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.75, GRID),
            ('BACKGROUND',    (0, 0), (0, -1), SHADE),
            ('BACKGROUND',    (2, 0), (2, -1), SHADE),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ]))
        elements.append(hdr_tbl)
        elements.append(Spacer(1, 8))

        # -- SCOPE ITEMS -----------------------------------------------
        # Shows WHAT the contract covers. Amounts are optional: the user
        # certifies one figure for the month, so per-item splits are only
        # printed when they have actually been entered.
        scope_items = contract.get('scope_items') or []
        scope_amounts = cert.get('scope_amounts') or {}
        if scope_items:
            has_amounts = any(scope_amounts.get(str(i)) for i in range(len(scope_items)))
            rows = [[Paragraph('S.No', s_hdr), Paragraph('Description of Works', s_hdr),
                     Paragraph('Cumulative Amount (AED)', s_hdr)]]
            for i, name in enumerate(scope_items):
                amt = scope_amounts.get(str(i))
                rows.append([
                    Paragraph(str(i + 1), s_hdr),
                    Paragraph(str(name), s_item),
                    Paragraph(money(amt) if amt else '-', s_num),
                ])
            col_w = [0.5*inch, page_w - 0.5*inch - 1.5*inch, 1.5*inch]
            scope_tbl = Table(rows, colWidths=col_w, repeatRows=1)
            scope_tbl.setStyle(TableStyle([
                ('GRID',          (0, 0), (-1, -1), 0.75, GRID),
                ('BACKGROUND',    (0, 0), (-1, 0), SHADE),
                ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING',    (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING',   (0, 0), (-1, -1), 5),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
            ]))
            elements.append(scope_tbl)
            if not has_amounts:
                elements.append(Paragraph(
                    'Scope of the subcontract. This certificate certifies the works as a '
                    'whole for the bill month shown above.', s_memo))
            elements.append(Spacer(1, 8))

        # -- CONTRACT POSITION -----------------------------------------
        pos_rows = [
            [Paragraph('Contract Value (excl. VAT)', s_val),
             Paragraph(money(cum.get('contract_value')), s_num)],
            [Paragraph('Previously Certified', s_val),
             Paragraph(money(cum.get('previously_certified')), s_num)],
            [Paragraph('This Certificate', s_val),
             Paragraph(money(cert.get('amount_ex_vat')), s_num)],
            [Paragraph('Cumulative Certified to Date', s_lbl),
             Paragraph(money(cum.get('cumulative_certified')), s_num_b)],
            [Paragraph('Balance of Contract', s_val),
             Paragraph(money(cum.get('balance_of_contract')), s_num)],
        ]
        pos_tbl = Table(pos_rows, colWidths=[page_w * 0.62, page_w * 0.38])
        pos_tbl.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.75, GRID),
            ('BACKGROUND',    (0, 3), (-1, 3), SHADE),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ]))
        elements.append(pos_tbl)
        elements.append(Spacer(1, 8))

        # -- CERTIFICATION BLOCK ---------------------------------------
        # The face of the document: what is actually being certified for
        # payment this month. Mirrors the order on the reference certificate.
        cert_rows = [
            [Paragraph('Amount Certified (excl. VAT)', s_val), Paragraph('Dhs', s_hdr),
             Paragraph(money(cert.get('amount_ex_vat')), s_num)],
            [Paragraph('Less Retention (%g%%)' % ret_pct, s_val), Paragraph('', s_val),
             Paragraph('(%s)' % money(cert.get('retention_amount')), s_num)],
        ]
        if float(cert.get('other_deduction') or 0) > 0:
            note = cert.get('other_deduction_note') or ''
            label = 'Less Other Deduction' + ((' (%s)' % note) if note else '')
            cert_rows.append([Paragraph(label, s_val), Paragraph('', s_val),
                              Paragraph('(%s)' % money(cert.get('other_deduction')), s_num)])
        cert_rows += [
            [Paragraph('Total Amount', s_lbl), Paragraph('Dhs', s_hdr),
             Paragraph(money(cert.get('total_amount')), s_num_b)],
            [Paragraph('Value Added Tax (%g%%)' % vat_pct, s_val), Paragraph('', s_val),
             Paragraph(money(cert.get('vat_charged')), s_num)],
            [Paragraph('NET CERTIFIED AMOUNT OF THIS PAYMENT', s_lbl), Paragraph('Dhs', s_hdr),
             Paragraph(money(cert.get('net_payable')), s_num_b)],
        ]
        n_last = len(cert_rows) - 1
        n_total = n_last - 2
        cert_tbl = Table(cert_rows, colWidths=[page_w * 0.62, page_w * 0.08, page_w * 0.30])
        cert_tbl.setStyle(TableStyle([
            ('GRID',          (0, 0), (-1, -1), 0.75, GRID),
            ('BACKGROUND',    (0, n_total), (-1, n_total), SHADE),
            ('BACKGROUND',    (0, n_last), (-1, n_last), colors.HexColor('#DCE6F1')),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING',   (0, 0), (-1, -1), 5),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
        ]))
        elements.append(cert_tbl)
        elements.append(Spacer(1, 6))

        # -- MEMO ------------------------------------------------------
        # Without this note the VAT line reads as an arithmetic error to
        # anyone checking it: the invoice contained more VAT than is being
        # paid, because VAT on retained money falls due on release.
        elements.append(Paragraph(
            'Retention held to date: AED %s.' % money(cum.get('retention_held')), s_memo))
        if float(cert.get('retention_amount') or 0) > 0:
            vat_diff = float(cert.get('vat_portion') or 0) - float(cert.get('vat_charged') or 0)
            elements.append(Paragraph(
                'VAT is charged on the amount payable after retention. VAT on the full '
                'certified value is AED %s; the difference of AED %s relates to the '
                'retention held and becomes payable when the retention is released.'
                % (money(cert.get('vat_portion')), money(vat_diff)), s_memo))
        elements.append(Spacer(1, 12))

        # -- SIGNATURE BLOCK -------------------------------------------
        s_sig_hdr = ParagraphStyle('pc_sigh', fontName='Helvetica-Bold', fontSize=8, spaceAfter=2)
        s_sig = ParagraphStyle('pc_sig', fontName='Helvetica', fontSize=8, spaceAfter=1)

        left_col = [
            Paragraph('Certified for: NEWELL ELECTROMECHANICAL WORKS LLC', s_sig_hdr),
            Spacer(1, 12),
            Paragraph('Mr. Hesham Youssef', s_sig),
            Paragraph('Managing Partner', s_sig),
            Spacer(1, 10),
            Paragraph('_________________', s_sig),
        ]
        right_col = [
            Paragraph('Received &amp; Accepted by:', s_sig_hdr),
            Spacer(1, 12),
            Paragraph('M/S %s' % contract.get('subcontractor', ''), s_sig),
            Spacer(1, 12),
            Spacer(1, 10),
            Paragraph('______________________', s_sig),
        ]
        sig_tbl = Table([[left_col, right_col]], colWidths=[page_w * 0.5, page_w * 0.5])
        sig_tbl.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(sig_tbl)

        doc.build(elements, onFirstPage=_draw_bg, onLaterPages=_draw_bg)
        return output_path

    def generate_hr_letter(self, letter_data, output_path):
        """Generate an HR salary/employment certificate on company letterhead"""
        import arabic_reshaper
        from bidi.algorithm import get_display
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from PIL import Image as PILImage

        base_dir = os.path.dirname(os.path.abspath(__file__))
        _assets = os.path.join(base_dir, 'assets')
        if not os.path.isdir(_assets):
            _assets = os.path.join(base_dir, '..', 'assets')
        lh_full = os.path.join(_assets, 'letterhead.jpg')
        lh_hdr  = os.path.join(_assets, 'letterhead_header.jpg')
        _lf = lh_full if os.path.exists(lh_full) else lh_hdr

        # Measure header height to know where content starts
        try:
            with PILImage.open(lh_hdr) as _img:
                _iw, _ih = _img.size
            header_h = A4[0] * (_ih / _iw)
        except Exception:
            header_h = 1.8 * inch

        # Footer height (~10% of page)
        footer_h = A4[1] * 0.10

        # Register Arial Unicode for Arabic
        font_reg  = 'ArHR'
        font_bold = 'ArHRBold'
        try:
            uni_path  = r'C:\Windows\Fonts\ARIALUNI.TTF'
            bold_path = r'C:\Windows\Fonts\arialbd.ttf'
            pdfmetrics.registerFont(TTFont(font_reg,  uni_path))
            pdfmetrics.registerFont(TTFont(font_bold, bold_path))
        except Exception:
            font_reg = font_bold = 'Helvetica'

        def ar(text):
            """Reshape + bidi-reorder Arabic text for ReportLab LTR rendering."""
            reshaped = arabic_reshaper.reshape(text)
            return get_display(reshaped)

        def draw_rtl_para(c, text, x_right, y, max_w, font, size, line_h=None, color=(0,0,0)):
            """Draw a right-aligned Arabic paragraph with word-wrapping. Returns y after last line."""
            if line_h is None:
                line_h = size * 1.6
            words = text.split(' ')
            lines = []
            cur = []
            for w in words:
                test = ' '.join(cur + [w])
                vis  = ar(test)
                if c.stringWidth(vis, font, size) <= max_w:
                    cur.append(w)
                else:
                    if cur:
                        lines.append(' '.join(cur))
                    cur = [w]
            if cur:
                lines.append(' '.join(cur))
            c.setFont(font, size)
            c.setFillColorRGB(*color)
            for line in lines:
                c.drawRightString(x_right, y, ar(line))
                y -= line_h
            return y

        page_w, page_h = A4
        margin_l = 0.75 * inch
        margin_r = 0.75 * inch
        x_right  = page_w - margin_r
        x_left   = margin_l
        max_w    = page_w - margin_l - margin_r

        # Data
        date_str = letter_data.get('date', '15/05/2026')
        name_ar  = letter_data.get('name_ar', '')
        passport = letter_data.get('passport', '')
        position = letter_data.get('position', '')
        salary   = letter_data.get('salary', '')
        purpose  = letter_data.get('purpose', 'الجهات الحكومية ولمن يهمه الأمر')

        c = rl_canvas.Canvas(output_path, pagesize=A4)

        # Draw letterhead background
        c.drawImage(_lf, 0, 0, width=page_w, height=page_h,
                    preserveAspectRatio=False, mask='auto')

        # Y starts just below header
        y = page_h - header_h - 0.3 * inch

        # ── Date ─────────────────────────────────────────────────────
        c.setFont(font_reg, 11)
        c.setFillColorRGB(0, 0, 0)
        c.drawRightString(x_right, y, ar(f'التاريخ: {date_str}'))
        y -= 0.45 * inch

        # ── Title ─────────────────────────────────────────────────────
        c.setFont(font_bold, 15)
        title_vis = ar('إلى من يهمه الأمر')
        c.drawCentredString(page_w / 2, y, title_vis)
        y -= 0.22 * inch

        # Underline
        c.setStrokeColorRGB(0.22, 0.22, 0.22)
        c.setLineWidth(0.8)
        c.line(x_left, y, x_right, y)
        y -= 0.35 * inch

        # ── Body paragraphs ───────────────────────────────────────────
        body_size = 12
        line_h    = body_size * 1.75

        para1 = (
            f'تشهد شركة نيويل للأعمال الكهروميكانيكية بأن السيد {name_ar}، '
            f'حامل جواز السفر رقم {passport}، '
            f'يعمل لدى الشركة بوظيفة {position} بدوام كامل.'
        )
        y = draw_rtl_para(c, para1, x_right, y, max_w, font_reg, body_size, line_h)
        y -= 0.1 * inch

        para2 = f'ويتقاضى راتبًا شهريًا قدره {salary} درهم إماراتي.'
        y = draw_rtl_para(c, para2, x_right, y, max_w, font_reg, body_size, line_h)
        y -= 0.1 * inch

        para3 = f'وقد تم إصدار هذه الشهادة بناءً على طلبه لتقديمها إلى {purpose}.'
        y = draw_rtl_para(c, para3, x_right, y, max_w, font_reg, body_size, line_h)
        y -= 0.15 * inch

        closing = 'وتفضلوا بقبول فائق الاحترام والتقدير.'
        y = draw_rtl_para(c, closing, x_right, y, max_w, font_reg, body_size, line_h)
        y -= 0.45 * inch

        # ── Signature block ───────────────────────────────────────────
        c.setFont(font_reg, 11)
        c.drawRightString(x_right, y, ar('قسم الموارد البشرية'))
        y -= 0.28 * inch

        c.setFont(font_bold, 11)
        c.drawRightString(x_right, y, 'Newell Electromechanical')
        y -= 0.55 * inch

        c.setFont(font_reg, 11)
        c.drawRightString(x_right, y, ar('التوقيع: ___________________'))
        y -= 0.35 * inch

        c.drawRightString(x_right, y, ar('ختم الشركة: ___________________'))

        c.save()
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


    def generate_employee_guide(self, guide_data, output_path):
        """Generate new employee onboarding PDF guide focused on financial processes."""
        from reportlab.platypus import PageBreak, KeepTogether

        A4_W, A4_H = A4
        margin = 0.75 * inch
        content_w = A4_W - 2 * margin

        doc = SimpleDocTemplate(
            output_path, pagesize=A4,
            topMargin=margin, bottomMargin=margin,
            leftMargin=margin, rightMargin=margin,
        )

        def _draw_page_number(canvas, doc):
            if doc.page == 1:
                return
            canvas.saveState()
            canvas.setFont('Helvetica', 8)
            canvas.setFillColorRGB(0.5, 0.5, 0.5)
            canvas.drawCentredString(A4_W / 2, 0.4 * inch, f"Page {doc.page}")
            canvas.restoreState()

        def _blue_table(data, col_widths, row_heights=None):
            t = Table(data, colWidths=col_widths, rowHeights=row_heights)
            n = len(data)
            style_cmds = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2563eb')),
                ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 9),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
                ('TOPPADDING', (0, 0), (-1, 0), 7),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('TEXTCOLOR', (0, 1), (-1, -1), colors.HexColor('#374151')),
                ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#d1d5db')),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 1), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ]
            for i in range(1, n):
                if i % 2 == 0:
                    style_cmds.append(
                        ('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f0f4ff'))
                    )
            t.setStyle(TableStyle(style_cmds))
            return t

        def _doc_blocks_to_elements(blocks, fallback_text=''):
            elems = []
            if not blocks:
                if fallback_text:
                    elems.append(Paragraph(fallback_text, self.styles['GuideBody']))
                return elems
            for b in blocks:
                btype = b.get('type', 'paragraph')
                text = b.get('text', '')
                if btype == 'heading1':
                    elems.append(Paragraph(text, self.styles['ChapterTitle']))
                elif btype == 'heading2':
                    elems.append(Paragraph(text, self.styles['GuideSection']))
                elif btype == 'heading3':
                    elems.append(Paragraph(f'<b>{text}</b>', self.styles['GuideBody']))
                elif btype == 'bullet':
                    elems.append(Paragraph(f'•  {text}', self.styles['GuideBullet']))
                elif btype == 'table':
                    rows = b.get('rows', [])
                    if rows:
                        n_cols = max(len(r) for r in rows)
                        cw = content_w / n_cols
                        tbl_data = [
                            [Paragraph(cell, self.styles['GuideTableCell']) for cell in row]
                            for row in rows
                        ]
                        elems.append(_blue_table(tbl_data, [cw] * n_cols))
                        elems.append(Spacer(1, 8))
                else:
                    if text:
                        elems.append(Paragraph(text, self.styles['GuideBody']))
            return elems

        elements = []
        company = guide_data.get('company', {})
        generated_date = guide_data.get('generated_date', datetime.now().strftime('%d %B %Y'))

        # ── COVER PAGE ───────────────────────────────────────────────────────
        elements.append(Spacer(1, 1.2 * inch))

        logo_path = self.company_logo_path
        if logo_path and os.path.exists(logo_path):
            logo = Image(logo_path, width=2.4 * inch, height=1.0 * inch)
            logo.hAlign = 'CENTER'
            elements.append(logo)
            elements.append(Spacer(1, 0.3 * inch))

        elements.append(Paragraph(company.get('name', self.company_name), self.styles['GuideTitle']))
        elements.append(Spacer(1, 0.15 * inch))
        elements.append(Paragraph(
            'New Employee Onboarding Guide', self.styles['ChapterTitle']
        ))
        elements.append(Paragraph('Financial Processes &amp; Systems', self.styles['GuideSubtitle']))
        elements.append(Spacer(1, 0.4 * inch))

        elements.append(HRFlowable(width=content_w, thickness=1.5,
                                   color=colors.HexColor('#2563eb')))
        elements.append(Spacer(1, 0.2 * inch))

        cover_details = [
            ['Confidential — For Internal Use Only'],
            [f'Generated: {generated_date}'],
            [f'Address: {company.get("address", self.company_address)}'],
            [f'TRN: {company.get("trn", self.company_trn)}'],
        ]
        for row in cover_details:
            elements.append(Paragraph(row[0], self.styles['GuideSubtitle']))

        elements.append(PageBreak())

        # ── TABLE OF CONTENTS ────────────────────────────────────────────────
        elements.append(Paragraph('Table of Contents', self.styles['ChapterTitle']))
        elements.append(Spacer(1, 0.1 * inch))

        toc_sections = [
            ('1', 'Company Overview'),
            ('2', 'Financial Workflow — End to End'),
            ('3', 'Quotations'),
            ('4', 'Tax Invoices'),
            ('5', 'Delivery Orders'),
            ('6', 'Cheques, PDC/CDC &amp; Bank Payment Vouchers'),
            ('7', 'Vendor Directory'),
            ('8', 'Liabilities Overview'),
            ('9', 'Key Rules &amp; Compliance'),
            ('10', 'Quick Reference'),
        ]
        toc_data = [['Section', 'Title']] + [[s, t] for s, t in toc_sections]
        toc_table = _blue_table(toc_data, [0.6 * inch, content_w - 0.6 * inch])
        elements.append(toc_table)
        elements.append(PageBreak())

        # ── SECTION 1: COMPANY OVERVIEW ──────────────────────────────────────
        elements.append(Paragraph('1. Company Overview', self.styles['ChapterTitle']))
        overview_blocks = guide_data.get('google_doc_overview', [])
        fallback = (
            f"{company.get('name', self.company_name)} is a leading electromechanical "
            f"works company based in Dubai, UAE. "
            f"We handle procurement, electrical, and mechanical projects across the UAE.\n\n"
            f"Address: {company.get('address', self.company_address)}\n"
            f"TRN: {company.get('trn', self.company_trn)}\n"
            f"Tel: 04-8843367  |  P.O. Box 88593, Dubai"
        )
        elements.extend(_doc_blocks_to_elements(overview_blocks, fallback))
        elements.append(PageBreak())

        # ── SECTION 2: FINANCIAL WORKFLOW ────────────────────────────────────
        elements.append(Paragraph('2. Financial Workflow — End to End', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'Every purchase at Newell follows a strict procure-to-pay cycle. '
            'Each step creates a document with its own ID, which links to the next step. '
            'This ensures full traceability from the initial quote to the final payment.',
            self.styles['GuideBody']
        ))
        elements.append(Spacer(1, 8))

        flow_data = [
            ['Step', 'Document', 'ID Format', 'Purpose'],
            ['1', 'Quotation', 'QT-YYYY-MM-###', 'Vendor submits a price quote'],
            ['2', 'Local Purchase Order', 'LPO-YYYY-MM-###', 'We formally approve the purchase'],
            ['3', 'Delivery Order', 'DO-YYYY-MM-###', 'Goods are received and inspected'],
            ['4', 'Tax Invoice', 'INV-YYYY-MM-###', 'Vendor invoices us (includes 5% VAT)'],
            ['5', 'Cheque / BPV', 'CHQ-YYYY-MM-###', 'Payment is issued and recorded'],
        ]
        flow_col_w = [0.35 * inch, 1.7 * inch, 1.6 * inch, content_w - 0.35 * inch - 1.7 * inch - 1.6 * inch]
        elements.append(_blue_table(flow_data, flow_col_w))
        elements.append(Spacer(1, 10))
        elements.append(Paragraph(
            '<b>Important:</b> Never skip a step. Each document must reference the previous '
            'one (e.g., an Invoice must reference its LPO ID). The system enforces this linkage.',
            self.styles['GuideCallout']
        ))
        elements.append(PageBreak())

        # ── SECTION 3: QUOTATIONS ────────────────────────────────────────────
        elements.append(Paragraph('3. Quotations', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'A Quotation is a vendor\'s price offer. Before purchasing anything, we collect '
            'at least one quotation and record it in the system.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('Quotation ID Format', self.styles['GuideSection']))
        elements.append(Paragraph(
            'Format: <b>QT-YYYY-MM-###</b> (e.g., QT-2026-05-001). '
            'IDs are auto-generated — you never type them manually.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('Status Lifecycle', self.styles['GuideSection']))
        q_status_data = [
            ['Status', 'Meaning'],
            ['Draft', 'Being entered — not yet submitted for approval'],
            ['Pending Approval', 'Submitted and awaiting manager sign-off'],
            ['Approved', 'Cleared — can be converted to an LPO'],
            ['Rejected', 'Not approved — vendor or price not accepted'],
            ['Expired', 'Validity date passed without action'],
            ['Converted to LPO', 'An LPO has been raised against this quotation'],
        ]
        elements.append(_blue_table(q_status_data, [1.4 * inch, content_w - 1.4 * inch]))

        qt_summary = guide_data.get('quotations_summary', {})
        if qt_summary.get('total'):
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                f"Live count: <b>{qt_summary['total']}</b> quotations in the system "
                f"({qt_summary.get('approved', 0)} approved, {qt_summary.get('pending', 0)} pending).",
                self.styles['GuideBody']
            ))
        elements.append(PageBreak())

        # ── SECTION 4: TAX INVOICES ──────────────────────────────────────────
        elements.append(Paragraph('4. Tax Invoices', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'After goods are delivered, the vendor sends a Tax Invoice. '
            'We record it in the system linked to the relevant LPO.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('Invoice ID Format: <b>INV-YYYY-MM-###</b>',
                                  self.styles['GuideBody']))
        elements.append(Paragraph('UAE VAT Rule', self.styles['GuideSection']))
        elements.append(Paragraph(
            'All invoices are subject to <b>5% VAT</b> as required by UAE Federal Tax Authority. '
            'The vendor\'s TRN (Tax Registration Number) must appear on every invoice. '
            'Our TRN is <b>' + self.company_trn + '</b>.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('Status Lifecycle', self.styles['GuideSection']))
        inv_status_data = [
            ['Status', 'Meaning'],
            ['Pending', 'Invoice received, awaiting review'],
            ['Approved', 'Invoice verified and approved for payment'],
            ['Paid', 'Payment has been issued'],
            ['Overdue', 'Payment due date passed without payment'],
            ['Cancelled', 'Invoice voided'],
        ]
        elements.append(_blue_table(inv_status_data, [1.4 * inch, content_w - 1.4 * inch]))

        inv_summary = guide_data.get('invoices_summary', {})
        if inv_summary.get('total'):
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                f"Live count: <b>{inv_summary['total']}</b> invoices in the system.",
                self.styles['GuideBody']
            ))
        elements.append(PageBreak())

        # ── SECTION 5: DELIVERY ORDERS ───────────────────────────────────────
        elements.append(Paragraph('5. Delivery Orders', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'When goods arrive on site, a Delivery Order (DO) is created to confirm receipt. '
            'This is the official handover record.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('DO ID Format: <b>DO-YYYY-MM-###</b>', self.styles['GuideBody']))

        elements.append(Paragraph('Key Fields', self.styles['GuideSection']))
        do_fields_data = [
            ['Field', 'Description'],
            ['Received By', 'Name of person who accepted the delivery on site'],
            ['Condition', 'Good / Damaged / Rejected — recorded per line item'],
            ['LPO Reference', 'Links this DO to the original purchase order'],
            ['Partial Delivery', 'A DO can cover part of an LPO — quantities tracked separately'],
        ]
        elements.append(_blue_table(do_fields_data, [1.6 * inch, content_w - 1.6 * inch]))

        do_summary = guide_data.get('delivery_orders_summary', {})
        if do_summary.get('total'):
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                f"Live count: <b>{do_summary['total']}</b> delivery orders recorded.",
                self.styles['GuideBody']
            ))
        elements.append(PageBreak())

        # ── SECTION 6: CHEQUES / PDC / CDC / BPV ────────────────────────────
        elements.append(Paragraph('6. Cheques, PDC/CDC &amp; Bank Payment Vouchers',
                                  self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'Once an invoice is approved, payment is issued by cheque. '
            'Every cheque is formally recorded as a Bank Payment Voucher (BPV).',
            self.styles['GuideBody']
        ))

        elements.append(Paragraph('PDC vs CDC', self.styles['GuideSection']))
        pdc_data = [
            ['Type', 'Full Name', 'Meaning'],
            ['PDC', 'Post-Dated Cheque', 'Cheque dated in the future — will clear on that date'],
            ['CDC', 'Current-Dated Cheque', 'Cheque dated today — clears immediately'],
        ]
        elements.append(_blue_table(pdc_data, [0.7 * inch, 1.9 * inch, content_w - 0.7 * inch - 1.9 * inch]))

        elements.append(Paragraph('Cheque Status Lifecycle', self.styles['GuideSection']))
        chq_status_data = [
            ['Status', 'Meaning'],
            ['Created', 'Cheque details entered in the system'],
            ['Approved', 'Finance manager has signed off'],
            ['Released', 'Physical cheque handed to vendor'],
            ['Cleared', 'Bank has processed the payment'],
        ]
        elements.append(_blue_table(chq_status_data, [1.4 * inch, content_w - 1.4 * inch]))

        elements.append(Paragraph('Bank Payment Voucher (BPV)', self.styles['GuideSection']))
        elements.append(Paragraph(
            'Every cheque gets a BPV — a formal record that is printed and filed. '
            'The BPV shows the BPV number, date, cheque details, amounts (Debit/Credit), '
            'and requires two signatures: <b>Received By</b> and <b>Approved By</b>.',
            self.styles['GuideBody']
        ))

        bpv_summary = guide_data.get('bpv_summary', {})
        if bpv_summary.get('total_count'):
            elements.append(Paragraph('Live BPV Summary', self.styles['GuideSection']))
            bpv_stats_data = [
                ['Metric', 'Value'],
                ['Total BPVs recorded', str(bpv_summary['total_count'])],
                ['PDC vouchers', str(bpv_summary.get('pdc_count', 'N/A'))],
                ['CDC vouchers', str(bpv_summary.get('cdc_count', 'N/A'))],
                ['Total amount (AED)', f"{bpv_summary.get('total_amount', 0):,.2f}"],
            ]
            elements.append(_blue_table(bpv_stats_data, [3 * inch, content_w - 3 * inch]))

        chq_summary = guide_data.get('cheques_summary', {})
        if chq_summary:
            elements.append(Spacer(1, 8))
            elements.append(Paragraph(
                f"Cheques in system: <b>{chq_summary.get('total', 0)}</b> total — "
                f"{chq_summary.get('released', 0)} released, "
                f"{chq_summary.get('cleared', 0)} cleared, "
                f"{chq_summary.get('pending', 0)} pending.",
                self.styles['GuideBody']
            ))
        elements.append(PageBreak())

        # ── SECTION 7: VENDOR DIRECTORY ──────────────────────────────────────
        elements.append(Paragraph('7. Vendor Directory', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'All approved vendors are registered in the Vendors sheet. '
            'Only use registered vendors for purchases. '
            'Each vendor has a Vendor ID (VND-YYYY-MM-###) used to link quotes and LPOs.',
            self.styles['GuideBody']
        ))

        vendors = guide_data.get('vendor_directory', [])
        if vendors:
            vendor_data = [['Vendor Name', 'Contact', 'Email', 'Phone', 'Pay Terms', 'TRN']]
            for v in vendors[:50]:
                vendor_data.append([
                    Paragraph(v.get('name', ''), self.styles['GuideTableCell']),
                    Paragraph(v.get('contact', ''), self.styles['GuideTableCell']),
                    Paragraph(v.get('email', ''), self.styles['GuideTableCell']),
                    v.get('phone', ''),
                    v.get('payment_terms', ''),
                    v.get('trn', ''),
                ])
            vnd_col_w = [1.9 * inch, 1.1 * inch, 1.5 * inch, 0.95 * inch, 0.65 * inch, 0.9 * inch]
            elements.append(_blue_table(vendor_data, vnd_col_w))
            if len(vendors) > 50:
                elements.append(Paragraph(
                    f'Showing first 50 of {len(vendors)} vendors. '
                    'See the live Vendors sheet for the full directory.',
                    self.styles['GuideBody']
                ))
        else:
            elements.append(Paragraph(
                'No vendor data available. Check the Vendors sheet in Google Sheets.',
                self.styles['GuideBody']
            ))
        elements.append(PageBreak())

        # ── SECTION 8: LIABILITIES OVERVIEW ─────────────────────────────────
        elements.append(Paragraph('8. Liabilities Overview', self.styles['ChapterTitle']))
        elements.append(Paragraph(
            'The Liabilities Tracker records amounts we owe to external companies '
            '(vendors, suppliers, contractors). It is the single source of truth for '
            'outstanding payables.',
            self.styles['GuideBody']
        ))
        elements.append(Paragraph('Liabilities Sheet Columns', self.styles['GuideSection']))
        lib_cols_data = [
            ['Column', 'Description'],
            ['SN', 'Auto-incremented serial number'],
            ['Company Name', 'Vendor or supplier we owe money to'],
            ['Invoice / Reference No.', 'The invoice or document that created the liability'],
            ['Amount (AED)', 'Amount owed in UAE Dirhams'],
            ['Date Added', 'When the liability was recorded'],
            ['Notes', 'Any additional context (e.g. "partial payment made")'],
        ]
        elements.append(_blue_table(lib_cols_data, [1.7 * inch, content_w - 1.7 * inch]))

        lib_summary = guide_data.get('liabilities_summary', {})
        if lib_summary.get('grand_total') is not None:
            elements.append(Paragraph('Outstanding Liabilities — Top Companies', self.styles['GuideSection']))
            top = lib_summary.get('top_companies', [])
            if top:
                lib_tbl_data = [['Company', 'Amount (AED)']]
                for row in top:
                    lib_tbl_data.append([
                        row.get('company', ''),
                        f"{row.get('amount', 0):,.2f}",
                    ])
                lib_tbl_data.append([
                    Paragraph('<b>GRAND TOTAL</b>', self.styles['GuideTableCell']),
                    Paragraph(f"<b>{lib_summary['grand_total']:,.2f}</b>",
                              self.styles['GuideTableCell']),
                ])
                t = _blue_table(lib_tbl_data, [content_w - 1.5 * inch, 1.5 * inch])
                elements.append(t)
        elements.append(PageBreak())

        # ── SECTION 9: KEY RULES & COMPLIANCE ───────────────────────────────
        elements.append(Paragraph('9. Key Rules &amp; Compliance', self.styles['ChapterTitle']))

        policy_blocks = guide_data.get('google_doc_policies', [])
        elements.extend(_doc_blocks_to_elements(policy_blocks))

        rules = [
            ('<b>5% UAE VAT:</b> Every vendor invoice must include 5% VAT and show '
             'the vendor\'s TRN. Invoices without a TRN cannot be processed.'),
            ('<b>Approval chain:</b> All purchases require manager approval before an LPO '
             'is issued. No goods should be ordered without a signed-off LPO.'),
            ('<b>Audit log:</b> Every action in the system (create, update, delete) is '
             'automatically logged with a timestamp and the user who made the change. '
             'This log cannot be edited.'),
            ('<b>Document retention:</b> PDFs are saved to the .tmp/ folder for printing. '
             'The source of truth is always the Google Sheet — not the PDF.'),
            ('<b>Payment terms:</b> Honour the payment terms agreed with each vendor '
             '(visible in the Vendors sheet). Late payments can damage vendor relationships.'),
            ('<b>No cheque without approval:</b> A cheque can only be printed and released '
             'after it reaches "Approved" status in the system.'),
        ]
        for rule in rules:
            elements.append(Paragraph(f'•  {rule}', self.styles['GuideBullet']))
            elements.append(Spacer(1, 4))
        elements.append(PageBreak())

        # ── SECTION 10: QUICK REFERENCE ─────────────────────────────────────
        elements.append(Paragraph('10. Quick Reference', self.styles['ChapterTitle']))

        elements.append(Paragraph('Document ID Formats', self.styles['GuideSection']))
        id_data = [
            ['Document Type', 'ID Format', 'Example'],
            ['Vendor', 'VND-YYYY-MM-###', 'VND-2026-05-001'],
            ['Quotation', 'QT-YYYY-MM-###', 'QT-2026-05-001'],
            ['Local Purchase Order', 'LPO-YYYY-MM-###', 'LPO-2026-05-001'],
            ['Delivery Order', 'DO-YYYY-MM-###', 'DO-2026-05-001'],
            ['Tax Invoice', 'INV-YYYY-MM-###', 'INV-2026-05-001'],
            ['Cheque', 'CHQ-YYYY-MM-###', 'CHQ-2026-05-001'],
        ]
        id_col_w = [1.8 * inch, 1.7 * inch, content_w - 1.8 * inch - 1.7 * inch]
        elements.append(_blue_table(id_data, id_col_w))

        elements.append(Paragraph('Company Contacts', self.styles['GuideSection']))
        contacts_data = [
            ['Detail', 'Value'],
            ['Company', company.get('name', self.company_name)],
            ['Address', company.get('address', self.company_address)],
            ['P.O. Box', '88593, Dubai, UAE'],
            ['Telephone', '04-8843367'],
            ['TRN', company.get('trn', self.company_trn)],
            ['Email', 'Newellmep@gmail.com'],
        ]
        elements.append(_blue_table(contacts_data, [1.5 * inch, content_w - 1.5 * inch]))

        elements.append(Paragraph('Google Sheets — Live Data', self.styles['GuideSection']))
        sheet_links = [
            ['Sheet', 'Link'],
            ['Main Accounting', 'docs.google.com/spreadsheets/d/1owuQWD7VjKW-6oKPkqKnM0wWWv4LKi0fAcvR2jvzxHw'],
            ['BPV (Bank Payment Vouchers)', 'docs.google.com/spreadsheets/d/1DZfS3J6Men-XuX11nXAYnMhgQKCn4SJTFDDFxlEQAwk'],
            ['Liabilities Tracker', 'docs.google.com/spreadsheets/d/14j-hZ0Io0jPNh9wxGV8rmTL7voK6xJ8s2QJVmACjf2Y'],
        ]
        elements.append(_blue_table(sheet_links, [1.8 * inch, content_w - 1.8 * inch]))

        elements.append(Spacer(1, 16))
        elements.append(HRFlowable(width=content_w, thickness=1,
                                   color=colors.HexColor('#2563eb')))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            f'When in doubt, check the Audit Log — every action is recorded. '
            f'Guide generated on {generated_date}.',
            self.styles['GuideSubtitle']
        ))

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        doc.build(elements,
                  onFirstPage=_draw_page_number,
                  onLaterPages=_draw_page_number)
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
