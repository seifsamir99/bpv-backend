"""
Flask API for Purchase & Accounting System
Provides REST endpoints for frontend to interact with Google Sheets backend
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from googleapiclient.errors import HttpError
import db as pgdb  # Supabase Postgres layer ('db' name is taken by the SheetsDB instance below)
from sheets_operations import SheetsDB
from new_bpv_service import NewBPVService
from employee_attendance_service import EmployeeAttendanceService
import time_entries_service as time_log
import labour_log_service as labour_log
from ai_extraction import AIExtractor
from pdf_generator import PDFGenerator
from approval_workflow import ApprovalWorkflow
from reporting import ReportGenerator
from email_notifications import EmailNotifier
import credit_form_service as credit_form
import payment_cert_service as payment_cert
import supplier_ledger_service as supplier_ledger
import statement_extraction
import lpo_records_service
import job_order_records_service
import os
import io
import json
import uuid
import psycopg2
import functools
import hmac
import threading
import time
from datetime import datetime, timedelta
import tempfile
import base64
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for frontend

# Initialize services
db = SheetsDB()
bpv_service = NewBPVService()
emp_service = EmployeeAttendanceService()
ai_extractor = AIExtractor()
pdf_gen = PDFGenerator()
approval = ApprovalWorkflow()
reports = ReportGenerator()
notifier = EmailNotifier()


def safe_error(e):
    """Log the full exception server-side; return a client-safe message.

    Google API errors carry the spreadsheet ID and request URL in their text,
    so they must never be echoed back to the browser. Domain errors raised by
    our own services ("Voucher not found", etc.) pass through unchanged.
    """
    app.logger.error("API error: %s", e, exc_info=True)
    msg = str(e)
    if isinstance(e, HttpError) or 'googleapis.com' in msg or 'HttpError' in msg:
        return "Data source error. Please check that the required sheet tab exists."
    return msg


# Error handler
@app.errorhandler(Exception)
def handle_error(error):
    """Last-resort handler: log server-side, return a client-safe JSON body.

    The status must reflect who is at fault. This used to return 400 for
    *everything*, which is how the 2026-08-20 database outage reached the
    browser as "Request failed with status code 400" -- a client error, so the
    frontend looked broken while the real fault was a dead connection pool.
    A 404 for an unknown route came back as 400 for the same reason.
    """
    if isinstance(error, HTTPException):
        return jsonify({"success": False, "error": error.description}), error.code
    return jsonify({"success": False, "error": safe_error(error)}), 500


def _time_entry_json(row):
    """Serialize a raw time_entries row (from time_entries_service) to JSON.
    Admin-facing only — includes `note`. See _time_entry_json_public for the
    self-service /checkin routes, which must never expose admin notes."""
    if row is None:
        return None
    return {
        'id': row['id'],
        'entryDate': row['entry_date'].isoformat(),
        'checkInAt': row['check_in_at'].isoformat() if row['check_in_at'] else None,
        'checkOutAt': row['check_out_at'].isoformat() if row['check_out_at'] else None,
        'checkInLat': row['check_in_lat'],
        'checkInLng': row['check_in_lng'],
        'checkOutLat': row['check_out_lat'],
        'checkOutLng': row['check_out_lng'],
        'status': row['status'],
        'note': row['note'],
        'earlyLeave': bool(row['early_leave']),
    }


def _time_entry_json_public(row):
    """Serialize a time_entries row for the engineer's own self-service page —
    everything except `note`, which may contain admin-only remarks about them."""
    data = _time_entry_json(row)
    if data is not None:
        data.pop('note', None)
    return data


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/bpv/next-number', methods=['GET'])
def get_next_bpv_number():
    """Get the next available BPV number"""
    try:
        next_num = bpv_service.get_next_bpv_number()
        return jsonify({"success": True, "data": {"nextNumber": next_num}})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/bpv/<bpv_no>', methods=['GET'])
def get_bpv_voucher(bpv_no):
    """Get single BPV voucher by number"""
    try:
        voucher = bpv_service.get_voucher(bpv_no)
        if not voucher:
            return jsonify({"success": False, "error": "Voucher not found"}), 404
        return jsonify({"success": True, "data": voucher})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/bpv/<bpv_no>', methods=['DELETE'])
def delete_bpv_voucher(bpv_no):
    """Delete BPV voucher (clears data, keeps template)"""
    try:
        result = bpv_service.delete_voucher(bpv_no)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/pdc', methods=['POST'])
def add_manual_pdc():
    """Add a new manual PDC entry"""
    try:
        data = request.json
        result = bpv_service.add_manual_pdc(data)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/pdc/<int:row_index>', methods=['DELETE'])
def delete_manual_pdc(row_index):
    """Delete a manual PDC entry by its 1-based data row index"""
    try:
        result = bpv_service.delete_manual_pdc(row_index)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/tax-invoices', methods=['GET'])
def get_tax_invoices():
    """Get tax invoices for a month (month is 0-indexed)."""
    try:
        month = int(request.args.get('month', 0))
        year = int(request.args.get('year', datetime.now().year))
        records = bpv_service.get_tax_invoices(month, year)
        return jsonify({"success": True, "data": records})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/tax-invoices', methods=['POST'])
def add_tax_invoice():
    """Add a new tax invoice."""
    try:
        result = bpv_service.add_tax_invoice(request.json or {})
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/tax-invoices/<int:invoice_id>', methods=['PUT'])
def update_tax_invoice(invoice_id):
    """Update a tax invoice by id."""
    try:
        result = bpv_service.update_tax_invoice(invoice_id, request.json or {})
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/tax-invoices/<int:invoice_id>', methods=['DELETE'])
def delete_tax_invoice(invoice_id):
    """Delete a tax invoice by id."""
    try:
        result = bpv_service.delete_tax_invoice(invoice_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


# ============================================================================
# SUPPLIER LEDGER (/supplier-ledger page) -- accounts payable: invoices owed to
# suppliers, payments allocated against them, ageing. Prefixed
# /api/supplier-ledger because /api/suppliers is the legacy Sheets route.
# ============================================================================

def _sl_fail(exc, status=400):
    """LedgerError refusals carry no DB detail -- return them verbatim."""
    if isinstance(exc, supplier_ledger.LedgerError):
        return jsonify({"success": False, "error": str(exc)}), status
    return jsonify({"success": False, "error": safe_error(exc)}), 500


@app.route('/api/supplier-ledger/summary', methods=['GET'])
def supplier_ledger_summary():
    try:
        return jsonify({"success": True, "data": supplier_ledger.summary(request.args.get('asOf'))})
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/suppliers', methods=['POST'])
def supplier_ledger_create_supplier():
    try:
        return jsonify({"success": True, "data": supplier_ledger.create_supplier(request.json or {})})
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/suppliers/<int:sid>', methods=['PUT'])
def supplier_ledger_update_supplier(sid):
    try:
        return jsonify({"success": True, "data": supplier_ledger.update_supplier(sid, request.json or {})})
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/suppliers/<int:sid>', methods=['DELETE'])
def supplier_ledger_delete_supplier(sid):
    try:
        return jsonify(supplier_ledger.delete_supplier(sid))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/suppliers/<int:sid>/statement', methods=['GET'])
def supplier_ledger_statement(sid):
    try:
        data = supplier_ledger.statement(sid, request.args.get('asOf'))
        if not data:
            return jsonify({"success": False, "error": "Supplier not found"}), 404
        return jsonify({"success": True, "data": data})
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/invoices', methods=['POST'])
def supplier_ledger_add_invoices():
    """Body: {supplierId, invoices: [...]} or a single invoice {supplierId, ...}."""
    try:
        body = request.json or {}
        items = body.get('invoices') or [body]
        return jsonify(supplier_ledger.add_invoices(body.get('supplierId'), items))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/invoices/<int:iid>', methods=['PUT'])
def supplier_ledger_update_invoice(iid):
    try:
        return jsonify(supplier_ledger.update_invoice(iid, request.json or {}))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/invoices/<int:iid>', methods=['DELETE'])
def supplier_ledger_delete_invoice(iid):
    try:
        return jsonify(supplier_ledger.delete_invoice(iid))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/payments', methods=['POST'])
def supplier_ledger_add_payment():
    try:
        return jsonify(supplier_ledger.add_payment(request.json or {}))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/payments/<int:pid>', methods=['DELETE'])
def supplier_ledger_delete_payment(pid):
    try:
        return jsonify(supplier_ledger.delete_payment(pid))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/extract', methods=['POST'])
def supplier_ledger_extract():
    """Upload a statement (one PDF, or several photos as `files`) -> Claude
    reads every row. Nothing is saved; the page reviews it and then POSTs
    /import. Also returns the best-matching existing supplier."""
    try:
        uploads = request.files.getlist('files') or request.files.getlist('file')
        data = statement_extraction.extract_statement([(f.filename, f.read()) for f in uploads])
        data['matchedSupplier'] = supplier_ledger.match_supplier(data.get('supplierName'))
        return jsonify({"success": True, "data": data})
    except statement_extraction.ExtractionError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/suppliers/<int:sid>/references', methods=['GET'])
def supplier_ledger_references(sid):
    try:
        return jsonify({"success": True, "data": supplier_ledger.supplier_references(sid)})
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/supplier-ledger/import', methods=['POST'])
def supplier_ledger_import():
    try:
        return jsonify(supplier_ledger.import_statement(request.json or {}))
    except Exception as e:
        return _sl_fail(e)


@app.route('/api/pdc/statuses', methods=['GET'])
def get_pdc_statuses():
    """Get all PDC statuses as {chequeNo: status}"""
    try:
        statuses = bpv_service.get_pdc_statuses()
        return jsonify({"success": True, "data": statuses})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/pdc/status-by-cheque/<cheque_no>', methods=['PATCH'])
def update_pdc_status(cheque_no):
    """Update status for a single PDC cheque"""
    try:
        data = request.json
        status = data.get('status', '')
        result = bpv_service.update_pdc_status(cheque_no, status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/pdc/bulk-status', methods=['PATCH'])
def bulk_update_pdc_status():
    """Bulk update PDC statuses.
    Accepts: {chequeNumbers: [...], status: "..."} or {updates: [{chequeNo, status}]}
    """
    try:
        data = request.json
        if 'chequeNumbers' in data:
            status = data.get('status', 'Not Released')
            updates = [{'chequeNo': c, 'status': status} for c in data['chequeNumbers']]
        else:
            updates = data.get('updates', [])
        result = bpv_service.bulk_update_pdc_status(updates)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/cdc/status-by-cheque/<cheque_no>', methods=['PATCH'])
def update_cdc_status(cheque_no):
    """Update status for a single CDC cheque"""
    try:
        data = request.json
        status = data.get('status', '')
        result = bpv_service.update_cdc_status(cheque_no, status)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/cdc/bulk-status', methods=['PATCH'])
def bulk_update_cdc_status():
    """Bulk update CDC statuses.
    Accepts: {chequeNumbers: [...], status: "..."} or {updates: [{chequeNo, status}]}
    """
    try:
        data = request.json
        if 'chequeNumbers' in data:
            status = data.get('status', 'Not Released')
            updates = [{'chequeNo': c, 'status': status} for c in data['chequeNumbers']]
        else:
            updates = data.get('updates', [])
        result = bpv_service.bulk_update_cdc_status(updates)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


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
# LPO GENERATOR (sites + counter persisted in Supabase — lpo_counter and
# lpo_sites tables, shared by every environment so LPO numbers never collide)
# ============================================================================


def _lpo_read_json(filename, default):
    """Legacy-signature shim: filename is 'counter.json' or 'sites.json'."""
    if filename == 'counter.json':
        row = pgdb.q_one("SELECT value FROM lpo_counter WHERE id = 1")
        return {'current': row['value']} if row else dict(default)
    return [r['name'] for r in pgdb.q("SELECT name FROM lpo_sites ORDER BY id")]


def _lpo_write_json(filename, data):
    """Legacy-signature shim: filename is 'counter.json' or 'sites.json'."""
    if filename == 'counter.json':
        pgdb.execute(
            """INSERT INTO lpo_counter (id, value) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value""",
            (int(data['current']),))
        return

    def fn(conn):  # replace the whole (tiny) site list, preserving order
        with conn.cursor() as cur:
            cur.execute("TRUNCATE lpo_sites RESTART IDENTITY")
            for site in data:
                cur.execute("INSERT INTO lpo_sites (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                            (site,))
    pgdb.transaction(fn)


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


# Quotations rarely run past a few pages; this just bounds the vision request size.
MAX_PDF_PAGES = 20


def _pdf_extract_all_pages(doc):
    """Run the table parser on every page of a PDF and join the results.
    Returns (line_items, text). line_items is None if any page has a table the
    parser can't read, so a multi-page quote falls back to vision instead of
    being silently cut short."""
    line_items, texts = [], []
    for page in doc:
        texts.append(page.get_text())
        try:
            items = _pdf_extract_line_items(page)
        except Exception:
            items = None
        if items:
            if line_items is not None:
                line_items.extend(items)
            continue
        try:
            has_table = bool(page.find_tables().tables)
        except Exception:
            has_table = True
        if has_table:
            line_items = None
    return (line_items or None), '\n'.join(texts)


def _pdf_render_pages(pdf_path):
    """Render up to MAX_PDF_PAGES pages to JPEGs next to the PDF (named
    <pdf>_page<N>.jpg so the caller's cleanup can find them); returns the paths."""
    import fitz
    paths = []
    with fitz.open(pdf_path) as doc:
        for i, page in enumerate(doc):
            if i >= MAX_PDF_PAGES:
                break
            img_path = pdf_path.replace('.pdf', f'_page{i + 1}.jpg')
            page.get_pixmap(matrix=fitz.Matrix(2, 2)).save(img_path, jpg_quality=85)
            paths.append(img_path)
    return paths


def _cleanup_pdf_pages(pdf_path):
    """Delete the page images _pdf_render_pages wrote for this PDF."""
    if not pdf_path.endswith('.pdf'):
        return
    for i in range(1, MAX_PDF_PAGES + 1):
        img_path = pdf_path.replace('.pdf', f'_page{i}.jpg')
        if os.path.exists(img_path):
            os.unlink(img_path)


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
            try:
                with fitz.open(tmp_path) as doc:
                    if doc.page_count == 0:
                        raise ValueError("PDF has no pages")
                    # Step 1: deterministic table extraction for line items (all pages)
                    line_items, page_text = _pdf_extract_all_pages(doc)

                # Step 2: use AI (text) for header fields
                if line_items and page_text.strip():
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
                # Fall back: render every page as an image and use vision
                try:
                    page_imgs = _pdf_render_pages(tmp_path)
                    result = ai_extractor.extract_from_images(page_imgs, 'supplier_quotation')
                except Exception as vision_err:
                    result = {'success': False, 'error': f'Could not process PDF: {vision_err}'}
        else:
            # For images: use vision API
            result = ai_extractor.extract_from_image(tmp_path, 'supplier_quotation')

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _cleanup_pdf_pages(tmp_path)


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

        # Save history record (non-fatal: PDF is still returned even if this fails)
        try:
            lpo_records_service.create_record(lpo_info, supplier, line_items)
        except Exception as e:
            app.logger.error("LPO history record save failed: %s", e, exc_info=True)

        # Bump counter if LPO number matches auto-generated format
        try:
            counter = _lpo_read_json('counter.json', {'current': 1001})
            year = datetime.now().year
            expected = f"LPO-{year}-{counter['current']:04d}"
            if lpo_number == expected:
                # Conditional increment: atomic, so two concurrent PDF
                # generations can never skip or reuse a number.
                pgdb.execute(
                    "UPDATE lpo_counter SET value = value + 1 WHERE id = 1 AND value = %s",
                    (counter['current'],))
        except Exception:
            pass

        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{lpo_number}.pdf"
        )
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/lpo-gen/records', methods=['GET'])
def lpo_records_list():
    """History list for the LPO History panel (search + date range, no pagination)."""
    try:
        records = lpo_records_service.list_records(
            search=request.args.get('search', ''),
            date_from=request.args.get('date_from', ''),
            date_to=request.args.get('date_to', ''))
        return jsonify({"success": True, "records": records})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/lpo-gen/records/<int:record_id>', methods=['GET'])
def lpo_records_get(record_id):
    """Full record detail, used to pre-fill the form when Edit is clicked."""
    try:
        record = lpo_records_service.get_record(record_id)
        if not record:
            return jsonify({"success": False, "error": "Record not found"}), 404
        return jsonify({"success": True, "record": record})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/lpo-gen/records/<int:record_id>/pdf', methods=['GET'])
def lpo_records_pdf(record_id):
    """Re-download a past LPO — regenerates the PDF from stored data, unchanged."""
    record = lpo_records_service.get_record(record_id)
    if not record:
        return jsonify({"success": False, "error": "Record not found"}), 404

    lpo_data = {
        'lpo': {
            'lpo_id': record['lpo_number'], 'lpo_date': record['lpo_date'],
            'quote_ref': record['quote_ref'], 'project': record['project'],
            'delivery_site': record['delivery_site'], 'delivery_contact': record['delivery_contact'],
            'delivery_phone': record['delivery_phone'], 'payment_terms': record['payment_terms'],
            'vat_percent': float(record['vat_percent']), 'discount': float(record['discount']),
            'pricing_mode': record['pricing_mode'],
            'subtotal_override': record['subtotal_override'],
            'total_override': record['total_override'],
            'extra_terms': record['extra_terms'],
            'notes': record['notes'],
            'signed': request.args.get('signed') == '1',
        },
        'supplier': {
            'name': record['supplier_name'], 'contact': record['supplier_contact'],
            'email': record['supplier_email'], 'phone': record['supplier_phone'],
            'address': record['supplier_address'], 'trn': record['supplier_trn'],
        },
        'line_items': record['line_items'],
    }
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name
    try:
        pdf_gen.generate_lpo(lpo_data, pdf_path)
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True,
                          download_name=f"{record['lpo_number']}.pdf")
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/lpo-gen/records/<int:record_id>', methods=['PUT'])
def lpo_records_update(record_id):
    """Save edits to a past LPO: regenerate its PDF and overwrite the stored record.

    Same body shape as POST /api/lpo-gen/pdf. Does NOT touch lpo_counter —
    only the original create route advances the counter.
    """
    if not lpo_records_service.get_record(record_id):
        return jsonify({"success": False, "error": "Record not found"}), 404

    data = request.json
    lpo_info = data.get('lpo', {})
    supplier = data.get('supplier', {})
    line_items = data.get('line_items', [])

    lpo_data = {'lpo': lpo_info, 'supplier': supplier, 'line_items': line_items}
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name
    try:
        pdf_gen.generate_lpo(lpo_data, pdf_path)
        lpo_records_service.update_record(record_id, lpo_info, supplier, line_items)
        lpo_number = lpo_info.get('lpo_id', 'LPO')
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True,
                          download_name=f"{lpo_number}.pdf")
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/lpo-gen/records/<int:record_id>', methods=['DELETE'])
def lpo_records_delete(record_id):
    try:
        deleted = lpo_records_service.delete_record(record_id)
        if not deleted:
            return jsonify({"success": False, "error": "Record not found"}), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


# ============================================================================
# JOB ORDER GENERATOR (subcontractor work orders — separate counter/list from
# LPO's material-supplier lpo_counter/lpo_sites; state in Supabase
# job_order_counter and job_order_subcontractors tables)
# ============================================================================


def _jo_read_json(filename, default):
    """Legacy-signature shim, mirrors _lpo_read_json: 'counter.json' or 'subcontractors.json'."""
    if filename == 'counter.json':
        row = pgdb.q_one("SELECT value FROM job_order_counter WHERE id = 1")
        return {'current': row['value']} if row else dict(default)
    return [r['name'] for r in pgdb.q("SELECT name FROM job_order_subcontractors ORDER BY id")]


def _jo_write_json(filename, data):
    """Legacy-signature shim, mirrors _lpo_write_json."""
    if filename == 'counter.json':
        pgdb.execute(
            """INSERT INTO job_order_counter (id, value) VALUES (1, %s)
               ON CONFLICT (id) DO UPDATE SET value = EXCLUDED.value""",
            (int(data['current']),))
        return

    def fn(conn):  # replace the whole (tiny) subcontractor list, preserving order
        with conn.cursor() as cur:
            cur.execute("TRUNCATE job_order_subcontractors RESTART IDENTITY")
            for sub in data:
                cur.execute(
                    "INSERT INTO job_order_subcontractors (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
                    (sub,))
    pgdb.transaction(fn)


def _jo_ref_number(project_code, counter_value):
    """Build 'JO/NEMW/{project_code} / {year}/{NNNNN}' — 5-digit sequence, matching real docs."""
    year = datetime.now().year
    return f"JO/NEMW/{project_code} / {year}/{counter_value:05d}"


@app.route('/api/job-order/next-number', methods=['GET'])
def job_order_next_number():
    """Get next Job Order ref suggestion; project_code is caller-supplied (not part of the counter)."""
    project_code = request.args.get('project_code', '').strip()
    counter = _jo_read_json('counter.json', {'current': 1})
    ref_number = _jo_ref_number(project_code, counter['current'])
    return jsonify({"success": True, "ref_number": ref_number, "counter": counter['current']})


@app.route('/api/job-order/subcontractors', methods=['GET'])
def job_order_get_subcontractors():
    """Get list of saved subcontractors"""
    subs = _jo_read_json('subcontractors.json', [])
    return jsonify({"success": True, "subcontractors": subs})


@app.route('/api/job-order/subcontractors', methods=['POST'])
def job_order_add_subcontractor():
    """Add a new subcontractor"""
    data = request.json
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"success": False, "error": "Subcontractor name required"}), 400
    subs = _jo_read_json('subcontractors.json', [])
    if name not in subs:
        subs.append(name)
        _jo_write_json('subcontractors.json', subs)
    return jsonify({"success": True, "subcontractors": subs})


@app.route('/api/job-order/subcontractors/<int:idx>', methods=['DELETE'])
def job_order_delete_subcontractor(idx):
    """Remove a subcontractor by index"""
    subs = _jo_read_json('subcontractors.json', [])
    if idx < 0 or idx >= len(subs):
        return jsonify({"success": False, "error": "Invalid index"}), 400
    subs.pop(idx)
    _jo_write_json('subcontractors.json', subs)
    return jsonify({"success": True, "subcontractors": subs})


@app.route('/api/job-order/extract', methods=['POST'])
def job_order_extract():
    """Upload a subcontractor quotation (image or PDF) and extract data using AI"""
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
            try:
                with fitz.open(tmp_path) as doc:
                    if doc.page_count == 0:
                        raise ValueError("PDF has no pages")
                    # Step 1: deterministic table extraction for line items (all pages)
                    line_items, page_text = _pdf_extract_all_pages(doc)

                # Step 2: use AI (text) for header fields
                if line_items and page_text.strip():
                    header_result = ai_extractor.extract_from_text(page_text, 'job_order_quotation')
                    if header_result.get('success'):
                        data = header_result['data']
                        data['line_items'] = line_items
                        data['has_unit_pricing'] = any(
                            float(i.get('unit_price', 0) or 0) > 0 for i in line_items)
                        result = {'success': True, 'data': data}
                    else:
                        raise ValueError("header extraction failed")
                else:
                    raise ValueError("falling back to vision")
            except Exception:
                # Fall back: render every page as an image and use vision
                try:
                    page_imgs = _pdf_render_pages(tmp_path)
                    result = ai_extractor.extract_from_images(page_imgs, 'job_order_quotation')
                except Exception as vision_err:
                    result = {'success': False, 'error': f'Could not process PDF: {vision_err}'}
        else:
            # For images: use vision API
            result = ai_extractor.extract_from_image(tmp_path, 'job_order_quotation')

        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        _cleanup_pdf_pages(tmp_path)


@app.route('/api/job-order/pdf', methods=['POST'])
def job_order_pdf():
    """Generate Job Order PDF from provided data (stateless, no DB read besides the counter)"""
    data = request.json

    job_order_info = data.get('job_order', {})
    subcontractor = data.get('subcontractor', {})
    line_items = data.get('line_items', [])
    terms_text = data.get('terms_text', '')

    job_order_data = {
        'job_order': job_order_info,
        'subcontractor': subcontractor,
        'line_items': line_items,
        'terms_text': terms_text,
    }

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name

    try:
        pdf_gen.generate_job_order(job_order_data, pdf_path)

        ref_number = job_order_info.get('ref_number', 'JobOrder')

        # Save history record (non-fatal: PDF is still returned even if this fails)
        try:
            job_order_records_service.create_record(job_order_info, subcontractor, line_items, terms_text)
        except Exception as e:
            app.logger.error("Job Order history record save failed: %s", e, exc_info=True)

        # Bump counter if the submitted ref matches the expected auto-generated one
        try:
            project_code = job_order_info.get('project_code', '').strip()
            counter = _jo_read_json('counter.json', {'current': 1})
            expected = _jo_ref_number(project_code, counter['current'])
            if ref_number == expected:
                # Conditional increment: atomic, so two concurrent PDF
                # generations can never skip or reuse a number.
                pgdb.execute(
                    "UPDATE job_order_counter SET value = value + 1 WHERE id = 1 AND value = %s",
                    (counter['current'],))
        except Exception:
            pass

        safe_name = ref_number.replace('/', '-').replace(' ', '')
        return send_file(
            pdf_path,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{safe_name}.pdf"
        )
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/job-order/records', methods=['GET'])
def job_order_records_list():
    """History list for the Job Order History panel (search + date range, no pagination)."""
    try:
        records = job_order_records_service.list_records(
            search=request.args.get('search', ''),
            date_from=request.args.get('date_from', ''),
            date_to=request.args.get('date_to', ''))
        return jsonify({"success": True, "records": records})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/job-order/records/<int:record_id>', methods=['GET'])
def job_order_records_get(record_id):
    """Full record detail, used to pre-fill the form when Edit is clicked."""
    try:
        record = job_order_records_service.get_record(record_id)
        if not record:
            return jsonify({"success": False, "error": "Record not found"}), 404
        return jsonify({"success": True, "record": record})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/job-order/records/<int:record_id>/pdf', methods=['GET'])
def job_order_records_pdf(record_id):
    """Re-download a past Job Order — regenerates the PDF from stored data, unchanged."""
    record = job_order_records_service.get_record(record_id)
    if not record:
        return jsonify({"success": False, "error": "Record not found"}), 404

    job_order_data = {
        'job_order': {
            'ref_number': record['ref_number'], 'project_code': record['project_code'],
            'date': record['jo_date'], 'project': record['project'], 'sub': record['sub_scope'],
            'quote_date': record['quote_date'], 'has_unit_pricing': record['has_unit_pricing'],
            'discount': float(record['discount']), 'vat_enabled': record['vat_enabled'],
            'vat_percent': float(record['vat_percent']),
            'show_totals': record['show_totals'],
        },
        'subcontractor': {
            'name': record['subcontractor_name'], 'contact': record['subcontractor_contact'],
            'phone': record['subcontractor_phone'], 'address': record['subcontractor_address'],
        },
        'line_items': record['line_items'],
        'terms_text': record['terms_text'],
    }
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name
    try:
        pdf_gen.generate_job_order(job_order_data, pdf_path)
        safe_name = record['ref_number'].replace('/', '-').replace(' ', '')
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True,
                          download_name=f"{safe_name}.pdf")
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/job-order/records/<int:record_id>', methods=['PUT'])
def job_order_records_update(record_id):
    """Save edits to a past Job Order: regenerate its PDF and overwrite the stored record.

    Same body shape as POST /api/job-order/pdf. Does NOT touch job_order_counter —
    only the original create route advances the counter.
    """
    if not job_order_records_service.get_record(record_id):
        return jsonify({"success": False, "error": "Record not found"}), 404

    data = request.json
    job_order_info = data.get('job_order', {})
    subcontractor = data.get('subcontractor', {})
    line_items = data.get('line_items', [])
    terms_text = data.get('terms_text', '')

    job_order_data = {
        'job_order': job_order_info, 'subcontractor': subcontractor,
        'line_items': line_items, 'terms_text': terms_text,
    }
    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name
    try:
        pdf_gen.generate_job_order(job_order_data, pdf_path)
        job_order_records_service.update_record(record_id, job_order_info, subcontractor, line_items, terms_text)
        safe_name = job_order_info.get('ref_number', 'JobOrder').replace('/', '-').replace(' ', '')
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True,
                          download_name=f"{safe_name}.pdf")
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/job-order/records/<int:record_id>', methods=['DELETE'])
def job_order_records_delete(record_id):
    try:
        deleted = job_order_records_service.delete_record(record_id)
        if not deleted:
            return jsonify({"success": False, "error": "Record not found"}), 404
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


# ============================================================================
# SUB-CONTRACTOR PAYMENT CERTIFICATES (/payment-cert page)
# Newell issues these DOWNWARD to its own subcontractors. Unlike the LPO and
# Job Order generators above -- stateless, counter-only -- a payment
# certificate is cumulative, so contracts and issued certificates are stored
# and the running totals are derived, never re-typed. All arithmetic lives in
# payment_cert_service; nothing here computes money.
# ============================================================================

def _pc_fail(exc, status=400):
    """PaymentCertError messages are written FOR the user and carry no database
    detail, so they go back verbatim. Everything else still goes through
    safe_error() -- Postgres text leaks table and connection details."""
    if isinstance(exc, payment_cert.PaymentCertError):
        return jsonify({"success": False, "error": str(exc)}), status
    return jsonify({"success": False, "error": safe_error(exc)}), 500


@app.route('/api/payment-cert/contracts', methods=['GET'])
def payment_cert_list_contracts():
    """List subcontracts. ?archived=1 includes the finished ones."""
    try:
        include = request.args.get('archived', '') in ('1', 'true', 'yes')
        return jsonify({"success": True,
                        "contracts": payment_cert.list_contracts(include_archived=include)})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/contracts', methods=['POST'])
def payment_cert_create_contract():
    try:
        return jsonify({"success": True,
                        "contract": payment_cert.create_contract(request.json or {})})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/contracts/<int:cid>', methods=['GET'])
def payment_cert_get_contract(cid):
    try:
        contract = payment_cert.get_contract(cid)
        if not contract:
            return jsonify({"success": False, "error": "Contract not found"}), 404
        return jsonify({"success": True, "contract": contract})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/contracts/<int:cid>', methods=['PUT'])
def payment_cert_update_contract(cid):
    try:
        return jsonify({"success": True,
                        "contract": payment_cert.update_contract(cid, request.json or {})})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/contracts/<int:cid>', methods=['DELETE'])
def payment_cert_archive_contract(cid):
    """Archive, never delete: the certificates issued under a contract are
    financial records and the FK would cascade them away."""
    try:
        payment_cert.archive_contract(cid)
        return jsonify({"success": True})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/contracts/<int:cid>/certificates', methods=['GET'])
def payment_cert_list_certificates(cid):
    """Every certificate issued on a contract, plus where the contract stands."""
    try:
        contract = payment_cert.get_contract(cid)
        if not contract:
            return jsonify({"success": False, "error": "Contract not found"}), 404
        state = payment_cert.cumulative_state(contract)
        return jsonify({
            "success": True,
            "certificates": payment_cert.list_certificates(cid),
            "state": {
                "contract_value":       float(state['contract_value']),
                "previously_certified": float(state['previously_certified']),
                "previously_paid":      float(state['previously_paid']),
                "retention_held":       float(state['retention_held']),
                "balance_of_contract":  float(state['balance_of_contract']),
                "last_cert_no":         state['last_cert_no'],
            },
        })
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/preview', methods=['POST'])
def payment_cert_preview():
    """Compute the certificate without saving it.

    The page's live certification block renders straight from this, so the
    money arithmetic exists in exactly one place (payment_cert_service.compute)
    and the browser never re-implements it.
    """
    data = request.json or {}
    try:
        return jsonify({"success": True, "preview": payment_cert.preview(
            data.get('contract_id'),
            data.get('net_paid', data.get('gross')),
            data.get('other_deduction', 0))})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/certificates', methods=['POST'])
def payment_cert_save_certificate():
    """Issue the next certificate on a contract.

    Refuses to take the cumulative certified amount past the contract value
    unless allow_overrun is set -- variation orders are real, but must be
    deliberate rather than a typo.
    """
    data = request.json or {}
    try:
        cert = payment_cert.save_certificate(
            contract_id=data.get('contract_id'),
            net_paid=data.get('net_paid', data.get('gross')),
            invoice_date=data.get('invoice_date'),
            bill_month=data.get('bill_month', ''),
            bill_type=data.get('bill_type', 'interim'),
            other_deduction=data.get('other_deduction', 0),
            other_deduction_note=data.get('other_deduction_note', ''),
            scope_amounts=data.get('scope_amounts'),
            allow_overrun=bool(data.get('allow_overrun')),
        )
        return jsonify({"success": True, "certificate": cert})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/certificates/<int:pid>', methods=['DELETE'])
def payment_cert_delete_certificate(pid):
    """Delete a certificate -- only ever the latest on its contract, because
    removing one from the middle silently changes the cumulative figures on
    every certificate already issued after it."""
    try:
        payment_cert.delete_certificate(pid)
        return jsonify({"success": True})
    except Exception as e:
        return _pc_fail(e)


@app.route('/api/payment-cert/certificates/<int:pid>/pdf', methods=['GET'])
def payment_cert_pdf(pid):
    try:
        payload = payment_cert.certificate_pdf_payload(pid)
    except Exception as e:
        return _pc_fail(e)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
        pdf_path = tmp.name

    try:
        pdf_gen.generate_payment_certificate(payload, pdf_path)
        cert = payload['certificate']
        name = (cert.get('ref_number') or 'PaymentCertificate').replace('/', '-').replace(' ', '')
        return send_file(pdf_path, mimetype='application/pdf', as_attachment=True,
                         download_name=f"{name}.pdf")
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


# ============================================================================
# CREDIT APPLICATION FORMS (/credit-form page)
# Upload a supplier's blank form -> detect the blanks -> map them to our
# company profile -> user corrects visually -> filled PDF. Mapped forms are
# saved as templates in credit_form_templates.
# ============================================================================

# Uploaded blanks live here between /analyze and /pdf so the browser does not
# have to ship the file back on every generate. Bounded — this is a convenience
# cache, and a miss just asks the user to re-upload.
_credit_forms = {}
_CREDIT_CACHE_MAX = 8
_CREDIT_MAX_UPLOAD = 15 * 1024 * 1024  # no MAX_CONTENT_LENGTH is set app-wide


def _credit_cache(form_id, pdf_bytes):
    if len(_credit_forms) >= _CREDIT_CACHE_MAX:
        _credit_forms.pop(next(iter(_credit_forms)), None)
    _credit_forms[form_id] = pdf_bytes


def _credit_pdf_bytes(data):
    """Resolve the blank PDF a request refers to: cache, template, or inline."""
    form_id = data.get('form_id')
    if form_id and form_id in _credit_forms:
        return _credit_forms[form_id]

    template_id = data.get('template_id')
    if template_id:
        row = pgdb.q_one("SELECT pdf_bytes FROM credit_form_templates WHERE id = %s",
                         (int(template_id),))
        if row:
            return bytes(row['pdf_bytes'])

    if data.get('pdf_b64'):
        return base64.b64decode(data['pdf_b64'])

    raise ValueError("Blank form not found on the server — please upload it again.")


@app.route('/api/credit-form/profile', methods=['GET'])
def credit_form_profile():
    """Company profile defaults used to pre-fill the form."""
    return jsonify({"success": True, "profile": credit_form.company_profile()})


@app.route('/api/credit-form/analyze', methods=['POST'])
def credit_form_analyze():
    """Upload a blank credit form: rasterise it, find the blanks, map fields."""
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "No file uploaded"}), 400

    file = request.files['file']
    pdf_bytes = file.read()
    if not pdf_bytes:
        return jsonify({"success": False, "error": "Empty file"}), 400
    if len(pdf_bytes) > _CREDIT_MAX_UPLOAD:
        return jsonify({"success": False, "error": "File too large (max 15 MB)"}), 400
    if not pdf_bytes.startswith(b'%PDF'):
        return jsonify({"success": False, "error": "Only PDF forms are supported"}), 400

    try:
        analysis = credit_form.analyze(pdf_bytes)
        use_ai = request.form.get('use_ai', '1') != '0'
        mapping, ai_status = credit_form.map_fields(analysis, use_ai=use_ai)
        placements = credit_form.build_placements(analysis, mapping)

        form_id = uuid.uuid4().hex
        _credit_cache(form_id, pdf_bytes)

        # The images are the bulk of the payload; keep them out of `pages` meta.
        return jsonify({
            "success": True,
            "form_id": form_id,
            "ai_status": ai_status,
            "zoom": analysis['zoom'],
            "pages": [{"index": p['index'], "width": p['width'],
                       "height": p['height'], "image": p['image'],
                       "paragraphs": p['paragraphs']}
                      for p in analysis['pages']],
            "placements": placements,
            "edits": [],
            "suggested_name": os.path.splitext(file.filename or '')[0][:80],
        })
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/credit-form/pdf', methods=['POST'])
def credit_form_pdf():
    """Render the user's placements onto the blank form and return the PDF."""
    data = request.json or {}
    try:
        pdf_bytes = _credit_pdf_bytes(data)
        placements = [p for p in data.get('placements', []) if p.get('enabled', True)]
        edits = [e for e in data.get('edits', []) if e.get('mode') not in (None, '', 'keep')]
        filled = credit_form.render(pdf_bytes, placements, edits=edits)

        name = (data.get('name') or 'Credit Application').strip()
        safe_name = ''.join(ch for ch in name if ch.isalnum() or ch in ' -_')[:80]
        return send_file(
            io.BytesIO(filled),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"{safe_name or 'Credit Application'} - FILLED.pdf"
        )
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/credit-form/templates', methods=['GET'])
def credit_form_list_templates():
    """Saved supplier forms (metadata only — the PDFs are large)."""
    rows = pgdb.q("""SELECT id, supplier_name, updated_at
                     FROM credit_form_templates ORDER BY supplier_name""")
    return jsonify({"success": True, "templates": [
        {"id": r['id'], "supplier_name": r['supplier_name'],
         "updated_at": r['updated_at'].isoformat() if r['updated_at'] else None}
        for r in rows]})


@app.route('/api/credit-form/templates', methods=['POST'])
def credit_form_save_template():
    """Save (or overwrite) a supplier's mapping together with its blank PDF."""
    data = request.json or {}
    name = (data.get('supplier_name') or '').strip()
    if not name:
        return jsonify({"success": False, "error": "Supplier name required"}), 400

    try:
        pdf_bytes = _credit_pdf_bytes(data)
        pgdb.execute(
            """INSERT INTO credit_form_templates (supplier_name, placements, pdf_bytes)
               VALUES (%s, %s, %s)
               ON CONFLICT (supplier_name) DO UPDATE SET
                 placements = EXCLUDED.placements,
                 pdf_bytes  = EXCLUDED.pdf_bytes,
                 updated_at = now()""",
            (name, json.dumps({"placements": data.get('placements', []),
                               "edits": data.get('edits', [])}),
             psycopg2.Binary(pdf_bytes)))
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/credit-form/templates/<int:tid>', methods=['GET'])
def credit_form_get_template(tid):
    """Load a saved supplier form — page images + placements, no AI call."""
    row = pgdb.q_one("""SELECT supplier_name, placements, pdf_bytes
                        FROM credit_form_templates WHERE id = %s""", (tid,))
    if not row:
        return jsonify({"success": False, "error": "Template not found"}), 404

    try:
        pdf_bytes = bytes(row['pdf_bytes'])
        analysis = credit_form.analyze(pdf_bytes)
        form_id = uuid.uuid4().hex
        _credit_cache(form_id, pdf_bytes)

        # Templates saved before clause editing existed hold a bare array.
        saved = row['placements']
        if isinstance(saved, list):
            saved = {"placements": saved, "edits": []}

        return jsonify({
            "success": True,
            "form_id": form_id,
            "supplier_name": row['supplier_name'],
            "zoom": analysis['zoom'],
            "pages": [{"index": p['index'], "width": p['width'],
                       "height": p['height'], "image": p['image'],
                       "paragraphs": p['paragraphs']}
                      for p in analysis['pages']],
            "placements": saved.get('placements', []),
            "edits": saved.get('edits', []),
        })
    except Exception as e:
        return jsonify({"success": False, "error": safe_error(e)}), 500


@app.route('/api/credit-form/templates/<int:tid>', methods=['DELETE'])
def credit_form_delete_template(tid):
    pgdb.execute("DELETE FROM credit_form_templates WHERE id = %s", (tid,))
    return jsonify({"success": True})


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

def _chat_build_context():
    """Summarize live system data for the chat assistant.

    Every source is optional — this system's main sheet has no Suppliers/LPOs
    tabs (the old context source), so each block must fail soft rather than
    take the whole chat down.
    """
    parts = []
    try:
        vouchers = bpv_service.get_all_vouchers()
        total = sum(v.get('totalAmount') or 0 for v in vouchers)
        parts.append(f"BPV vouchers: {len(vouchers)} total, combined amount AED {total:,.2f}.")
        recent = vouchers[-5:]
        if recent:
            lines = []
            for v in recent:
                companies = ", ".join(sorted({
                    i.get('companyName', '') for i in v.get('lineItems', []) if i.get('companyName')
                }))
                lines.append(f"- BPV {v.get('bpvNo')} ({v.get('date')}): AED {v.get('totalAmount') or 0:,.2f} — {companies}")
            parts.append("Most recent vouchers:\n" + "\n".join(lines))
    except Exception:
        pass

    for label, getter in (("PDC", "get_pdc_statuses"), ("CDC", "get_cdc_statuses")):
        try:
            statuses = getattr(bpv_service, getter)()
            if statuses:
                counts = {}
                for status in statuses.values():
                    counts[status] = counts.get(status, 0) + 1
                summary = ", ".join(f"{n} {s}" for s, n in sorted(counts.items()))
                parts.append(f"{label} cheques tracked: {len(statuses)} ({summary}).")
        except Exception:
            pass

    try:
        labour = emp_service.get_employees('labour')
        staff = emp_service.get_employees('staff')
        parts.append(f"Employees: {len(labour)} labour, {len(staff)} staff.")
    except Exception:
        pass

    return "\n\n".join(parts) or "No system data is available right now."


@app.route('/api/chat', methods=['POST'])
def ai_chat():
    """AI chat assistant over live system data (BPV, PDC/CDC, employees).

    Engine: Claude (claude-opus-4-6, per CLAUDE.md) when a real
    ANTHROPIC_API_KEY is set; otherwise OpenAI; otherwise a friendly
    not-configured message.
    """
    try:
        data = request.json
        user_message = data.get('message', '')
        history = data.get('history', [])

        if not user_message:
            return jsonify({"success": False, "error": "No message provided"}), 400

        anthropic_key = os.getenv('ANTHROPIC_API_KEY', '')
        openai_key = os.getenv('OPENAI_API_KEY', '')
        use_claude = anthropic_key.startswith('sk-ant-')
        if not use_claude and not openai_key:
            return jsonify({
                "success": False,
                "error": "AI assistant is not configured yet — no AI API key is set on the server."
            })

        system_prompt = (
            "You are the AI assistant for Newell Electromechanical Works LLC's "
            "accounting system (Bank Payment Vouchers, PDC/CDC cheques, employees, "
            "payroll). Answer concisely using the live data below. If the data "
            "doesn't cover the question, say so instead of guessing.\n\n"
            + _chat_build_context()
        )

        messages = []
        for msg in history:
            role = msg.get('role')
            content = str(msg.get('content', '')).strip()
            if role in ('user', 'assistant') and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_message})

        def ask_claude():
            from anthropic import Anthropic
            client = Anthropic(api_key=anthropic_key)
            response = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1024,
                system=system_prompt,
                messages=messages,
            )
            return next((b.text for b in response.content if b.type == "text"), "")

        def ask_openai():
            from openai import OpenAI
            client = OpenAI(api_key=openai_key)
            response = client.chat.completions.create(
                model="gpt-4o",
                max_tokens=500,
                messages=[{"role": "system", "content": system_prompt}] + messages,
            )
            return response.choices[0].message.content

        if use_claude:
            try:
                ai_response = ask_claude()
            except Exception:
                if not openai_key:
                    raise
                app.logger.warning("Claude chat failed; falling back to OpenAI", exc_info=True)
                ai_response = ask_openai()
        else:
            ai_response = ask_openai()

        return jsonify({"success": True, "response": ai_response})

    except Exception as e:
        return jsonify({
            "success": False,
            "error": safe_error(e),
            "response": "Sorry, the AI assistant hit a problem answering that. Please try again."
        })


# ============================================================================
# EMPLOYEES
# ============================================================================

@app.route('/api/employees', methods=['GET'])
def get_employees():
    emp_type = request.args.get('type')
    employees = emp_service.get_employees(emp_type)
    return jsonify({'success': True, 'data': employees})


@app.route('/api/employees/validation', methods=['GET'])
def validate_employees():
    issues = emp_service.validate_employees()
    return jsonify({'success': True, 'totalIssues': len(issues), 'issues': issues})


@app.route('/api/employees/next-id', methods=['GET'])
def get_next_employee_id():
    emp_type = request.args.get('type', 'labour')
    next_id = emp_service.get_next_employee_id(emp_type)
    return jsonify({'success': True, 'data': {'nextId': next_id}})


@app.route('/api/employees/ot/bulk', methods=['POST'])
def bulk_update_ot():
    updates = request.json.get('updates', [])
    count = emp_service.bulk_update_ot(updates)
    return jsonify({'success': True, 'message': f'Updated {count} OT records'})


# ---- Month-End: per-month OT / bonus / deduction entries (month is 0-indexed) ----

@app.route('/api/month-end', methods=['GET'])
def get_month_end():
    now = datetime.now()
    month = request.args.get('month', type=int)
    year = request.args.get('year', type=int)
    month = month if month is not None else now.month - 1
    year = year if year is not None else now.year
    data = emp_service.get_month_end(month, year)
    return jsonify({'success': True, 'data': data})


@app.route('/api/month-end/bulk', methods=['POST'])
def save_month_end():
    body = request.json or {}
    month = body.get('month')
    year = body.get('year')
    updates = body.get('updates', [])
    if month is None or year is None:
        return jsonify({'success': False, 'error': 'month and year are required'}), 400
    count = emp_service.save_month_end(month, year, updates)
    return jsonify({'success': True, 'message': f'Saved {count} month-end records'})


@app.route('/api/employees/<employee_id>', methods=['GET'])
def get_employee(employee_id):
    emp = emp_service.get_employee(employee_id)
    if emp is None:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'data': emp})


@app.route('/api/employees', methods=['POST'])
def create_employee():
    data = request.json
    emp = emp_service.create_employee(data)
    return jsonify({'success': True, 'data': emp}), 201


@app.route('/api/employees/<employee_id>', methods=['PUT'])
def update_employee(employee_id):
    data = request.json
    result = emp_service.update_employee(employee_id, data)
    if result is None:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'data': result})


@app.route('/api/employees/<employee_id>', methods=['DELETE'])
def delete_employee(employee_id):
    msg = emp_service.delete_employee(employee_id)
    if msg is None:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'message': msg})


@app.route('/api/employees/<employee_id>/move', methods=['POST'])
def move_employee(employee_id):
    target_type = request.json.get('targetType')
    if target_type not in ('labour', 'staff'):
        return jsonify({'success': False, 'error': 'targetType must be "labour" or "staff"'}), 400
    ok, msg = emp_service.move_employee(employee_id, target_type)
    if not ok:
        return jsonify({'success': False, 'error': msg}), 404
    return jsonify({'success': True, 'message': msg})


@app.route('/api/employees/<employee_id>/fix-missing', methods=['POST'])
def fix_missing_salary(employee_id):
    body = request.json or {}
    target_type = body.get('targetType')
    if target_type not in ('labour', 'staff'):
        return jsonify({'success': False, 'error': 'targetType must be "labour" or "staff"'}), 400
    emp_data, err = emp_service.fix_missing_salary(
        employee_id, target_type,
        body.get('ratePerDay', 0), body.get('ratePerHour', 0), body.get('otHours', 0)
    )
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Created salary record for {emp_data["name"]}', 'data': emp_data})


@app.route('/api/employees/<employee_id>/ot', methods=['PUT'])
def update_employee_ot(employee_id):
    ot_hours = request.json.get('otHours')
    ok = emp_service.update_employee_ot(employee_id, ot_hours)
    if not ok:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'message': f'Updated OT hours for {employee_id}'})


# ============================================================================
# ATTENDANCE
# ============================================================================

@app.route('/api/attendance', methods=['GET'])
def get_attendance():
    emp_type = request.args.get('type', 'all')
    month = int(request.args['month']) if 'month' in request.args else None
    year = int(request.args['year']) if 'year' in request.args else None
    data = emp_service.get_attendance(emp_type, month, year)
    return jsonify({'success': True, 'data': data})


@app.route('/api/attendance/<employee_id>/<int:day>', methods=['PUT'])
def update_attendance(employee_id, day):
    body = request.json or {}
    status = body.get('status')
    emp_type = body.get('type', 'labour')
    month = body.get('month')
    year = body.get('year')
    ok, err = emp_service.update_attendance(employee_id, day, status, emp_type, month, year)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Updated {employee_id} day {day} to {status}'})


@app.route('/api/attendance/bulk', methods=['POST'])
def bulk_update_attendance():
    body = request.json or {}
    day = body.get('day')
    updates = body.get('updates', [])
    emp_type = body.get('type')
    month = body.get('month')
    year = body.get('year')
    count, err = emp_service.bulk_update_attendance(day, updates, emp_type, month, year)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Updated {count} attendance records'})


@app.route('/api/attendance/bulk-all-days', methods=['POST'])
def bulk_update_all_days():
    body = request.json or {}
    employee_id = body.get('employeeId')
    status = body.get('status')
    emp_type = body.get('type', 'labour')
    month = body.get('month')
    year = body.get('year')
    if not employee_id or not status:
        return jsonify({'success': False, 'error': 'employeeId and status are required'}), 400
    count, err = emp_service.bulk_update_all_days(employee_id, status, emp_type, month, year)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Updated {count} days for employee {employee_id}'})


@app.route('/api/attendance/bulk-all-days-all', methods=['POST'])
def bulk_update_all_days_all_employees():
    body = request.json or {}
    status = body.get('status')
    employee_ids = body.get('employeeIds')  # optional: limits to these employees (respects UI filters)
    emp_type = body.get('type', 'all')
    month = body.get('month')
    year = body.get('year')
    if not status:
        return jsonify({'success': False, 'error': 'status is required'}), 400
    count, err = emp_service.bulk_update_all_days_all_employees(status, employee_ids, emp_type, month, year)
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Updated {count} employees for the month'})


@app.route('/api/attendance/bulk-range', methods=['POST'])
def bulk_update_attendance_range():
    """Set one status from day X to day Y — a leave period, a shutdown.
    Sundays are included unless includeSundays is false."""
    body = request.json or {}
    status = body.get('status')
    from_day, to_day = body.get('fromDay'), body.get('toDay')
    if not status:
        return jsonify({'success': False, 'error': 'status is required'}), 400
    if from_day is None or to_day is None:
        return jsonify({'success': False, 'error': 'fromDay and toDay are required'}), 400
    count, err = emp_service.bulk_update_range(
        from_day, to_day, status,
        employee_ids=body.get('employeeIds'),
        emp_type=body.get('type', 'all'),
        month=body.get('month'), year=body.get('year'),
        include_sundays=body.get('includeSundays', True))
    if err:
        return jsonify({'success': False, 'error': err}), 400
    return jsonify({'success': True, 'message': f'Updated {count} employees from day {from_day} to {to_day}'})


# ============================================================================
# TIME LOG (site check-in / check-out)
# ============================================================================

@app.route('/api/checkin/<token>', methods=['GET'])
def get_checkin_status(token):
    employee = time_log.get_employee_by_token(token)
    if employee is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404
    entry = time_log.get_today_entry(employee['id'])
    return jsonify({'success': True, 'data': {
        'employeeName': employee['name'],
        'designation': employee['designation'],
        'todayEntry': _time_entry_json_public(entry),
    }})


@app.route('/api/checkin/<token>/in', methods=['POST'])
def checkin_in(token):
    employee = time_log.get_employee_by_token(token)
    if employee is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404
    body = request.json or {}
    lat, lng = body.get('lat'), body.get('lng')
    if lat is None or lng is None:
        return jsonify({'success': False, 'error': 'Location is required to check in.'}), 400
    # A self-marked Absent/Sick is final for the day — checking in would clear
    # it (see time_log.check_in), so block that route out of it too.
    existing = time_log.get_today_entry(employee['id'])
    if existing and existing['status']:
        return jsonify({'success': False, 'error':
                        "You are already marked %s for today. Contact the office to change it."
                        % existing['status'].capitalize()}), 409
    entry = time_log.check_in(employee['id'], lat=lat, lng=lng)
    return jsonify({'success': True, 'data': _time_entry_json_public(entry)})


@app.route('/api/checkin/<token>/out', methods=['POST'])
def checkin_out(token):
    employee = time_log.get_employee_by_token(token)
    if employee is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404
    body = request.json or {}
    lat, lng = body.get('lat'), body.get('lng')
    if lat is None or lng is None:
        return jsonify({'success': False, 'error': 'Location is required to check out.'}), 400
    entry = time_log.check_out(
        employee['id'], lat=lat, lng=lng,
        early_leave=body.get('earlyLeave', False), note=body.get('note'))
    return jsonify({'success': True, 'data': _time_entry_json_public(entry)})


@app.route('/api/checkin/<token>/status', methods=['PUT'])
def checkin_set_status(token):
    """Self-service: the engineer marks themselves Absent/Sick for today.

    One-way on purpose: they cannot clear it or switch Absent<->Sick afterwards,
    and cannot overwrite a day they already checked in for. Only the office can
    correct a day (admin Day view / DELETE /api/time-entries/<id>)."""
    employee = time_log.get_employee_by_token(token)
    if employee is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404
    body = request.json or {}
    status = body.get('status')
    if status not in time_log.STATUSES:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    existing = time_log.get_today_entry(employee['id'])
    if existing and existing['status']:
        return jsonify({'success': False, 'error':
                        "You are already marked %s for today. Contact the office to change it."
                        % existing['status'].capitalize()}), 409
    if existing and existing['check_in_at']:
        return jsonify({'success': False, 'error':
                        'You already checked in today. Contact the office to change it.'}), 409
    entry = time_log.set_status(employee['id'], status=status, note=body.get('note'))
    return jsonify({'success': True, 'data': _time_entry_json_public(entry)})


# Office Time Log edits (status, manual check-in, time edits, reset) are
# limited to the owner's own devices. There is no login system, so this is a
# shared secret: TIME_LOG_ADMIN_KEY in the env, saved once in the owner's
# browser by opening /time-log?admin=<key>, and sent as X-Admin-Key. Fails
# closed — with the env var unset, nobody can edit. Reading stays open, and
# the engineers' own check-in endpoints above are not affected.

def _is_time_log_admin():
    expected = os.getenv('TIME_LOG_ADMIN_KEY') or ''
    given = request.headers.get('X-Admin-Key') or ''
    return bool(expected) and hmac.compare_digest(given, expected)


def _require_time_log_admin(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not _is_time_log_admin():
            return jsonify({'success': False, 'error':
                            'Only the administrator can change the Time Log.'}), 403
        return fn(*args, **kwargs)
    return wrapper


@app.route('/api/time-entries/admin-check', methods=['GET'])
def time_log_admin_check():
    return jsonify({'success': True, 'data': {'admin': _is_time_log_admin()}})


@app.route('/api/time-entries', methods=['GET'])
def list_time_entries():
    year = int(request.args['year'])
    month = int(request.args['month'])
    employee_id = request.args.get('employeeId')
    data = time_log.list_entries(year, month, employee_id)
    return jsonify({'success': True, 'data': data})


@app.route('/api/time-entries/day', methods=['GET'])
def get_time_entries_day():
    entry_date = request.args['date']
    employee_id = request.args.get('employeeId')
    # Run the 9 AM auto-absent sweep before showing today, so the page is
    # right the moment it is opened rather than on the loop's next tick.
    if entry_date == datetime.now(time_log.UAE_TZ).date().isoformat():
        try:
            time_log.auto_mark_absent()
        except Exception as e:
            print(f"Auto-absent sweep failed: {e}")
    data = time_log.list_day(entry_date, employee_id)
    return jsonify({'success': True, 'data': data})


@app.route('/api/time-entries/status', methods=['PUT'])
@_require_time_log_admin
def set_time_entry_status():
    """Admin Day view: mark (or clear, with status=None) Absent/Sick for an employee."""
    body = request.json or {}
    employee_id = body.get('employeeId')
    entry_date = body.get('date')
    if not employee_id or not entry_date:
        return jsonify({'success': False, 'error': 'employeeId and date are required'}), 400
    status = body.get('status')
    if status is not None and status not in time_log.STATUSES:
        return jsonify({'success': False, 'error': 'Invalid status'}), 400
    entry = time_log.set_status(str(employee_id), entry_date, status, body.get('note'))
    return jsonify({'success': True, 'data': _time_entry_json(entry)})


@app.route('/api/time-entries/manual', methods=['POST'])
@_require_time_log_admin
def manual_check_in():
    """Admin creates a day's entry directly (no GPS involved) — e.g. an
    engineer's browser has location permanently denied and can't self check-in."""
    body = request.json or {}
    employee_id = body.get('employeeId')
    entry_date = body.get('date')
    if not employee_id or not entry_date:
        return jsonify({'success': False, 'error': 'employeeId and date are required'}), 400
    entry = time_log.manual_check_in(
        str(employee_id), entry_date, body.get('checkInAt'), body.get('checkOutAt'), body.get('note'))
    return jsonify({'success': True, 'data': _time_entry_json(entry)})


@app.route('/api/time-entries/<int:entry_id>', methods=['PUT'])
@_require_time_log_admin
def update_time_entry(entry_id):
    body = request.json or {}
    entry = time_log.update_entry(entry_id, body.get('checkInAt'), body.get('checkOutAt'), body.get('note'))
    if entry is None:
        return jsonify({'success': False, 'error': 'Time entry not found'}), 404
    return jsonify({'success': True, 'data': _time_entry_json(entry)})


@app.route('/api/time-entries/<int:entry_id>', methods=['DELETE'])
@_require_time_log_admin
def reset_time_entry(entry_id):
    """Admin reset: wipe a day's entry (check-in/out, status, note) entirely,
    e.g. the engineer tapped the wrong button and needs a clean slate."""
    ok = time_log.delete_entry(entry_id)
    if not ok:
        return jsonify({'success': False, 'error': 'Time entry not found'}), 404
    return jsonify({'success': True, 'message': 'Time entry reset'})


# ============================================================================
# LABOUR SITE LOG (engineers fill their labours' day: site + OT + present)
# ============================================================================

@app.route('/api/crew/<token>', methods=['GET'])
def get_crew_sheet(token):
    """The engineer's daily sheet: every labour, plus whatever is already
    logged for that date. Token-scoped, site engineers only."""
    engineer = labour_log.get_engineer_by_token(token)
    if engineer is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404

    # labour_log.today() is UAE-local, not the (UTC) server date — see the note
    # on UAE_TZ in labour_log_service.
    uae_today = labour_log.today()
    entry_date = labour_log.parse_date(request.args.get('date')) or uae_today
    if not labour_log.date_in_window(entry_date):
        return jsonify({'success': False, 'error':
                        'You can only fill today and the last %d days.'
                        % labour_log.EDIT_WINDOW_DAYS}), 400

    return jsonify({'success': True, 'data': {
        'engineerName': engineer['name'],
        'date': entry_date.isoformat(),
        'minDate': (uae_today - timedelta(days=labour_log.EDIT_WINDOW_DAYS)).isoformat(),
        'maxDate': uae_today.isoformat(),
        'sites': labour_log.list_sites(),
        'labours': labour_log.list_labours_for_date(entry_date, engineer['id']),
    }})


@app.route('/api/crew/<token>/save', methods=['POST'])
def save_crew_sheet(token):
    """Save the rows the engineer touched. Overwriting another engineer's entry
    for the same labour/day is allowed and flagged (see labour_log_service)."""
    engineer = labour_log.get_engineer_by_token(token)
    if engineer is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404

    body = request.json or {}
    entry_date = labour_log.parse_date(body.get('date'))
    if entry_date is None:
        return jsonify({'success': False, 'error': 'A valid date is required'}), 400
    if not labour_log.date_in_window(entry_date):
        return jsonify({'success': False, 'error':
                        'You can only fill today and the last %d days.'
                        % labour_log.EDIT_WINDOW_DAYS}), 400

    rows = body.get('rows') or []
    if not isinstance(rows, list):
        return jsonify({'success': False, 'error': 'rows must be a list'}), 400

    saved = labour_log.save_day(engineer['id'], entry_date, rows)
    return jsonify({'success': True, 'message': f'Saved {saved} labours', 'data': {
        'date': entry_date.isoformat(),
        'labours': labour_log.list_labours_for_date(entry_date, engineer['id']),
    }})


@app.route('/api/labour-log', methods=['GET'])
def get_labour_log_month():
    """Office view (/site-log): the month's sheet for every labour.
    `month` is 0-indexed on the wire, like attendance/month-end."""
    year = int(request.args.get('year', datetime.now().year))
    month = int(request.args.get('month', datetime.now().month - 1))
    data = labour_log.list_month(year, month + 1)
    return jsonify({'success': True, 'data': data})


@app.route('/api/labour-log/<int:entry_id>', methods=['PUT'])
def update_labour_log_entry(entry_id):
    """Office correction of one logged day (re-syncs attendance + month OT)."""
    body = request.json or {}
    entry = labour_log.update_entry(
        entry_id, status=body.get('status'), ot_hours=body.get('otHours'),
        sites=body.get('sites'), note=body.get('note'))
    if entry is None:
        return jsonify({'success': False, 'error': 'Log entry not found'}), 404
    return jsonify({'success': True, 'data': {
        'id': entry['id'],
        'employeeId': entry['employee_id'],
        'entryDate': entry['entry_date'].isoformat(),
        'status': entry['status'],
        'otHours': float(entry['ot_hours']),
        'sites': entry['sites'],
        'note': entry['note'],
        'conflict': bool(entry['conflict']),
    }})


@app.route('/api/employees/<employee_id>/crew-sheet', methods=['POST'])
def set_crew_sheet_access(employee_id):
    """Office grants/revokes access to the labour sheet for one person.
    Separate from time_log_enabled — see labour_log_service.get_engineer_by_token."""
    enabled = bool((request.json or {}).get('enabled'))
    result = labour_log.set_crew_access(employee_id, enabled)
    if result is None:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'data': result})


@app.route('/api/employees/<employee_id>/time-log', methods=['POST'])
def set_time_log_access(employee_id):
    """Office adds/removes one person's check-in link (time_log_enabled).
    Also puts them on / takes them off the Time Log roster and auto-absent run."""
    enabled = bool((request.json or {}).get('enabled'))
    result = time_log.set_time_log_access(employee_id, enabled)
    if result is None:
        return jsonify({'success': False, 'error': 'Employee not found'}), 404
    return jsonify({'success': True, 'data': result})


@app.route('/api/crew/<token>/entry', methods=['DELETE'])
def clear_crew_entry(token):
    """Engineer clears one labour he saved. Same day only, own entries only —
    see labour_log_service.clear_day."""
    engineer = labour_log.get_engineer_by_token(token)
    if engineer is None:
        return jsonify({'success': False, 'error': 'Link not recognized'}), 404

    body = request.json or {}
    employee_id = str(body.get('employeeId') or '').strip()
    entry_date = labour_log.parse_date(body.get('date'))
    if not employee_id or entry_date is None:
        return jsonify({'success': False, 'error': 'employeeId and a valid date are required'}), 400

    ok, err = labour_log.clear_day(engineer['id'], employee_id, entry_date)
    if not ok:
        return jsonify({'success': False, 'error': err}), 409
    return jsonify({'success': True, 'message': 'Entry cleared', 'data': {
        'date': entry_date.isoformat(),
        'labours': labour_log.list_labours_for_date(entry_date, engineer['id']),
    }})


@app.route('/api/labour-sites', methods=['GET'])
def get_labour_sites():
    """Project sites for the engineers' chips. Separate from the LPO
    generator's delivery sites (/api/lpo-gen/sites) on purpose."""
    return jsonify({'success': True, 'sites': labour_log.list_sites()})


@app.route('/api/labour-sites', methods=['POST'])
def add_labour_site():
    name = (request.json or {}).get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Site name required'}), 400
    return jsonify({'success': True, 'sites': labour_log.add_site(name)})


@app.route('/api/labour-sites', methods=['DELETE'])
def delete_labour_site():
    """Delete by name rather than index — an index would silently delete the
    wrong site if the list changed between load and click."""
    name = request.args.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Site name required'}), 400
    return jsonify({'success': True, 'sites': labour_log.delete_site(name)})


@app.route('/api/labour-log/<int:entry_id>', methods=['DELETE'])
def delete_labour_log_entry(entry_id):
    """Office: drop a logged day entirely (wrong man marked)."""
    if not labour_log.delete_entry(entry_id):
        return jsonify({'success': False, 'error': 'Log entry not found'}), 404
    return jsonify({'success': True, 'message': 'Log entry removed'})


# ============================================================================
# PAYROLL
# ============================================================================

@app.route('/api/payroll', methods=['POST'])
def write_payroll():
    body = request.json or {}
    month = body.get('month')
    year = body.get('year')
    emp_type = body.get('type')
    data = body.get('data', [])
    if month is None or not year or not emp_type:
        return jsonify({'success': False, 'error': 'Missing required fields: month, year, type, data'}), 400
    partial = bool(body.get('partial'))
    count, cash_count, tab_name, month_abbr = emp_service.write_payroll(month, year, emp_type, data, partial=partial)
    changed = 'changed ' if partial else ''
    msg = f'Saved {count} {changed}{emp_type} payroll records for {month_abbr} {year}'
    if cash_count:
        msg += f' ({cash_count} paid in cash)'
    return jsonify({'success': True, 'message': msg})


@app.route('/api/payroll/history', methods=['GET'])
def get_payroll_history():
    return jsonify({'success': True, 'data': emp_service.get_payroll_history()})


@app.route('/api/payroll', methods=['GET'])
def get_payroll():
    month = request.args.get('month')
    year = request.args.get('year')
    emp_type = request.args.get('type')
    if month is None or not year or not emp_type:
        return jsonify({'success': False, 'error': 'Missing required query params: month, year, type'}), 400
    data, msg = emp_service.get_payroll(month, year, emp_type)
    if msg:
        return jsonify({'success': True, 'data': data, 'message': msg})
    return jsonify({'success': True, 'data': data, 'savedFromSheet': True})


if __name__ == "__main__":
    port = int(os.getenv('PORT') or os.getenv('API_PORT', 5000))
    debug = os.getenv('FLASK_ENV') == 'development'

    print(f"Starting Purchase & Accounting API on port {port}")
    print(f"Debug mode: {debug}")

    # Time Log auto-absent: every 5 minutes, mark today's no-shows Absent once
    # it is past 9 AM UAE time. Idempotent, so the debug reloader's second
    # process running it too is harmless.
    def _auto_absent_loop():
        while True:
            try:
                marked = time_log.auto_mark_absent()
                if marked:
                    print(f"Auto-absent: marked {len(marked)} employee(s) Absent")
            except Exception as e:
                print(f"Auto-absent sweep failed: {e}")
            time.sleep(300)

    threading.Thread(target=_auto_absent_loop, daemon=True).start()

    # threaded=True: without it the dev server handles ONE request at a time —
    # a slow payroll save blocked every other request (hung history panel,
    # multi-second GETs, client timeouts).
    app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)

