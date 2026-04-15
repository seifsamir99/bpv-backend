"""
Flask API for Purchase & Accounting System
Provides REST endpoints for frontend to interact with Google Sheets backend
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from sheets_operations import SheetsDB
from new_bpv_service import NewBPVService
from ai_extraction import AIExtractor
from pdf_generator import PDFGenerator
from approval_workflow import ApprovalWorkflow
from reporting import ReportGenerator
from email_notifications import EmailNotifier
import os
import json
from datetime import datetime
import tempfile
import base64
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

# Initialize services
db = SheetsDB()
bpv_service = NewBPVService()
ai_extractor = AIExtractor()
pdf_gen = PDFGenerator()
approval = ApprovalWorkflow()
reports = ReportGenerator()
notifier = EmailNotifier()


# Error handler
@app.errorhandler(Exception)
def handle_error(error):
    return jsonify({"success": False, "error": str(error)}), 400


# Health check
@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "timestamp": datetime.now().isoformat()})


# ============================================================================
# BPV (Bank Payment Vouchers)
# ============================================================================

@app.route('/api/bpv', methods=['GET'])
def get_bpv_vouchers():
    """Get all BPV vouchers"""
    try:
        vouchers = bpv_service.get_all_vouchers()
        return jsonify({"success": True, "data": vouchers})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv/next-number', methods=['GET'])
def get_next_bpv_number():
    """Get the next available BPV number"""
    try:
        next_num = bpv_service.get_next_bpv_number()
        return jsonify({"success": True, "data": {"nextNumber": next_num}})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv/<bpv_no>', methods=['GET'])
def get_bpv_voucher(bpv_no):
    """Get single BPV voucher by number"""
    try:
        voucher = bpv_service.get_voucher(bpv_no)
        if not voucher:
            return jsonify({"success": False, "error": "Voucher not found"}), 404
        return jsonify({"success": True, "data": voucher})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv', methods=['POST'])
def create_bpv_voucher():
    """Create new BPV voucher"""
    try:
        data = request.json

        # Calculate total if line items provided
        if 'lineItems' in data and 'totalAmount' not in data:
            data['totalAmount'] = bpv_service.calculate_total(data['lineItems'])

        voucher = bpv_service.create_voucher(data)
        return jsonify({"success": True, "data": voucher})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv/<bpv_no>', methods=['PUT'])
def update_bpv_voucher(bpv_no):
    """Update existing BPV voucher"""
    try:
        data = request.json

        # Calculate total if line items provided
        if 'lineItems' in data and 'totalAmount' not in data:
            data['totalAmount'] = bpv_service.calculate_total(data['lineItems'])

        voucher = bpv_service.update_voucher(bpv_no, data)
        return jsonify({"success": True, "data": voucher})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv/<bpv_no>', methods=['DELETE'])
def delete_bpv_voucher(bpv_no):
    """Delete BPV voucher (clears data, keeps template)"""
    try:
        result = bpv_service.delete_voucher(bpv_no)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/bpv/<bpv_no>/pdf', methods=['GET'])
def generate_bpv_pdf(bpv_no):
    """Generate PDF for BPV voucher"""
    try:
        voucher = bpv_service.get_voucher(bpv_no)
        if not voucher:
            return jsonify({"success": False, "error": "Voucher not found"}), 404

        # Generate PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
            pdf_path = tmp.name

        # Use existing PDF generator with BPV data
        pdf_gen.generate_bpv(voucher, pdf_path)

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"BPV_{voucher.get('bpvNo', bpv_no)}.pdf"
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# PDC (Post-Dated Cheques)
# ============================================================================

@app.route('/api/pdc', methods=['GET'])
def get_manual_pdcs():
    """Get manually added PDC entries"""
    try:
        records = bpv_service.get_manual_pdcs()
        return jsonify({"success": True, "data": records})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/pdc', methods=['POST'])
def add_manual_pdc():
    """Add a new manual PDC entry"""
    try:
        data = request.json
        result = bpv_service.add_manual_pdc(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/pdc/<int:row_index>', methods=['DELETE'])
def delete_manual_pdc(row_index):
    """Delete a manual PDC entry by its 1-based data row index"""
    try:
        result = bpv_service.delete_manual_pdc(row_index)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/pdc/statuses', methods=['GET'])
def get_pdc_statuses():
    """Get all PDC statuses as {chequeNo: status}"""
    try:
        statuses = bpv_service.get_pdc_statuses()
        return jsonify({"success": True, "data": statuses})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/pdc/status-by-cheque/<cheque_no>', methods=['PATCH'])
def update_pdc_status(cheque_no):
    """Update status for a single PDC cheque"""
    try:
        data = request.json
        status = data.get('status', '')
        result = bpv_service.update_pdc_status(cheque_no, status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/pdc/bulk-status', methods=['PATCH'])
def bulk_update_pdc_status():
    """Bulk update PDC statuses. Body: {updates: [{chequeNo, status}]}"""
    try:
        data = request.json
        updates = data.get('updates', [])
        result = bpv_service.bulk_update_pdc_status(updates)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# CDC (Current-Dated Cheques)
# ============================================================================

@app.route('/api/cdc/statuses', methods=['GET'])
def get_cdc_statuses():
    """Get all CDC statuses as {chequeNo: status}"""
    try:
        statuses = bpv_service.get_cdc_statuses()
        return jsonify({"success": True, "data": statuses})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/cdc/status-by-cheque/<cheque_no>', methods=['PATCH'])
def update_cdc_status(cheque_no):
    """Update status for a single CDC cheque"""
    try:
        data = request.json
        status = data.get('status', '')
        result = bpv_service.update_cdc_status(cheque_no, status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/cdc/bulk-status', methods=['PATCH'])
def bulk_update_cdc_status():
    """Bulk update CDC statuses. Body: {updates: [{chequeNo, status}]}"""
    try:
        data = request.json
        updates = data.get('updates', [])
        result = bpv_service.bulk_update_cdc_status(updates)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# SUPPLIERS
# ============================================================================

@app.route('/api/suppliers', methods=['GET'])
def get_suppliers():
    """Get all suppliers"""
    suppliers = db.get_all("Suppliers")
    return jsonify({"success": True, "data": suppliers})


@app.route('/api/suppliers/<supplier_id>', methods=['GET'])
def get_supplier(supplier_id):
    """Get supplier by ID"""
    supplier = db.get_by_id("Suppliers", "Supplier ID", supplier_id)
    if not supplier:
        return jsonify({"success": False, "error": "Supplier not found"}), 404
    return jsonify({"success": True, "data": supplier})


@app.route('/api/suppliers', methods=['POST'])
def create_supplier():
    """Create new supplier"""
    data = request.json
    user = data.get('user', 'System')

    supplier_id = db.create_supplier(
        supplier_name=data['supplier_name'],
        contact_person=data.get('contact_person', ''),
        email=data.get('email', ''),
        phone=data.get('phone', ''),
        address=data.get('address', ''),
        payment_terms=data.get('payment_terms', ''),
        trn=data.get('trn', ''),
        user=user
    )

    return jsonify({"success": True, "supplier_id": supplier_id})


# ============================================================================
# LPOs
# ============================================================================

@app.route('/api/lpos', methods=['GET'])
def get_lpos():
    """Get all LPOs"""
    lpos = db.get_all("LPOs")
    return jsonify({"success": True, "data": lpos})


@app.route('/api/lpos/<lpo_id>', methods=['GET'])
def get_lpo(lpo_id):
    """Get LPO with line items"""
    lpo = db.get_by_id("LPOs", "LPO ID", lpo_id)
    if not lpo:
        return jsonify({"success": False, "error": "LPO not found"}), 404

    line_items = db.filter("LPO Line Items", {"LPO ID": lpo_id})

    return jsonify({
        "success": True,
        "data": {
            "lpo": lpo,
            "line_items": line_items
        }
    })


@app.route('/api/lpos', methods=['POST'])
def create_lpo():
    """Create LPO directly with supplier and line items"""
    data = request.json
    user = data.get('user', 'System')

    lpo_id = db.create_lpo(
        supplier_id=data['supplier_id'],
        lpo_date=data['lpo_date'],
        line_items=data['line_items'],
        expected_delivery_date=data['expected_delivery_date'],
        user=user
    )

    return jsonify({"success": True, "lpo_id": lpo_id})


@app.route('/api/lpos/<lpo_id>/approve', methods=['POST'])
def approve_lpo_endpoint(lpo_id):
    """Approve LPO"""
    data = request.json
    approver = data.get('approver', 'System')
    comments = data.get('comments', '')

    result = approval.approve_lpo(lpo_id, approver, comments)
    return jsonify(result)


@app.route('/api/lpos/<lpo_id>/reject', methods=['POST'])
def reject_lpo_endpoint(lpo_id):
    """Reject LPO"""
    data = request.json
    approver = data.get('approver', 'System')
    reason = data.get('reason', '')

    result = approval.reject_lpo(lpo_id, approver, reason)
    return jsonify(result)


# ============================================================================
# INVOICES
# ============================================================================

@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    """Get all tax invoices"""
    invoices = db.get_all("Tax Invoices")
    return jsonify({"success": True, "data": invoices})


@app.route('/api/invoices/<invoice_id>', methods=['GET'])
def get_invoice(invoice_id):
    """Get invoice by ID"""
    invoice = db.get_by_id("Tax Invoices", "Invoice ID", invoice_id)
    if not invoice:
        return jsonify({"success": False, "error": "Invoice not found"}), 404
    return jsonify({"success": True, "data": invoice})


# ============================================================================
# CHEQUES
# ============================================================================

@app.route('/api/cheques', methods=['GET'])
def get_cheques():
    """Get all cheques"""
    cheques = db.get_all("Cheques")
    return jsonify({"success": True, "data": cheques})


@app.route('/api/cheques/<cheque_id>', methods=['GET'])
def get_cheque(cheque_id):
    """Get cheque by ID"""
    cheque = db.get_by_id("Cheques", "Cheque ID", cheque_id)
    if not cheque:
        return jsonify({"success": False, "error": "Cheque not found"}), 404
    return jsonify({"success": True, "data": cheque})


@app.route('/api/cheques', methods=['POST'])
def create_cheque():
    """Create new cheque"""
    data = request.json
    user = data.get('user', 'System')

    cheque_id = db.create_cheque(
        invoice_id=data['invoice_id'],
        cheque_number=data['cheque_number'],
        bank_name=data['bank_name'],
        cheque_date=data['cheque_date'],
        user=user
    )

    return jsonify({"success": True, "cheque_id": cheque_id})


@app.route('/api/cheques/<cheque_id>/approve', methods=['POST'])
def approve_cheque_endpoint(cheque_id):
    """Approve cheque"""
    data = request.json
    approver = data.get('approver', 'System')

    result = approval.approve_cheque(cheque_id, approver)
    return jsonify(result)


@app.route('/api/cheques/<cheque_id>/release', methods=['POST'])
def release_cheque_endpoint(cheque_id):
    """Release cheque"""
    data = request.json
    releaser = data.get('releaser', 'System')

    result = approval.release_cheque(cheque_id, releaser)
    return jsonify(result)


@app.route('/api/cheques/<cheque_id>/clear', methods=['POST'])
def clear_cheque_endpoint(cheque_id):
    """Mark cheque as cleared"""
    data = request.json
    user = data.get('user', 'System')

    result = approval.mark_cheque_cleared(cheque_id, user)
    return jsonify(result)


# ============================================================================
# AI EXTRACTION
# ============================================================================

@app.route('/api/extract/image', methods=['POST'])
def extract_from_image():
    """Extract data from uploaded image"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files['file']
    document_type = request.form.get('document_type', 'invoice')

    # Save temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1]) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        result = ai_extractor.extract_from_image(tmp_path, document_type)
        return jsonify(result)
    finally:
        os.unlink(tmp_path)


@app.route('/api/extract/voice', methods=['POST'])
def extract_from_voice():
    """Extract data from voice transcription"""
    data = request.json
    audio_text = data.get('text', '')
    context = data.get('context', 'general')

    result = ai_extractor.extract_from_voice(audio_text, context)
    return jsonify(result)


# ============================================================================
# PDF GENERATION
# ============================================================================

@app.route('/api/pdf/lpo/<lpo_id>', methods=['GET'])
def generate_lpo_pdf(lpo_id):
    """Generate LPO PDF"""
    # Get LPO data
    lpo = db.get_by_id("LPOs", "LPO ID", lpo_id)
    if not lpo:
        return jsonify({"success": False, "error": "LPO not found"}), 404

    # Get line items
    line_items = db.filter("LPO Line Items", {"LPO ID": lpo_id})

    # Get supplier
    supplier = db.get_by_id("Suppliers", "Supplier ID", lpo["Supplier ID"])

    lpo_data = {
        "lpo": lpo,
        "line_items": line_items,
        "supplier": supplier
    }

    # Generate PDF
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name

    pdf_gen.generate_lpo(lpo_data, pdf_path)

    return send_file(
        pdf_path,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"LPO_{lpo_id}.pdf"
    )


# ============================================================================
# LPO GENERATOR (standalone - local storage, no Google Sheets)
# ============================================================================

LPO_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.tmp', 'lpo')


def _lpo_dir():
    os.makedirs(LPO_DATA_DIR, exist_ok=True)
    return LPO_DATA_DIR


def _lpo_read_json(filename, default):
    path = os.path.join(_lpo_dir(), filename)
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return default


def _lpo_write_json(filename, data):
    path = os.path.join(_lpo_dir(), filename)
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)


@app.route('/api/lpo-gen/next-number', methods=['GET'])
def lpo_gen_next_number():
    """Get next LPO number suggestion"""
    counter = _lpo_read_json('counter.json', {'current': 1001})
    year = datetime.now().year
    lpo_number = f"LPO-{year}-{counter['current']:04d}"
    return jsonify({"success": True, "lpo_number": lpo_number, "counter": counter['current']})


@app.route('/api/lpo-gen/sites', methods=['GET'])
def lpo_gen_get_sites():
    """Get list of delivery sites"""
    sites = _lpo_read_json('sites.json', [])
    return jsonify({"success": True, "sites": sites})


@app.route('/api/lpo-gen/sites', methods=['POST'])
def lpo_gen_add_site():
    """Add a new delivery site"""
    data = request.json
    site_name = data.get('name', '').strip()
    if not site_name:
        return jsonify({"success": False, "error": "Site name required"}), 400
    sites = _lpo_read_json('sites.json', [])
    if site_name not in sites:
        sites.append(site_name)
        _lpo_write_json('sites.json', sites)
    return jsonify({"success": True, "sites": sites})


@app.route('/api/lpo-gen/sites/<int:idx>', methods=['DELETE'])
def lpo_gen_delete_site(idx):
    """Remove a delivery site by index"""
    sites = _lpo_read_json('sites.json', [])
    if idx < 0 or idx >= len(sites):
        return jsonify({"success": False, "error": "Invalid index"}), 400
    sites.pop(idx)
    _lpo_write_json('sites.json', sites)
    return jsonify({"success": True, "sites": sites})


def _pdf_extract_line_items(page):
    """Use PyMuPDF's table parser to deterministically extract line items from a PDF page."""
    def parse_num(s):
        try:
            return float(str(s or '').replace(',', '').strip())
        except (ValueError, TypeError):
            return 0.0

    tables = page.find_tables()
    if not tables or not tables.tables:
        return None

    main_tbl = max(tables.tables, key=lambda t: t.col_count)
    rows = main_tbl.extract()
    if not rows or len(rows) < 2:
        return None

    # Find header row (first row with 4+ non-empty cells)
    header_idx, header = 0, []
    for i, row in enumerate(rows):
        clean = [str(c or '').strip() for c in row]
        if sum(1 for c in clean if c) >= 4:
            header = clean
            header_idx = i
            break
    if not header:
        return None

    hl = [h.lower() for h in header]

    # Identify key columns
    col_qty = col_rate = col_total = None
    skip_cols = set()
    for j, h in enumerate(hl):
        if 'total qty' in h:
            col_qty = j;  skip_cols.add(j)
        elif 'unit rate' in h:
            col_rate = j; skip_cols.add(j)
        elif 'total' in h and 'qty' not in h and j >= len(hl) - 2:
            col_total = j; skip_cols.add(j)
        elif h in ('s.no', 'sno', 's.n', 'no.', 'no', '#'):
            skip_cols.add(j)
        elif 'req qty' in h or ('req' in h and 'qty' in h):
            skip_cols.add(j)   # weight column, not order qty
        elif 'unit weight' in h or ('weight' in h and 'approx' in h):
            skip_cols.add(j)   # weight per piece, not description

    if col_qty is None or col_rate is None:
        return None

    SUMMARY_KEYWORDS = ('grand total', 'vat', 'total (aed)', 'terms', 'conditions',
                        'approved', 'signature', 'validity', 'payment terms', 'stock')

    def _is_summary_row(raw_row):
        """True if this row is a totals/footer row, not a line item."""
        text = ' '.join(str(c or '').lower() for c in raw_row)
        return any(kw in text for kw in SUMMARY_KEYWORDS)

    # Extract data rows; carry forward merged cell values only for non-summary rows
    data_rows = [list(row) for row in rows[header_idx + 1:]]
    n_cols = len(data_rows[0]) if data_rows else 0
    last_desc_val = [None] * n_cols

    for row in data_rows:
        if _is_summary_row(row):
            continue   # don't update carry-forward from summary rows
        for j in range(min(n_cols, len(row))):
            if j in skip_cols:
                continue
            val = str(row[j] or '').strip()
            if val:
                last_desc_val[j] = val
            elif last_desc_val[j]:
                row[j] = last_desc_val[j]

    line_items = []
    for row in data_rows:
        if _is_summary_row(row):
            continue   # skip Grand Total / VAT / Total rows

        cells = [str(c or '').strip() for c in row]

        qty   = parse_num(cells[col_qty])   if col_qty  is not None and col_qty  < len(cells) else 0
        rate  = parse_num(cells[col_rate])  if col_rate is not None and col_rate < len(cells) else 0
        total = parse_num(cells[col_total]) if col_total is not None and col_total < len(cells) else qty * rate

        # Skip rows with no real data or summary-style rows (rate=0 but large total)
        if qty == 0 and rate == 0 and total == 0:
            continue
        if rate == 0 and total > 0:   # Grand Total / VAT row leaked through
            continue

        # Build description from all non-skip columns
        desc_parts = [cells[j] for j in range(len(cells)) if j not in skip_cols and cells[j]]
        desc = ' '.join(desc_parts).strip()
        if not desc:
            continue

        unit = 'Pcs' if any(w in desc.lower() for w in ['pipe', 'tube', 'straight']) else 'Nos'
        line_items.append({
            'description': desc,
            'quantity':    qty,
            'unit':        unit,
            'unit_price':  rate,
            'total_price': total,
        })

    return line_items if line_items else None


@app.route('/api/lpo-gen/extract', methods=['POST'])
def lpo_gen_extract():
    """Upload quotation (image or PDF) and extract data using AI"""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files['file']
    ext = os.path.splitext(file.filename)[1].lower()

    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as tmp:
        file.save(tmp.name)
        tmp_path = tmp.name

    try:
        if ext == '.pdf':
            import fitz
            img_tmp = None
            try:
                doc = fitz.open(tmp_path)
                if doc.page_count == 0:
                    raise ValueError("PDF has no pages")
                page = doc[0]

                # Step 1: try deterministic table extraction for line items
                try:
                    line_items = _pdf_extract_line_items(page)
                except Exception:
                    line_items = None

                # Step 2: use AI (text) for header fields
                try:
                    page_text = page.get_text()
                except Exception:
                    page_text = ''
                doc.close()

                if line_items and page_text:
                    header_result = ai_extractor.extract_from_text(page_text, 'supplier_quotation')
                    if header_result.get('success'):
                        data = header_result['data']
                        data['line_items'] = line_items
                        subtotal = sum(i['total_price'] for i in line_items)
                        vat_pct  = float(data.get('vat_percent', 5) or 5)
                        vat_amt  = round(subtotal * vat_pct / 100, 2)
                        data['subtotal']     = round(subtotal, 2)
                        data['vat_amount']   = vat_amt
                        data['total_amount'] = round(subtotal + vat_amt, 2)
                        result = {'success': True, 'data': data}
                    else:
                        raise ValueError("header extraction failed")
                else:
                    raise ValueError("falling back to vision")
            except Exception:
                # Fall back: render page as image and use vision
                try:
                    doc2 = fitz.open(tmp_path)
                    pix = doc2[0].get_pixmap(matrix=fitz.Matrix(2, 2))
                    img_tmp = tmp_path.replace('.pdf', '_page1.png')
                    pix.save(img_tmp)
                    doc2.close()
                    result = ai_extractor.extract_from_image(img_tmp, 'supplier_quotation')
                except Exception as vision_err:
                    result = {'success': False, 'error': f'Could not process PDF: {vision_err}'}
        else:
            # For images: use vision API
            result = ai_extractor.extract_from_image(tmp_path, 'supplier_quotation')

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        img_tmp_path = tmp_path.replace('.pdf', '_page1.png')
        if os.path.exists(img_tmp_path):
            os.unlink(img_tmp_path)


@app.route('/api/lpo-gen/pdf', methods=['POST'])
def lpo_gen_pdf():
    """Generate LPO PDF from provided data (stateless, no DB)"""
    data = request.json

    # Build lpo_data in the format pdf_generator expects
    lpo_info = data.get('lpo', {})
    supplier = data.get('supplier', {})
    line_items = data.get('line_items', [])

    lpo_data = {
        'lpo': lpo_info,
        'supplier': supplier,
        'line_items': line_items
    }

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name

    try:
        pdf_gen.generate_lpo(lpo_data, pdf_path)

        lpo_number = lpo_info.get('lpo_id', lpo_info.get('LPO ID', 'LPO'))

        # Bump counter if LPO number matches auto-generated format
        try:
            counter = _lpo_read_json('counter.json', {'current': 1001})
            year = datetime.now().year
            expected = f"LPO-{year}-{counter['current']:04d}"
            if lpo_number == expected:
                counter['current'] += 1
                _lpo_write_json('counter.json', counter)
        except Exception:
            pass

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{lpo_number}.pdf"
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================================
# REPORTS
# ============================================================================

@app.route('/api/reports/outstanding-lpos', methods=['GET'])
def get_outstanding_lpos_report():
    """Get outstanding LPOs report"""
    report = reports.outstanding_lpos_report()
    return jsonify({"success": True, "data": report})


@app.route('/api/reports/pending-cheques', methods=['GET'])
def get_pending_cheques_report():
    """Get pending cheques report"""
    status = request.args.get('status', 'all')
    report = reports.pending_cheques_report(status)
    return jsonify({"success": True, "data": report})


@app.route('/api/reports/cash-flow', methods=['GET'])
def get_cash_flow_report():
    """Get cash flow projection report"""
    days = int(request.args.get('days', 90))
    report = reports.cash_flow_projection(days)
    return jsonify({"success": True, "data": report})


@app.route('/api/reports/supplier-spending', methods=['GET'])
def get_supplier_spending_report():
    """Get supplier spending analysis"""
    report = reports.supplier_spending_analysis()
    return jsonify({"success": True, "data": report})


# ============================================================================
# APPROVALS
# ============================================================================

@app.route('/api/approvals/pending', methods=['GET'])
def get_pending_approvals():
    """Get all pending approvals"""
    pending_lpos = db.filter("LPOs", {"Status": "Pending Approval"})
    pending_cheques = db.filter("Cheques", {"Status": "Created"})

    return jsonify({
        "success": True,
        "data": {
            "lpos": pending_lpos,
            "cheques": pending_cheques
        }
    })


# ============================================================================
# AI CHAT ASSISTANT
# ============================================================================

@app.route('/api/chat', methods=['POST'])
def ai_chat():
    """AI Chat Assistant using OpenAI"""
    try:
        data = request.json
        user_message = data.get('message', '')

        if not user_message:
            return jsonify({"success": False, "error": "No message provided"}), 400

        # Get context from database
        suppliers = db.get_all("Suppliers")
        lpos = db.get_all("LPOs")
        cheques = db.get_all("Cheques")

        # Build supplier details with departments
        supplier_details = []
        for s in suppliers:
            name = s.get('Supplier Name', '')
            notes = s.get('Notes', '')
            if notes:
                supplier_details.append(f"{name} ({notes})")
            else:
                supplier_details.append(name)

        # Build context for AI
        context = f"""You are an AI assistant for Newell Electromechanical Works LLC's Purchase & Accounting System.

Current System Data:
- Total Suppliers: {len(suppliers)}
- Total LPOs: {len(lpos)}
- Total Cheques: {len(cheques)}

All Suppliers with Departments:
{chr(10).join(['- ' + detail for detail in supplier_details])}

User Question: {user_message}

Provide a helpful, concise response about the user's purchase and accounting data."""

        # Call OpenAI
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a helpful AI assistant for a purchase and accounting system in Dubai."},
                {"role": "user", "content": context}
            ],
            max_tokens=200,
            temperature=0.7
        )

        ai_response = response.choices[0].message.content

        return jsonify({
            "success": True,
            "response": ai_response
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "response": f"I'm having trouble accessing the AI service. Error: {str(e)}"
        })


if __name__ == "__main__":
    port = int(os.getenv('PORT') or os.getenv('API_PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'

    print(f"Starting Purchase & Accounting API on port {port}")
    print(f"Debug mode: {debug}")

    app.run(host='0.0.0.0', port=port, debug=debug)

