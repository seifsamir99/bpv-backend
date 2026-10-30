"""
Job Order history: persists a full record of every generated Job Order so it
can be searched, re-downloaded, edited, or deleted later. Mirrors
lpo_records_service.py's pattern (itself modeled on NewBPVService) of storing
structured data and regenerating PDFs on demand rather than storing PDF bytes.
"""
import json
import db as pgdb


def _compute_totals(line_items, discount, vat_enabled, vat_percent):
    subtotal = sum(float(i.get('amount', 0) or 0) for i in line_items)
    net = subtotal - float(discount or 0)
    vat_amount = (net * float(vat_percent or 0) / 100) if vat_enabled else 0
    total = net + vat_amount
    return round(subtotal, 2), round(total, 2)


def list_records(search='', date_from='', date_to=''):
    """Return summary rows for the history panel, newest first."""
    where = []
    params = []
    if search:
        where.append("(ref_number ILIKE %s OR subcontractor_name ILIKE %s OR project ILIKE %s)")
        like = f"%{search}%"
        params += [like, like, like]
    if date_from:
        where.append("jo_date >= %s")
        params.append(date_from)
    if date_to:
        where.append("jo_date <= %s")
        params.append(date_to)
    sql = """SELECT id, ref_number, to_char(jo_date, 'YYYY-MM-DD') AS jo_date, project,
                     subcontractor_name, total_amount, show_totals, created_at, updated_at
              FROM job_order_records"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    return pgdb.q(sql, tuple(params))


def get_record(record_id):
    """Return the full record (including line_items/terms_text) or None.

    Date columns are cast to 'YYYY-MM-DD' text in SQL rather than left as
    Python date objects: Flask's default JSON encoder serializes bare
    date/datetime values via HTTP-date format (e.g. "Wed, 06 Aug 2026..."),
    which an <input type="date"> can't parse back for the Edit flow.
    """
    return pgdb.q_one(
        """SELECT id, ref_number, project_code, to_char(jo_date, 'YYYY-MM-DD') AS jo_date,
                  project, sub_scope, to_char(quote_date, 'YYYY-MM-DD') AS quote_date,
                  subcontractor_name, subcontractor_contact, subcontractor_phone, subcontractor_address,
                  has_unit_pricing, discount, vat_enabled, vat_percent, show_totals,
                  subtotal, total_amount, line_items, terms_text, created_at, updated_at
           FROM job_order_records WHERE id = %s""",
        (record_id,))


def create_record(job_order_info, subcontractor, line_items, terms_text):
    """Upsert the record after a successful PDF generation. Returns its id.

    ON CONFLICT rather than a plain INSERT, for the same reason as
    lpo_records_service.create_record: ref_number is unique and the caller
    swallows errors, so a plain INSERT would silently drop a regenerated
    Job Order instead of refreshing the stored one.
    """
    vat_enabled = bool(job_order_info.get('vat_enabled', True))
    show_totals = bool(job_order_info.get('show_totals', True))
    subtotal, total_amount = _compute_totals(
        line_items, job_order_info.get('discount', 0), vat_enabled,
        job_order_info.get('vat_percent', 5))
    row = pgdb.q_one(
        """INSERT INTO job_order_records (
             ref_number, project_code, jo_date, project, sub_scope, quote_date,
             subcontractor_name, subcontractor_contact, subcontractor_phone, subcontractor_address,
             has_unit_pricing, discount, vat_enabled, vat_percent, show_totals,
             subtotal, total_amount, line_items, terms_text
           ) VALUES (
             %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
           )
           ON CONFLICT (ref_number) DO UPDATE SET
             project_code = EXCLUDED.project_code, jo_date = EXCLUDED.jo_date,
             project = EXCLUDED.project, sub_scope = EXCLUDED.sub_scope,
             quote_date = EXCLUDED.quote_date,
             subcontractor_name = EXCLUDED.subcontractor_name,
             subcontractor_contact = EXCLUDED.subcontractor_contact,
             subcontractor_phone = EXCLUDED.subcontractor_phone,
             subcontractor_address = EXCLUDED.subcontractor_address,
             has_unit_pricing = EXCLUDED.has_unit_pricing, discount = EXCLUDED.discount,
             vat_enabled = EXCLUDED.vat_enabled, vat_percent = EXCLUDED.vat_percent,
             show_totals = EXCLUDED.show_totals, subtotal = EXCLUDED.subtotal, total_amount = EXCLUDED.total_amount,
             line_items = EXCLUDED.line_items, terms_text = EXCLUDED.terms_text,
             updated_at = now()
           RETURNING id""",
        (job_order_info.get('ref_number', ''), job_order_info.get('project_code', ''),
         job_order_info.get('date') or None, job_order_info.get('project', ''),
         job_order_info.get('sub', ''), job_order_info.get('quote_date') or None,
         subcontractor.get('name', ''), subcontractor.get('contact', ''),
         subcontractor.get('phone', ''), subcontractor.get('address', ''),
         bool(job_order_info.get('has_unit_pricing', False)),
         float(job_order_info.get('discount', 0) or 0), vat_enabled,
         float(job_order_info.get('vat_percent', 5) or 5), show_totals,
         subtotal, total_amount, json.dumps(line_items), terms_text))
    return row['id']


def update_record(record_id, job_order_info, subcontractor, line_items, terms_text):
    """Overwrite an existing record's data (used by the Edit/Save Changes flow)."""
    vat_enabled = bool(job_order_info.get('vat_enabled', True))
    show_totals = bool(job_order_info.get('show_totals', True))
    subtotal, total_amount = _compute_totals(
        line_items, job_order_info.get('discount', 0), vat_enabled,
        job_order_info.get('vat_percent', 5))
    pgdb.execute(
        """UPDATE job_order_records SET
             project_code = %s, jo_date = %s, project = %s, sub_scope = %s, quote_date = %s,
             subcontractor_name = %s, subcontractor_contact = %s, subcontractor_phone = %s,
             subcontractor_address = %s, has_unit_pricing = %s, discount = %s, vat_enabled = %s,
             vat_percent = %s, show_totals = %s, subtotal = %s, total_amount = %s,
             line_items = %s, terms_text = %s,
             updated_at = now()
           WHERE id = %s""",
        (job_order_info.get('project_code', ''), job_order_info.get('date') or None,
         job_order_info.get('project', ''), job_order_info.get('sub', ''),
         job_order_info.get('quote_date') or None,
         subcontractor.get('name', ''), subcontractor.get('contact', ''),
         subcontractor.get('phone', ''), subcontractor.get('address', ''),
         bool(job_order_info.get('has_unit_pricing', False)),
         float(job_order_info.get('discount', 0) or 0), vat_enabled,
         float(job_order_info.get('vat_percent', 5) or 5), show_totals,
         subtotal, total_amount, json.dumps(line_items), terms_text, record_id))


def delete_record(record_id):
    """Delete a record; return True if a row was actually removed."""
    return pgdb.execute("DELETE FROM job_order_records WHERE id = %s", (record_id,)) > 0
