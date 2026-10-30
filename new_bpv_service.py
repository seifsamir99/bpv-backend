"""
NewBPVService — unified BPV/PDC/CDC service backed by Supabase Postgres.

Tables (see schema_supabase.sql):
  bpv_line_items — one row per line item, grouped by bpv_no on read
  pdc_status     — cheque-level status tracking for PDCs
  cdc_status     — cheque-level status tracking for CDCs
  manual_pdc     — manually added PDC entries not derived from BPV

Public method names and return shapes are identical to the old Google-Sheets
implementation (preserved in legacy_sheets_backup/) — api.py and the frontend
depend on them.
"""

from datetime import datetime

import db


class NewBPVService:

    # -------------------------------------------------------------------------
    # BPV operations
    # -------------------------------------------------------------------------

    def get_all_vouchers(self):
        """Return all vouchers as a list of dicts, grouped by bpv_no."""
        rows = db.q("SELECT * FROM bpv_line_items ORDER BY id")
        return self._group_to_vouchers(rows)

    def get_voucher(self, bpv_no):
        """Return a single voucher by BPV number, or None."""
        rows = db.q("SELECT * FROM bpv_line_items WHERE trim(bpv_no) = %s ORDER BY id",
                    (str(bpv_no).strip(),))
        vouchers = self._group_to_vouchers(rows)
        return vouchers[0] if vouchers else None

    def get_next_bpv_number(self):
        """Return the next available BPV number (int)."""
        row = db.q_one(r"""SELECT COALESCE(MAX(trim(bpv_no)::int), 0) AS max_no
                           FROM bpv_line_items WHERE trim(bpv_no) ~ '^\d+$'""")
        return int(row['max_no']) + 1

    @staticmethod
    def _lock_bpv(cur, bpv_no):
        """Serialize all writers touching this bpv_no.

        Both create and update are DELETE-then-INSERT. Without serialization,
        two concurrent writes to the same voucher (e.g. an auto-save racing a
        manual save) can interleave as DELETE(A), DELETE(B), INSERT(A),
        INSERT(B) — leaving DUPLICATE line items. A transaction-scoped Postgres
        advisory lock keyed on the bpv_no forces them to run one-at-a-time, so
        the final DELETE+INSERT always wins cleanly. The lock auto-releases at
        commit/rollback, which is safe under Supabase's transaction pooler
        (never use session-scoped advisory locks there).
        """
        if bpv_no:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (bpv_no,))

    def create_voucher(self, data):
        """Create a new BPV voucher: one row per line item.

        Idempotent on bpv_no: if a voucher with this number already exists, its
        rows are replaced rather than appended. This prevents duplicate line
        items when a create is fired twice (double-submit) or when the UI mis-
        routes an edit to create.
        """
        bpv_no = str(data.get('bpvNo', '')).strip()

        def fn(conn):
            with conn.cursor() as cur:
                self._lock_bpv(cur, bpv_no)
                if bpv_no:
                    cur.execute("DELETE FROM bpv_line_items WHERE trim(bpv_no) = %s",
                                (bpv_no,))
                self._insert_line_items(conn, data)
        db.transaction(fn)
        return self._constructed_voucher(data)

    def update_voucher(self, bpv_no, data):
        """Replace an existing voucher's rows atomically."""
        data['bpvNo'] = bpv_no
        key = str(bpv_no).strip()

        def fn(conn):
            with conn.cursor() as cur:
                self._lock_bpv(cur, key)
                cur.execute("DELETE FROM bpv_line_items WHERE trim(bpv_no) = %s",
                            (key,))
                self._insert_line_items(conn, data)
        db.transaction(fn)
        return self._constructed_voucher(data)

    def delete_voucher(self, bpv_no):
        """Delete all rows for a given BPV number."""
        deleted = db.execute("DELETE FROM bpv_line_items WHERE trim(bpv_no) = %s",
                             (str(bpv_no).strip(),))
        if not deleted:
            return {'success': False, 'error': f'Voucher {bpv_no} not found'}
        return {'success': True}

    def calculate_total(self, line_items):
        """Sum debit fields across all line items."""
        total = 0
        for item in line_items:
            try:
                total += float(item.get('debit', 0) or 0)
            except (ValueError, TypeError):
                pass
        return total

    def _insert_line_items(self, conn, data):
        """Insert one bpv_line_items row per line item (within caller's transaction)."""
        bpv_no = str(data.get('bpvNo', '')).strip()
        date = data.get('date', '')
        pdc_type = data.get('pdcType', 'PDC')
        line_items = data.get('lineItems', [])
        if not line_items:
            line_items = [{'srNo': 1, 'description': '', 'companyName': '',
                           'chequeNo': '', 'chequeDate': '', 'debit': 0, 'credit': 0}]
            data['lineItems'] = line_items

        with conn.cursor() as cur:
            for item in line_items:
                cur.execute(
                    """INSERT INTO bpv_line_items
                       (bpv_no, date, type, sr_no, description, company,
                        cheque_no, cheque_date, debit, credit)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (bpv_no, date, pdc_type, str(item.get('srNo', '')),
                     item.get('description', ''), item.get('companyName', ''),
                     str(item.get('chequeNo', '')), item.get('chequeDate', ''),
                     _to_float(item.get('debit', 0)), _to_float(item.get('credit', 0))))

    def _constructed_voucher(self, data):
        bpv_no = str(data.get('bpvNo', '')).strip()
        return {
            'id': int(bpv_no) if bpv_no.isdigit() else bpv_no,
            'bpvNo': bpv_no,
            'date': data.get('date', ''),
            'pdcType': data.get('pdcType', 'PDC'),
            'lineItems': data.get('lineItems', []),
            'totalAmount': self.calculate_total(data.get('lineItems', [])),
            'hasData': True,
        }

    def _group_to_vouchers(self, rows):
        """Group flat line-item rows into voucher dicts keyed by bpv_no."""
        vouchers_map = {}
        order = []
        for row in rows:
            bpv_no = str(row.get('bpv_no', '')).strip()
            if not bpv_no:
                continue
            if bpv_no not in vouchers_map:
                order.append(bpv_no)
                vouchers_map[bpv_no] = {
                    'id': int(bpv_no) if bpv_no.isdigit() else bpv_no,
                    'bpvNo': bpv_no,
                    'date': row.get('date', ''),
                    'pdcType': row.get('type', 'PDC'),
                    'lineItems': [],
                    'totalAmount': 0,
                    'hasData': True,
                }
            item = {
                'srNo': row.get('sr_no', ''),
                'description': row.get('description', ''),
                'companyName': row.get('company', ''),
                'chequeNo': row.get('cheque_no', ''),
                'chequeDate': row.get('cheque_date', ''),
                'debit': _to_float(row.get('debit', 0)),
                'credit': _to_float(row.get('credit', 0)),
            }
            vouchers_map[bpv_no]['lineItems'].append(item)
            vouchers_map[bpv_no]['totalAmount'] += item['debit']

        return [vouchers_map[k] for k in order]

    # -------------------------------------------------------------------------
    # PDC status operations
    # -------------------------------------------------------------------------

    def get_pdc_statuses(self):
        """Return dict {cheque_no: status} for all PDC statuses."""
        rows = db.q("SELECT cheque_no, status FROM pdc_status")
        return {r['cheque_no']: r['status'] for r in rows if r['cheque_no']}

    def update_pdc_status(self, cheque_no, status):
        return self._upsert_status('pdc_status', cheque_no, status)

    def bulk_update_pdc_status(self, updates):
        """updates: list of {chequeNo, status} dicts."""
        return self._bulk_upsert_status('pdc_status', updates)

    # -------------------------------------------------------------------------
    # CDC status operations
    # -------------------------------------------------------------------------

    def get_cdc_statuses(self):
        """Return dict {cheque_no: status} for all CDC statuses."""
        rows = db.q("SELECT cheque_no, status FROM cdc_status")
        return {r['cheque_no']: r['status'] for r in rows if r['cheque_no']}

    def update_cdc_status(self, cheque_no, status):
        return self._upsert_status('cdc_status', cheque_no, status)

    def bulk_update_cdc_status(self, updates):
        return self._bulk_upsert_status('cdc_status', updates)

    def _upsert_status(self, table, cheque_no, status):
        # table is a hardcoded internal name, never user input
        row = db.q_one(
            f"""INSERT INTO {table} (cheque_no, status, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (cheque_no)
                DO UPDATE SET status = EXCLUDED.status, updated_at = now()
                RETURNING (xmax = 0) AS inserted""",
            (str(cheque_no).strip(), status))
        return {'success': True, 'action': 'created' if row['inserted'] else 'updated'}

    def _bulk_upsert_status(self, table, updates):
        def fn(conn):
            with conn.cursor() as cur:
                for u in updates:
                    cur.execute(
                        f"""INSERT INTO {table} (cheque_no, status, updated_at)
                            VALUES (%s, %s, now())
                            ON CONFLICT (cheque_no)
                            DO UPDATE SET status = EXCLUDED.status, updated_at = now()""",
                        (str(u['chequeNo']).strip(), u['status']))
        db.transaction(fn)
        return {'success': True, 'updated': len(updates)}

    # -------------------------------------------------------------------------
    # Manual PDC operations
    # -------------------------------------------------------------------------

    def get_manual_pdcs(self):
        """Return all manual PDC entries (rowIndex = 1-based position, used by
        the frontend for deletion)."""
        rows = db.q("SELECT * FROM manual_pdc ORDER BY id")
        records = []
        for pos, r in enumerate(rows, start=1):
            records.append({
                'id': r['id'],
                'company': r['company'],
                'chequeNo': r['cheque_no'],
                'chequeDate': r['cheque_date'],
                'amount': _to_float(r['amount']),
                'description': r['description'],
                'status': r['status'],
                'timestamp': r['created_at'].strftime('%Y-%m-%d %H:%M:%S') if r['created_at'] else '',
                'rowIndex': pos,
            })
        return records

    def add_manual_pdc(self, data):
        """Insert a new manual PDC entry."""
        row = db.q_one(
            """INSERT INTO manual_pdc (company, cheque_no, cheque_date, amount, description, status)
               VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
            (data.get('company', ''), str(data.get('chequeNo', '')),
             data.get('chequeDate', ''), _to_float(data.get('amount', 0)),
             data.get('description', ''), data.get('status', 'Not Released')))
        return {'success': True, 'id': row['id']}

    def delete_manual_pdc(self, row_index):
        """Delete a manual PDC by its 1-based position in id order (matches the
        old sheet-row semantics used by the DELETE /api/pdc/<row_index> route)."""
        db.execute(
            """DELETE FROM manual_pdc WHERE id =
               (SELECT id FROM manual_pdc ORDER BY id OFFSET %s LIMIT 1)""",
            (int(row_index) - 1,))
        return {'success': True}

    # -------------------------------------------------------------------------
    # Tax invoices (per-month log of received supplier tax invoices)
    # -------------------------------------------------------------------------

    def get_tax_invoices(self, month, year):
        """Return tax invoices for a month. month is 0-indexed at the API
        boundary (like attendance/payroll); stored 1-12."""
        rows = db.q(
            "SELECT * FROM tax_invoices WHERE year = %s AND month = %s ORDER BY id",
            (int(year), int(month) + 1))
        records = []
        for pos, r in enumerate(rows, start=1):
            records.append({
                'id': r['id'],
                'invoiceDate': r['invoice_date'],
                'company': r['company'],
                'amountNoVat': _to_float(r['amount_no_vat']),
                'vat': _to_float(r['vat']),
                'amountWithVat': _to_float(r['amount_with_vat']),
                'invoiceNumber': r['invoice_number'],
                'rowIndex': pos,
            })
        return records

    def add_tax_invoice(self, data):
        """Insert one tax invoice. month/year are derived from invoiceDate
        (MM/DD/YYYY) so a row always lands in the tab matching its date;
        falls back to the posted month (0-indexed) / year."""
        month, year = self._tax_invoice_period(data)
        row = db.q_one(
            """INSERT INTO tax_invoices
                 (invoice_date, year, month, company, amount_no_vat, vat,
                  amount_with_vat, invoice_number)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (data.get('invoiceDate', ''), year, month, data.get('company', ''),
             _to_float(data.get('amountNoVat', 0)), _to_float(data.get('vat', 0)),
             _to_float(data.get('amountWithVat', 0)), str(data.get('invoiceNumber', ''))))
        return {'success': True, 'id': row['id']}

    def update_tax_invoice(self, invoice_id, data):
        """Update a tax invoice by id; re-derive month/year from the date."""
        month, year = self._tax_invoice_period(data)
        db.execute(
            """UPDATE tax_invoices SET
                 invoice_date = %s, year = %s, month = %s, company = %s,
                 amount_no_vat = %s, vat = %s, amount_with_vat = %s,
                 invoice_number = %s
               WHERE id = %s""",
            (data.get('invoiceDate', ''), year, month, data.get('company', ''),
             _to_float(data.get('amountNoVat', 0)), _to_float(data.get('vat', 0)),
             _to_float(data.get('amountWithVat', 0)), str(data.get('invoiceNumber', '')),
             int(invoice_id)))
        return {'success': True}

    def delete_tax_invoice(self, invoice_id):
        """Delete a tax invoice by id."""
        db.execute("DELETE FROM tax_invoices WHERE id = %s", (int(invoice_id),))
        return {'success': True}

    @staticmethod
    def _tax_invoice_period(data):
        """(month 1-12, year) for a tax invoice: parse MM/DD/YYYY from
        invoiceDate; fall back to the posted 0-indexed month + year."""
        date_str = (data.get('invoiceDate') or '').strip()
        parts = date_str.split('/')
        if len(parts) == 3:
            try:
                first, second, year = int(parts[0]), int(parts[1]), int(parts[2])
                # MM/DD/YYYY new standard; first > 12 means legacy DD/MM/YYYY.
                month = second if first > 12 else first
                if 1 <= month <= 12:
                    return month, year
            except ValueError:
                pass
        return int(data.get('month', 0)) + 1, int(data.get('year', datetime.now().year))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0
