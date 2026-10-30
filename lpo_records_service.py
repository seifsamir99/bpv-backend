"""
LPO history: persists a full record of every generated LPO so it can be
searched, re-downloaded, edited, or deleted later. Mirrors NewBPVService's
pattern of storing structured data and regenerating PDFs on demand rather
than storing PDF bytes (see execution/new_bpv_service.py and how
GET /api/bpv/<bpv_no>/pdf rebuilds a voucher PDF from bpv_line_items).
"""
import json
import db as pgdb


def _num(value):
    """Optional numeric override → float, with 0.0 meaning "not set"."""
    try:
        return float(value) if value not in (None, '') else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mode(value):
    """Pricing layout the LPO was written with; anything unknown means the
    original Qty x Rate one (see pdf_generator.generate_lpo)."""
    value = str(value or 'unit').lower()
    return value if value in ('unit', 'total', 'lump') else 'unit'


def _is_text_qty(value):
    """True for a Qty typed as text, such as "Per Sqft", rather than a number."""
    if value is None or str(value).strip() == '':
        return False
    try:
        float(value)
        return False
    except (TypeError, ValueError):
        return True


def _compute_totals(line_items, discount, vat_percent, sub_override=None, total_override=None,
                    pricing_mode='unit'):
    """Mirror of the totals maths in pdf_generator.generate_lpo — keep the two
    in step, or the History list will show a figure the PDF doesn't."""
    discount = _num(discount)
    vat_percent = float(vat_percent or 0)
    sub_override, total_override = _num(sub_override), _num(total_override)
    if total_override:
        total = round(total_override, 2)
        net = round(total / (1 + vat_percent / 100), 2)
        return round(net + discount, 2), total
    # A lump-sum LPO carries no per-line money; its value is the override only.
    if _mode(pricing_mode) == 'lump':
        line_sum = 0
    else:
        # A text Qty ("Per Sqft") is a rate-only line with no Total in 'unit' mode.
        line_sum = sum(float(i.get('total_price', 0) or 0) for i in line_items
                       if not (_mode(pricing_mode) == 'unit' and _is_text_qty(i.get('quantity'))))
    subtotal = sub_override or line_sum
    net = subtotal - discount
    total = net + net * vat_percent / 100
    return round(subtotal, 2), round(total, 2)


def list_records(search='', date_from='', date_to=''):
    """Return summary rows for the history panel, newest first."""
    where = []
    params = []
    if search:
        where.append("(lpo_number ILIKE %s OR supplier_name ILIKE %s OR project ILIKE %s)")
        like = f"%{search}%"
        params += [like, like, like]
    if date_from:
        where.append("lpo_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("lpo_date <= %s")
        params.append(date_to)
    sql = """SELECT id, lpo_number, to_char(lpo_date, 'YYYY-MM-DD') AS lpo_date, project,
                     supplier_name, total_amount, created_at, updated_at
              FROM lpo_records"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    return pgdb.q(sql, tuple(params))


def get_record(record_id):
    """Return the full record (including line_items/extra_terms) or None.

    Date columns are cast to 'YYYY-MM-DD' text in SQL rather than left as
    Python date objects: Flask's default JSON encoder serializes bare
    date/datetime values via HTTP-date format (e.g. "Wed, 06 Aug 2026..."),
    which an <input type="date"> can't parse back for the Edit flow.
    """
    return pgdb.q_one(
        """SELECT id, lpo_number, to_char(lpo_date, 'YYYY-MM-DD') AS lpo_date, quote_ref, project,
                  supplier_name, supplier_contact, supplier_email, supplier_phone, supplier_address,
                  supplier_trn, delivery_site, delivery_contact, delivery_phone, payment_terms,
                  discount, vat_percent, pricing_mode, subtotal_override, total_override,
                  subtotal, total_amount, line_items, extra_terms, notes,
                  created_at, updated_at
           FROM lpo_records WHERE id = %s""",
        (record_id,))


def create_record(lpo_info, supplier, line_items):
    """Upsert the record after a successful PDF generation. Returns its id.

    ON CONFLICT rather than a plain INSERT: lpo_number is unique, and api.py calls
    this inside a non-fatal try/except (a history failure must never block the PDF).
    A plain INSERT would therefore make regenerating an existing LPO number fail
    SILENTLY, leaving a stale row behind. Upserting keeps the stored record equal
    to the last PDF actually generated under that number.
    """
    subtotal, total_amount = _compute_totals(
        line_items, lpo_info.get('discount', 0), lpo_info.get('vat_percent', 5),
        lpo_info.get('subtotal_override'), lpo_info.get('total_override'),
        lpo_info.get('pricing_mode'))
    row = pgdb.q_one(
        """INSERT INTO lpo_records (
             lpo_number, lpo_date, quote_ref, project,
             supplier_name, supplier_contact, supplier_email, supplier_phone,
             supplier_address, supplier_trn,
             delivery_site, delivery_contact, delivery_phone, payment_terms,
             discount, vat_percent, pricing_mode, subtotal_override, total_override,
             subtotal, total_amount,
             line_items, extra_terms, notes
           ) VALUES (
             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
           )
           ON CONFLICT (lpo_number) DO UPDATE SET
             lpo_date = EXCLUDED.lpo_date, quote_ref = EXCLUDED.quote_ref,
             project = EXCLUDED.project, supplier_name = EXCLUDED.supplier_name,
             supplier_contact = EXCLUDED.supplier_contact, supplier_email = EXCLUDED.supplier_email,
             supplier_phone = EXCLUDED.supplier_phone, supplier_address = EXCLUDED.supplier_address,
             supplier_trn = EXCLUDED.supplier_trn, delivery_site = EXCLUDED.delivery_site,
             delivery_contact = EXCLUDED.delivery_contact, delivery_phone = EXCLUDED.delivery_phone,
             payment_terms = EXCLUDED.payment_terms, discount = EXCLUDED.discount,
             vat_percent = EXCLUDED.vat_percent, pricing_mode = EXCLUDED.pricing_mode,
             subtotal_override = EXCLUDED.subtotal_override,
             total_override = EXCLUDED.total_override, subtotal = EXCLUDED.subtotal,
             total_amount = EXCLUDED.total_amount, line_items = EXCLUDED.line_items,
             extra_terms = EXCLUDED.extra_terms, notes = EXCLUDED.notes,
             updated_at = now()
           RETURNING id""",
        (lpo_info.get('lpo_id', ''), lpo_info.get('lpo_date') or None, lpo_info.get('quote_ref', ''),
         lpo_info.get('project', ''), supplier.get('name', ''), supplier.get('contact', ''),
         supplier.get('email', ''), supplier.get('phone', ''), supplier.get('address', ''),
         supplier.get('trn', ''), lpo_info.get('delivery_site', ''), lpo_info.get('delivery_contact', ''),
         lpo_info.get('delivery_phone', ''), lpo_info.get('payment_terms', ''),
         float(lpo_info.get('discount', 0) or 0), float(lpo_info.get('vat_percent', 5) or 5),
         _mode(lpo_info.get('pricing_mode')),
         _num(lpo_info.get('subtotal_override')) or None, _num(lpo_info.get('total_override')) or None,
         subtotal, total_amount, json.dumps(line_items), json.dumps(lpo_info.get('extra_terms', []) or []),
         lpo_info.get('notes', '')))
    return row['id']


def update_record(record_id, lpo_info, supplier, line_items):
    """Overwrite an existing record's data (used by the Edit/Save Changes flow)."""
    subtotal, total_amount = _compute_totals(
        line_items, lpo_info.get('discount', 0), lpo_info.get('vat_percent', 5),
        lpo_info.get('subtotal_override'), lpo_info.get('total_override'),
        lpo_info.get('pricing_mode'))
    pgdb.execute(
        """UPDATE lpo_records SET
             lpo_date = %s, quote_ref = %s, project = %s,
             supplier_name = %s, supplier_contact = %s, supplier_email = %s, supplier_phone = %s,
             supplier_address = %s, supplier_trn = %s,
             delivery_site = %s, delivery_contact = %s, delivery_phone = %s, payment_terms = %s,
             discount = %s, vat_percent = %s, pricing_mode = %s,
             subtotal_override = %s, total_override = %s,
             subtotal = %s, total_amount = %s,
             line_items = %s, extra_terms = %s, notes = %s, updated_at = now()
           WHERE id = %s""",
        (lpo_info.get('lpo_date') or None, lpo_info.get('quote_ref', ''), lpo_info.get('project', ''),
         supplier.get('name', ''), supplier.get('contact', ''), supplier.get('email', ''),
         supplier.get('phone', ''), supplier.get('address', ''), supplier.get('trn', ''),
         lpo_info.get('delivery_site', ''), lpo_info.get('delivery_contact', ''),
         lpo_info.get('delivery_phone', ''), lpo_info.get('payment_terms', ''),
         float(lpo_info.get('discount', 0) or 0), float(lpo_info.get('vat_percent', 5) or 5),
         _mode(lpo_info.get('pricing_mode')),
         _num(lpo_info.get('subtotal_override')) or None, _num(lpo_info.get('total_override')) or None,
         subtotal, total_amount, json.dumps(line_items), json.dumps(lpo_info.get('extra_terms', []) or []),
         lpo_info.get('notes', ''), record_id))


def delete_record(record_id):
    """Delete a record; return True if a row was actually removed."""
    return pgdb.execute("DELETE FROM lpo_records WHERE id = %s", (record_id,)) > 0
