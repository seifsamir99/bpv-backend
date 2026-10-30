"""
Supplier ledger (accounts payable) -- the /supplier-ledger page.

What Newell owes its suppliers, per invoice, with payments allocated against
invoices and ageing like the suppliers' own "Statement of Account
(Outstanding)" (the Touch Home statement of 28/09/2026 was the reference).

Tables (migrate_supplier_ledger.py): suppliers, supplier_invoices,
supplier_payments, supplier_payment_allocations.

Nothing derived is stored. An invoice's balance is amount - credit - the sum
of its allocations; a supplier's outstanding is the sum of its invoice
balances minus any payment money not yet allocated to an invoice (paid "on
account"). Keeping balances derived means deleting a payment restores the
invoices exactly, with no counters to drift.

Ageing is by INVOICE date (as on the supplier statements), buckets 0-30,
31-60, 61-90, 91-120, 121+. Overdue is by DUE date.

This ledger is deliberately standalone -- it does not read BPV or tax_invoices.
Their company names are free text and do not reconcile with a supplier's own
statement; a payment can carry a bpv_no as a reference instead.
"""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from psycopg2.extras import RealDictCursor, execute_values

import db

UAE_TZ = timezone(timedelta(hours=4))
TWOPLACES = Decimal('0.01')
DUE_SOON_DAYS = 7
BUCKETS = (('d0_30', 0, 30), ('d31_60', 31, 60), ('d61_90', 61, 90),
           ('d91_120', 91, 120), ('d121_plus', 121, None))

# Namespace for pg_advisory_xact_lock(key1, supplier_id), so payment writes for
# one supplier serialize (two users allocating the same invoice at once must
# not both pass the open-balance check).
_LOCK_NS = 7301


class LedgerError(Exception):
    """A refusal the user needs to see verbatim (over-allocation, bad date).
    api.py returns the message as-is rather than through safe_error()."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def today_uae():
    return datetime.now(UAE_TZ).date()


def _money(v):
    if v is None or v == '':
        return Decimal('0.00')
    try:
        return Decimal(str(v)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        raise LedgerError(f"Invalid amount: {v}")


def _f(d):
    return float(Decimal(d).quantize(TWOPLACES, rounding=ROUND_HALF_UP))


def _date(v, field='date'):
    if isinstance(v, date):
        return v
    s = (v or '').strip()
    if not s:
        raise LedgerError(f"{field} is required")
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        raise LedgerError(f"Invalid {field}: {s} (expected YYYY-MM-DD)")


def _as_of(v):
    return _date(v, 'as-of date') if v else today_uae()


def _empty_buckets():
    return {k: Decimal('0.00') for k, _, _ in BUCKETS}


def _bucket_key(age_days):
    for key, lo, hi in BUCKETS:
        if age_days >= lo and (hi is None or age_days <= hi):
            return key
    return BUCKETS[0][0]  # future-dated invoice -> current bucket


def _invoice_rows(supplier_id=None):
    sql = """SELECT i.*, COALESCE(a.paid, 0) AS paid
               FROM supplier_invoices i
               LEFT JOIN (SELECT invoice_id, SUM(amount) AS paid
                            FROM supplier_payment_allocations GROUP BY invoice_id) a
                      ON a.invoice_id = i.id"""
    if supplier_id is not None:
        return db.q(sql + " WHERE i.supplier_id = %s ORDER BY i.invoice_date, i.id",
                    (int(supplier_id),))
    return db.q(sql + " ORDER BY i.invoice_date, i.id")


def _payment_rows(supplier_id=None):
    sql = """SELECT p.*, COALESCE(a.allocated, 0) AS allocated
               FROM supplier_payments p
               LEFT JOIN (SELECT payment_id, SUM(amount) AS allocated
                            FROM supplier_payment_allocations GROUP BY payment_id) a
                      ON a.payment_id = p.id"""
    if supplier_id is not None:
        return db.q(sql + " WHERE p.supplier_id = %s ORDER BY p.payment_date DESC, p.id DESC",
                    (int(supplier_id),))
    return db.q(sql + " ORDER BY p.payment_date DESC, p.id DESC")


def _invoice_json(r, as_of):
    amount, credit, paid = _money(r['amount']), _money(r['credit']), _money(r['paid'])
    balance = amount - credit - paid
    due = r['due_date']
    days_overdue = (as_of - due).days if balance > 0 else 0
    if balance <= 0:
        status = 'Paid'
    elif paid > 0:
        status = 'Partial'
    else:
        status = 'Open'
    return {
        'id': r['id'],
        'supplierId': r['supplier_id'],
        'invoiceDate': r['invoice_date'].isoformat(),
        'dueDate': due.isoformat(),
        'reference': r['reference'],
        'description': r['description'],
        'amount': _f(amount),
        'credit': _f(credit),
        'paid': _f(paid),
        'balance': _f(balance),
        'status': status,
        'ageDays': (as_of - r['invoice_date']).days,
        'daysOverdue': max(days_overdue, 0),
        'overdue': balance > 0 and due < as_of,
        'dueSoon': balance > 0 and as_of <= due <= as_of + timedelta(days=DUE_SOON_DAYS),
        'notes': r['notes'],
    }


def _supplier_json(r):
    return {
        'id': r['id'],
        'name': r['name'],
        'code': r['code'],
        'paymentTermsDays': r['payment_terms_days'],
        'phone': r['phone'],
        'trn': r['trn'],
        'notes': r['notes'],
    }


def _aggregate(invoices_raw, payments_raw, as_of):
    """Totals + ageing for one supplier's raw invoice/payment rows."""
    buckets = _empty_buckets()
    t = {'invoiced': Decimal('0'), 'credit': Decimal('0'), 'paidAllocated': Decimal('0'),
         'openInvoices': Decimal('0'), 'overdue': Decimal('0'), 'dueSoon': Decimal('0')}
    open_count = overdue_count = 0
    next_due = None
    for r in invoices_raw:
        amount, credit, paid = _money(r['amount']), _money(r['credit']), _money(r['paid'])
        bal = amount - credit - paid
        t['invoiced'] += amount
        t['credit'] += credit
        t['paidAllocated'] += paid
        if bal <= 0:
            continue
        open_count += 1
        t['openInvoices'] += bal
        buckets[_bucket_key((as_of - r['invoice_date']).days)] += bal
        due = r['due_date']
        if due < as_of:
            t['overdue'] += bal
            overdue_count += 1
        elif due <= as_of + timedelta(days=DUE_SOON_DAYS):
            t['dueSoon'] += bal
        if due >= as_of and (next_due is None or due < next_due):
            next_due = due
    paid_total = sum((_money(p['amount']) for p in payments_raw), Decimal('0'))
    unallocated = sum((_money(p['amount']) - _money(p['allocated']) for p in payments_raw),
                      Decimal('0'))
    outstanding = t['openInvoices'] - unallocated
    last_payment = max((p['payment_date'] for p in payments_raw), default=None)
    return {
        'invoiced': _f(t['invoiced']),
        'credit': _f(t['credit']),
        'paid': _f(paid_total),
        'unallocated': _f(unallocated),
        'outstanding': _f(outstanding),
        'overdue': _f(t['overdue']),
        'dueSoon': _f(t['dueSoon']),
        'openCount': open_count,
        'overdueCount': overdue_count,
        'nextDue': next_due.isoformat() if next_due else None,
        'lastPayment': last_payment.isoformat() if last_payment else None,
        'ageing': {k: _f(v) for k, v in buckets.items()},
    }


# ---------------------------------------------------------------------------
# Summary + statement
# ---------------------------------------------------------------------------

def summary(as_of=None):
    as_of = _as_of(as_of)
    suppliers = db.q("SELECT * FROM suppliers ORDER BY name")
    inv_by, pay_by = {}, {}
    for r in _invoice_rows():
        inv_by.setdefault(r['supplier_id'], []).append(r)
    for p in _payment_rows():
        pay_by.setdefault(p['supplier_id'], []).append(p)

    rows = []
    for s in suppliers:
        agg = _aggregate(inv_by.get(s['id'], []), pay_by.get(s['id'], []), as_of)
        rows.append({**_supplier_json(s), **agg})
    rows.sort(key=lambda r: -r['outstanding'])

    keys = ('invoiced', 'paid', 'outstanding', 'overdue', 'dueSoon', 'unallocated')
    totals = {k: _f(sum((Decimal(str(r[k])) for r in rows), Decimal('0'))) for k in keys}
    totals['ageing'] = {k: _f(sum((Decimal(str(r['ageing'][k])) for r in rows), Decimal('0')))
                        for k, _, _ in BUCKETS}
    totals['openCount'] = sum(r['openCount'] for r in rows)
    totals['overdueCount'] = sum(r['overdueCount'] for r in rows)
    return {'asOf': as_of.isoformat(), 'suppliers': rows, 'totals': totals}


def statement(supplier_id, as_of=None):
    as_of = _as_of(as_of)
    s = db.q_one("SELECT * FROM suppliers WHERE id = %s", (int(supplier_id),))
    if not s:
        return None
    inv_raw = _invoice_rows(supplier_id)
    pay_raw = _payment_rows(supplier_id)
    allocs = db.q(
        """SELECT a.payment_id, a.invoice_id, a.amount, i.reference
             FROM supplier_payment_allocations a
             JOIN supplier_invoices i ON i.id = a.invoice_id
            WHERE i.supplier_id = %s ORDER BY a.id""", (int(supplier_id),))
    alloc_by = {}
    for a in allocs:
        alloc_by.setdefault(a['payment_id'], []).append({
            'invoiceId': a['invoice_id'], 'reference': a['reference'], 'amount': _f(_money(a['amount']))})
    payments = [{
        'id': p['id'],
        'paymentDate': p['payment_date'].isoformat(),
        'amount': _f(_money(p['amount'])),
        'allocated': _f(_money(p['allocated'])),
        'unallocated': _f(_money(p['amount']) - _money(p['allocated'])),
        'method': p['method'],
        'chequeNo': p['cheque_no'],
        'bpvNo': p['bpv_no'],
        'notes': p['notes'],
        'allocations': alloc_by.get(p['id'], []),
    } for p in pay_raw]
    return {
        'asOf': as_of.isoformat(),
        'supplier': _supplier_json(s),
        'totals': _aggregate(inv_raw, pay_raw, as_of),
        'invoices': [_invoice_json(r, as_of) for r in inv_raw],
        'payments': payments,
    }


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

def _supplier_params(data):
    name = (data.get('name') or '').strip()
    if not name:
        raise LedgerError("Supplier name is required")
    try:
        terms = int(data.get('paymentTermsDays') or 0)
    except (TypeError, ValueError):
        raise LedgerError("Payment terms must be a whole number of days")
    return (name, (data.get('code') or '').strip(), terms,
            (data.get('phone') or '').strip(), (data.get('trn') or '').strip(),
            (data.get('notes') or '').strip())


def create_supplier(data):
    params = _supplier_params(data)
    if db.q_one("SELECT id FROM suppliers WHERE lower(name) = lower(%s)", (params[0],)):
        raise LedgerError(f"Supplier '{params[0]}' already exists")
    row = db.q_one(
        """INSERT INTO suppliers (name, code, payment_terms_days, phone, trn, notes)
           VALUES (%s,%s,%s,%s,%s,%s) RETURNING *""", params)
    return _supplier_json(row)


def update_supplier(supplier_id, data):
    params = _supplier_params(data)
    dup = db.q_one("SELECT id FROM suppliers WHERE lower(name) = lower(%s) AND id <> %s",
                   (params[0], int(supplier_id)))
    if dup:
        raise LedgerError(f"Supplier '{params[0]}' already exists")
    row = db.q_one(
        """UPDATE suppliers SET name=%s, code=%s, payment_terms_days=%s, phone=%s, trn=%s, notes=%s
            WHERE id=%s RETURNING *""", params + (int(supplier_id),))
    if not row:
        raise LedgerError("Supplier not found")
    return _supplier_json(row)


def delete_supplier(supplier_id):
    n = db.q_one(
        """SELECT (SELECT count(*) FROM supplier_invoices WHERE supplier_id=%s) AS inv,
                  (SELECT count(*) FROM supplier_payments WHERE supplier_id=%s) AS pay""",
        (int(supplier_id), int(supplier_id)))
    if n['inv'] or n['pay']:
        raise LedgerError(
            f"Supplier has {n['inv']} invoice(s) and {n['pay']} payment(s) -- delete those first")
    db.execute("DELETE FROM suppliers WHERE id = %s", (int(supplier_id),))
    return {'success': True}


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

def _invoice_params(data, terms_days):
    inv_date = _date(data.get('invoiceDate'), 'invoice date')
    due = _date(data.get('dueDate'), 'due date') if data.get('dueDate') else \
        inv_date + timedelta(days=terms_days)
    amount = _money(data.get('amount'))
    credit = _money(data.get('credit'))
    if amount <= 0:
        raise LedgerError("Invoice amount must be greater than zero")
    if credit < 0 or credit > amount:
        raise LedgerError("Credit must be between 0 and the invoice amount")
    ref = str(data.get('reference') or '').strip()
    if not ref:
        raise LedgerError("Invoice reference is required")
    return (inv_date, due, ref, (data.get('description') or 'SALES - CREDIT').strip(),
            amount, credit, (data.get('notes') or '').strip())


def add_invoices(supplier_id, items):
    """Insert one or many invoices for a supplier in one statement.
    Duplicate references (per supplier) are refused, not silently skipped."""
    s = db.q_one("SELECT * FROM suppliers WHERE id = %s", (int(supplier_id),))
    if not s:
        raise LedgerError("Supplier not found")
    rows = [(int(supplier_id),) + _invoice_params(d, s['payment_terms_days']) for d in items]
    if not rows:
        raise LedgerError("No invoices to add")
    refs = [r[3] for r in rows]
    if len(set(refs)) != len(refs):
        raise LedgerError("The same invoice reference appears twice")
    existing = db.q("SELECT reference FROM supplier_invoices WHERE supplier_id=%s AND reference = ANY(%s)",
                    (int(supplier_id), refs))
    if existing:
        raise LedgerError("Invoice already exists: " + ", ".join(e['reference'] for e in existing))

    def fn(conn):
        with conn.cursor() as cur:
            execute_values(cur,
                """INSERT INTO supplier_invoices
                     (supplier_id, invoice_date, due_date, reference, description, amount, credit, notes)
                   VALUES %s""", rows)
    db.transaction(fn)
    return {'success': True, 'added': len(rows)}


def update_invoice(invoice_id, data):
    cur_row = db.q_one(
        """SELECT i.*, s.payment_terms_days,
                  COALESCE((SELECT SUM(amount) FROM supplier_payment_allocations
                             WHERE invoice_id = i.id), 0) AS paid
             FROM supplier_invoices i JOIN suppliers s ON s.id = i.supplier_id
            WHERE i.id = %s""", (int(invoice_id),))
    if not cur_row:
        raise LedgerError("Invoice not found")
    params = _invoice_params(data, cur_row['payment_terms_days'])
    amount, credit = params[4], params[5]
    if amount - credit < _money(cur_row['paid']):
        raise LedgerError(
            f"Payments of {_f(_money(cur_row['paid'])):,.2f} are already allocated to this invoice; "
            "the amount less credit can't go below that")
    dup = db.q_one("SELECT id FROM supplier_invoices WHERE supplier_id=%s AND reference=%s AND id<>%s",
                   (cur_row['supplier_id'], params[2], int(invoice_id)))
    if dup:
        raise LedgerError(f"Invoice {params[2]} already exists for this supplier")
    db.execute(
        """UPDATE supplier_invoices SET invoice_date=%s, due_date=%s, reference=%s, description=%s,
                  amount=%s, credit=%s, notes=%s WHERE id=%s""", params + (int(invoice_id),))
    return {'success': True}


def delete_invoice(invoice_id):
    paid = db.q_one("SELECT count(*) AS n FROM supplier_payment_allocations WHERE invoice_id=%s",
                    (int(invoice_id),))
    if paid['n']:
        raise LedgerError("This invoice has payments allocated to it -- delete those payments first")
    db.execute("DELETE FROM supplier_invoices WHERE id = %s", (int(invoice_id),))
    return {'success': True}


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------

def add_payment(data):
    supplier_id = int(data.get('supplierId') or 0)
    pay_date = _date(data.get('paymentDate'), 'payment date')
    amount = _money(data.get('amount'))
    if amount <= 0:
        raise LedgerError("Payment amount must be greater than zero")
    allocs = []
    for a in data.get('allocations') or []:
        amt = _money(a.get('amount'))
        if amt < 0:
            raise LedgerError("Allocation amounts can't be negative")
        if amt > 0:
            allocs.append((int(a.get('invoiceId')), amt))
    if len({i for i, _ in allocs}) != len(allocs):
        raise LedgerError("The same invoice is allocated twice")
    total_alloc = sum((amt for _, amt in allocs), Decimal('0'))
    if total_alloc > amount:
        raise LedgerError(
            f"Allocated {_f(total_alloc):,.2f} is more than the payment of {_f(amount):,.2f}")

    def fn(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT id FROM suppliers WHERE id = %s", (supplier_id,))
            if not cur.fetchone():
                raise LedgerError("Supplier not found")
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NS, supplier_id))
            if allocs:
                cur.execute(
                    """SELECT i.id, i.reference, i.supplier_id, i.amount - i.credit
                              - COALESCE((SELECT SUM(amount) FROM supplier_payment_allocations
                                           WHERE invoice_id = i.id), 0) AS open
                         FROM supplier_invoices i WHERE i.id = ANY(%s)""",
                    ([i for i, _ in allocs],))
                open_by = {r['id']: r for r in cur.fetchall()}
                for inv_id, amt in allocs:
                    inv = open_by.get(inv_id)
                    if not inv or inv['supplier_id'] != supplier_id:
                        raise LedgerError("An allocated invoice does not belong to this supplier")
                    if amt > _money(inv['open']):
                        raise LedgerError(
                            f"Invoice {inv['reference']} only has {_f(_money(inv['open'])):,.2f} open")
            cur.execute(
                """INSERT INTO supplier_payments
                     (supplier_id, payment_date, amount, method, cheque_no, bpv_no, notes)
                   VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (supplier_id, pay_date, amount, (data.get('method') or '').strip(),
                 (data.get('chequeNo') or '').strip(), str(data.get('bpvNo') or '').strip(),
                 (data.get('notes') or '').strip()))
            pid = cur.fetchone()['id']
            if allocs:
                execute_values(cur,
                    "INSERT INTO supplier_payment_allocations (payment_id, invoice_id, amount) VALUES %s",
                    [(pid, i, amt) for i, amt in allocs])
            return pid
    pid = db.transaction(fn)
    return {'success': True, 'id': pid}


def delete_payment(payment_id):
    """Delete a payment; its allocations cascade, re-opening the invoices."""
    db.execute("DELETE FROM supplier_payments WHERE id = %s", (int(payment_id),))
    return {'success': True}


# ---------------------------------------------------------------------------
# Statement import (the page's "Import Statement" button; rows come from
# statement_extraction.py and are reviewed by the user first)
# ---------------------------------------------------------------------------

_NAME_NOISE = {'llc', 'co', 'company', 'the', 'est', 'fze', 'fzco', 'fzc'}


def _norm_name(name):
    """'LEMINAR GULF TRADING CO.(L.L.C)' -> 'leminar gulf trading'."""
    cleaned = ''.join(c if c.isalnum() else ' ' for c in (name or '').lower().replace('l.l.c', 'llc'))
    return ' '.join(w for w in cleaned.split() if w not in _NAME_NOISE)


def match_supplier(name):
    """Existing supplier whose normalised name equals (or contains) this one."""
    target = _norm_name(name)
    if not target:
        return None
    rows = db.q("SELECT * FROM suppliers")
    for r in rows:
        if _norm_name(r['name']) == target:
            return _supplier_json(r)
    for r in rows:
        n = _norm_name(r['name'])
        if n and (n in target or target in n):
            return _supplier_json(r)
    return None


def supplier_references(supplier_id):
    """Every invoice reference already in the ledger for a supplier, with its
    open balance -- the import review marks duplicates and lists open invoices
    missing from the new statement (usually paid since)."""
    rows = _invoice_rows(supplier_id)
    return [{'id': r['id'], 'reference': r['reference'], 'invoiceDate': r['invoice_date'].isoformat(),
             'balance': _f(_money(r['amount']) - _money(r['credit']) - _money(r['paid']))} for r in rows]


def import_statement(data):
    """Add a reviewed statement's rows to the ledger in one transaction.

    - supplierId, or newSupplier {name, code, phone, trn, notes, paymentTermsDays}
    - invoices [{invoiceDate, dueDate, reference, description, amount, cleared}]:
      references already in the ledger are skipped, never overwritten. A row's
      `cleared` becomes a payment allocated to that invoice (the statement
      doesn't say when it was paid -- flagged in the payment's notes).
    - unallocatedPayments [{date, reference, amount}]: receipts listed on the
      statement that aren't tied to an invoice go in on account; one already
      recorded with the same date + amount + reference is skipped.
    """
    stmt_date = _date(data['statementDate'], 'statement date') if data.get('statementDate') else None
    stmt_label = stmt_date.strftime('%d/%m/%Y') if stmt_date else 'import'

    new_sup = data.get('newSupplier')
    sup_params = _supplier_params(new_sup) if new_sup else None
    supplier_id = int(data.get('supplierId') or 0)
    if not sup_params and not supplier_id:
        raise LedgerError("Choose a supplier")

    invoices = []
    for d in data.get('invoices') or []:
        ref = str(d.get('reference') or '').strip()
        cleared = _money(d.get('cleared'))
        amount = _money(d.get('amount'))
        if cleared < 0 or cleared > amount:
            raise LedgerError(f"Invoice {ref}: cleared amount must be between 0 and the invoice amount")
        invoices.append((d, cleared))
    payments = []
    for p in data.get('unallocatedPayments') or []:
        amt = _money(p.get('amount'))
        if amt > 0:
            payments.append((_date(p.get('date') or (stmt_date and stmt_date.isoformat()), 'payment date'),
                             str(p.get('reference') or '').strip(), amt))
    if not invoices and not payments:
        raise LedgerError("Nothing selected to import")

    def fn(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            sid = supplier_id
            if sup_params:
                cur.execute("SELECT id FROM suppliers WHERE lower(name) = lower(%s)", (sup_params[0],))
                row = cur.fetchone()
                if row:
                    sid = row['id']
                else:
                    cur.execute(
                        """INSERT INTO suppliers (name, code, payment_terms_days, phone, trn, notes)
                           VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""", sup_params)
                    sid = cur.fetchone()['id']
            cur.execute("SELECT * FROM suppliers WHERE id = %s", (sid,))
            sup = cur.fetchone()
            if not sup:
                raise LedgerError("Supplier not found")
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NS, sid))
            cur.execute("SELECT reference FROM supplier_invoices WHERE supplier_id = %s", (sid,))
            existing = {r['reference'] for r in cur.fetchall()}

            added, skipped, cleared_count = 0, [], 0
            for d, cleared in invoices:
                p = _invoice_params(d, sup['payment_terms_days'])
                ref = p[2]
                if ref in existing:
                    skipped.append(ref)
                    continue
                existing.add(ref)
                cur.execute(
                    """INSERT INTO supplier_invoices
                         (supplier_id, invoice_date, due_date, reference, description, amount, credit, notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (sid,) + p)
                inv_id = cur.fetchone()['id']
                added += 1
                if cleared > 0:
                    cur.execute(
                        """INSERT INTO supplier_payments (supplier_id, payment_date, amount, method, notes)
                           VALUES (%s,%s,%s,'',%s) RETURNING id""",
                        (sid, stmt_date or p[0], cleared,
                         f"Cleared on {ref} per supplier statement {stmt_label} (payment date unknown)"))
                    pid = cur.fetchone()['id']
                    cur.execute(
                        "INSERT INTO supplier_payment_allocations (payment_id, invoice_id, amount) VALUES (%s,%s,%s)",
                        (pid, inv_id, cleared))
                    cleared_count += 1

            pay_added = 0
            for pay_date, ref, amt in payments:
                cur.execute(
                    """SELECT 1 FROM supplier_payments
                        WHERE supplier_id=%s AND payment_date=%s AND amount=%s AND cheque_no=%s""",
                    (sid, pay_date, amt, ref))
                if cur.fetchone():
                    continue
                cur.execute(
                    """INSERT INTO supplier_payments (supplier_id, payment_date, amount, method, cheque_no, notes)
                       VALUES (%s,%s,%s,'',%s,%s)""",
                    (sid, pay_date, amt, ref, f"Receipt per supplier statement {stmt_label}"))
                pay_added += 1
            return {'supplierId': sid, 'added': added, 'skipped': skipped,
                    'clearedPayments': cleared_count, 'paymentsAdded': pay_added}

    return {'success': True, 'data': db.transaction(fn)}
