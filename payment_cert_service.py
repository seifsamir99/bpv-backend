"""
Sub-contractor payment certificates — the /payment-cert page.

Newell issues these DOWNWARD to its own subcontractors: the same instrument a
main contractor issues to Newell (the AFAQ "SUB-CONTRACTOR MONTHLY PAYMENT
CERTIFICATE" the format was copied from).

Tables (see schema_supabase.sql): payment_cert_contracts, payment_certificates,
payment_cert_counter.

Why this is stored rather than a stateless generator like the LPO and Job Order
pages: a payment certificate is CUMULATIVE. What may be certified this month
depends on what was already certified, and the retention held to date only
exists as a running total. Those figures are derived here from the stored
certificate chain, never typed by the user — which is the whole point, because
a re-keyed "previous" column that disagrees with what was actually paid is the
classic way these documents go wrong.

The money model (fixed with the user 2026-08-20):
  * The user types ONE number per month: the CASH the subcontractor actually
    receives — the "Net Certified This Payment" line at the foot of the
    certificate.
  * Everything above it is worked BACKWARDS from that figure. The certified
    value is derived, not entered, so it usually lands on an odd number.
  * Retention is still deducted off the certified value, and VAT is still
    charged on what remains — the same shape as the reference certificate
    (436,054.14 x 5% = 21,802.71), just solved from the other end.
  * No advance payment.

The user pays a round figure and works out what that implies, rather than
certifying a value and discovering the payment. Do not "simplify" compute() by
treating the input as the certified amount or as a VAT-inclusive invoice total
— both were considered and explicitly rejected on 2026-08-20.

Because the user enters the CURRENT month's figure, there is deliberately no
"Less Previous Certified Paid Amount" line. That line exists on the reference
document only because its "Amount Certified" is a cumulative-to-date figure.
Do not reintroduce it here — it would deduct the previous payment twice.
"""

import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import db

# Money is handled as Decimal end to end. Floats cannot represent 0.05 exactly,
# and these figures are certified to a subcontractor and filed for VAT.
TWOPLACES = Decimal('0.01')

BILL_TYPES = ('interim', 'final')


class PaymentCertError(Exception):
    """A refusal the user needs to see verbatim (over-certification, bad input).

    api.py returns the message as-is rather than through safe_error(): these
    carry no database detail, and "you would exceed the contract value by
    12,500.00" is the entire value of the check.
    """


def _money(value):
    """Coerce to a 2dp Decimal, rejecting anything that is not a number."""
    if value is None or value == '':
        return Decimal('0.00')
    try:
        return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        raise PaymentCertError("'%s' is not a valid amount" % (value,))


def _f(value):
    """Decimal/None -> float, for JSON. Callers round before this point."""
    return float(value or 0)


# -- the calculation ---------------------------------------------------------

def compute(contract, net_paid, other_deduction=0):
    """The certificate arithmetic. Pure — no database, no side effects.

    `net_paid` is what the user typed: the cash the subcontractor actually
    receives this month. Every other row is worked BACKWARDS from it, so the
    certified value is derived rather than entered.

    Rounding is deliberately absorbed by the derived rows, working down from
    the cash rather than up from the certified value. Deriving straight from
    net_paid instead would leave the bottom line a cent off the figure the user
    typed, on a document whose entire purpose is to state that figure:

        total_amount  = round(net / (1 + vat))     -> vat = net - total, exact
        amount_ex_vat = round((total + other) / (1 - ret))
                                                   -> retention ties to total, exact

    so `total + vat == net_paid` and `ex_vat - retention - other == total` hold
    to the cent, while vat_charged and retention_amount still equal their
    stated percentages once rounded.

    Note vat_charged is smaller than vat_portion whenever retention is held.
    That is correct, not a rounding bug: VAT on the retained money falls due
    when the retention is released, not now. The PDF carries a memo line saying
    so, because it reliably looks like an error to whoever checks it.
    """
    net_payable = _money(net_paid)
    other = _money(other_deduction)
    if net_payable <= 0:
        raise PaymentCertError("This month's amount must be greater than zero")
    if other < 0:
        raise PaymentCertError("Other deduction cannot be negative")

    vat_pct = Decimal(str(contract['vat_percent'] or 0))
    ret_pct = Decimal(str(contract['retention_percent'] or 0))
    if ret_pct >= 100:
        raise PaymentCertError("Retention % must be below 100")

    total_amount = (net_payable / (1 + vat_pct / 100)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    vat_charged = net_payable - total_amount

    amount_ex_vat = ((total_amount + other) / (1 - ret_pct / 100)).quantize(
        TWOPLACES, rounding=ROUND_HALF_UP)
    retention_amount = amount_ex_vat - total_amount - other

    # VAT on the FULL certified value — what the subcontractor's own invoice
    # carries. The gap to vat_charged is the VAT sitting on the retention.
    vat_portion = (amount_ex_vat * vat_pct / 100).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    return {
        'amount_entered':   net_payable,
        'amount_ex_vat':    amount_ex_vat,
        'vat_portion':      vat_portion,
        'retention_amount': retention_amount,
        'other_deduction':  other,
        'total_amount':     total_amount,
        'vat_charged':      vat_charged,
        'net_payable':      net_payable,
    }


def cumulative_state(contract, before_cert_no=None):
    """Running totals from the stored certificate chain.

    before_cert_no limits the sum to certificates EARLIER than that number, so
    reprinting an old certificate shows the position as it stood at the time
    rather than today's.
    """
    sql = """SELECT coalesce(sum(amount_ex_vat), 0)    AS certified,
                    coalesce(sum(net_payable), 0)      AS paid,
                    coalesce(sum(retention_amount), 0) AS retention,
                    coalesce(max(cert_no), 0)          AS last_no
             FROM payment_certificates WHERE contract_id = %s"""
    params = [contract['id']]
    if before_cert_no is not None:
        sql += " AND cert_no < %s"
        params.append(before_cert_no)

    row = db.q_one(sql, tuple(params)) or {}
    certified = _money(row.get('certified'))
    contract_value = _money(contract['contract_value'])
    return {
        'previously_certified':  certified,
        'previously_paid':       _money(row.get('paid')),
        'retention_held':        _money(row.get('retention')),
        'last_cert_no':          int(row.get('last_no') or 0),
        'contract_value':        contract_value,
        'balance_of_contract':   contract_value - certified,
    }


def preview(contract_id, net_paid, other_deduction=0):
    """compute() + the cumulative block, without writing anything.

    The frontend renders its live certification block straight from this, so
    the arithmetic is never duplicated in JavaScript.
    """
    contract = get_contract(contract_id)
    if not contract:
        raise PaymentCertError("Contract not found")

    figures = compute(contract, net_paid, other_deduction)
    state = cumulative_state(contract)

    cumulative_certified = state['previously_certified'] + figures['amount_ex_vat']
    overrun = cumulative_certified - state['contract_value']

    out = {k: _f(v) for k, v in figures.items()}
    out.update({k: _f(v) for k, v in state.items() if k != 'last_cert_no'})
    out.update({
        'contract':               contract,
        'next_cert_no':           state['last_cert_no'] + 1,
        'cumulative_certified':   _f(cumulative_certified),
        'balance_after':          _f(state['contract_value'] - cumulative_certified),
        'retention_held_after':   _f(state['retention_held'] + figures['retention_amount']),
        # Advisory: the UI warns, the save refuses unless allow_overrun is set.
        'over_certified':         overrun > 0,
        'overrun_amount':         _f(overrun if overrun > 0 else 0),
    })
    return out


# -- contracts ---------------------------------------------------------------

CONTRACT_COLS = """id, subcontractor, project, project_code, scope, job_order_ref,
                   contract_value, retention_percent, vat_percent, contact, phone,
                   address, scope_items, archived, created_at"""


def _contract_json(row):
    if not row:
        return None
    out = dict(row)
    for key in ('contract_value', 'retention_percent', 'vat_percent'):
        out[key] = _f(out.get(key))
    items = out.get('scope_items') or []
    out['scope_items'] = items if isinstance(items, list) else json.loads(items)
    if out.get('created_at'):
        out['created_at'] = out['created_at'].isoformat()
    return out


def list_contracts(include_archived=False):
    sql = "SELECT %s FROM payment_cert_contracts" % CONTRACT_COLS
    if not include_archived:
        sql += " WHERE archived = false"
    sql += " ORDER BY subcontractor, id"
    return [_contract_json(r) for r in db.q(sql)]


def get_contract(contract_id):
    return _contract_json(db.q_one(
        "SELECT %s FROM payment_cert_contracts WHERE id = %%s" % CONTRACT_COLS,
        (contract_id,)))


def _validate_contract(data):
    name = (data.get('subcontractor') or '').strip()
    if not name:
        raise PaymentCertError("Subcontractor name is required")

    value = _money(data.get('contract_value'))
    if value <= 0:
        raise PaymentCertError("Contract value must be greater than zero")

    ret = _money(data.get('retention_percent', 10))
    vat = _money(data.get('vat_percent', 5))
    if not (0 <= ret <= 100):
        raise PaymentCertError("Retention % must be between 0 and 100")
    if not (0 <= vat <= 100):
        raise PaymentCertError("VAT % must be between 0 and 100")

    items = data.get('scope_items') or []
    if not isinstance(items, list):
        raise PaymentCertError("Scope items must be a list")

    return {
        'subcontractor':     name,
        'project':           (data.get('project') or '').strip(),
        'project_code':      (data.get('project_code') or '').strip(),
        'scope':             (data.get('scope') or '').strip(),
        'job_order_ref':     (data.get('job_order_ref') or '').strip(),
        'contract_value':    value,
        'retention_percent': ret,
        'vat_percent':       vat,
        'contact':           (data.get('contact') or '').strip(),
        'phone':             (data.get('phone') or '').strip(),
        'address':           (data.get('address') or '').strip(),
        'scope_items':       json.dumps([str(i).strip() for i in items if str(i).strip()]),
    }


def create_contract(data):
    f = _validate_contract(data)
    row = db.q_one(
        """INSERT INTO payment_cert_contracts
             (subcontractor, project, project_code, scope, job_order_ref, contract_value,
              retention_percent, vat_percent, contact, phone, address, scope_items)
           VALUES (%(subcontractor)s, %(project)s, %(project_code)s, %(scope)s,
                   %(job_order_ref)s, %(contract_value)s, %(retention_percent)s,
                   %(vat_percent)s, %(contact)s, %(phone)s, %(address)s, %(scope_items)s)
           ON CONFLICT (subcontractor, project) DO NOTHING
           RETURNING """ + CONTRACT_COLS, f)
    if not row:
        raise PaymentCertError(
            "A contract for %s on this project already exists" % f['subcontractor'])
    return _contract_json(row)


def update_contract(contract_id, data):
    """Edit contract details.

    The contract value may only be RAISED once certificates exist: lowering it
    below what has already been certified would retroactively turn issued
    certificates into over-certifications. Raising it is how a variation order
    is absorbed.
    """
    existing = get_contract(contract_id)
    if not existing:
        raise PaymentCertError("Contract not found")

    f = _validate_contract(data)
    state = cumulative_state(existing)
    if state['last_cert_no'] and f['contract_value'] < state['previously_certified']:
        raise PaymentCertError(
            "Contract value cannot be set below the %s already certified on this contract"
            % state['previously_certified'])

    row = db.q_one(
        """UPDATE payment_cert_contracts SET
             subcontractor = %(subcontractor)s, project = %(project)s,
             project_code = %(project_code)s, scope = %(scope)s,
             job_order_ref = %(job_order_ref)s, contract_value = %(contract_value)s,
             retention_percent = %(retention_percent)s, vat_percent = %(vat_percent)s,
             contact = %(contact)s, phone = %(phone)s, address = %(address)s,
             scope_items = %(scope_items)s
           WHERE id = %(id)s
           RETURNING """ + CONTRACT_COLS, dict(f, id=contract_id))
    return _contract_json(row)


def archive_contract(contract_id):
    """Hide a finished contract. Never a hard delete — the certificates issued
    under it are financial records, and the FK would cascade them away."""
    n = db.execute(
        "UPDATE payment_cert_contracts SET archived = true WHERE id = %s", (contract_id,))
    if not n:
        raise PaymentCertError("Contract not found")
    return True


# -- certificates ------------------------------------------------------------

MONEY_COLS = ('amount_entered', 'amount_ex_vat', 'vat_portion', 'retention_amount',
              'other_deduction', 'total_amount', 'vat_charged', 'net_payable')

CERT_COLS = """id, contract_id, cert_no, ref_number, bill_month, invoice_date, bill_type,
               amount_entered, amount_ex_vat, vat_portion, retention_amount,
               other_deduction, other_deduction_note, total_amount, vat_charged,
               net_payable, scope_amounts, created_at"""


def _cert_json(row):
    if not row:
        return None
    out = dict(row)
    for key in MONEY_COLS:
        out[key] = _f(out.get(key))
    amounts = out.get('scope_amounts') or {}
    out['scope_amounts'] = amounts if isinstance(amounts, dict) else json.loads(amounts)
    for key in ('invoice_date', 'created_at'):
        if out.get(key):
            out[key] = out[key].isoformat()
    return out


def list_certificates(contract_id):
    return [_cert_json(r) for r in db.q(
        "SELECT %s FROM payment_certificates WHERE contract_id = %%s ORDER BY cert_no"
        % CERT_COLS, (contract_id,))]


def get_certificate(cert_id):
    return _cert_json(db.q_one(
        "SELECT %s FROM payment_certificates WHERE id = %%s" % CERT_COLS, (cert_id,)))


def ref_number(project_code, counter_value):
    """'PC/NEMW/{project_code} / {year}/{NNNNN}' — same shape as _jo_ref_number
    in api.py, so payment certificates file alongside the job orders."""
    return "PC/NEMW/%s / %d/%05d" % (project_code, datetime.now().year, counter_value)


def save_certificate(contract_id, net_paid, invoice_date, bill_month='', bill_type='interim',
                     other_deduction=0, other_deduction_note='', scope_amounts=None,
                     allow_overrun=False):
    """Issue the next certificate on a contract.

    The whole thing runs inside one transaction holding a
    pg_advisory_xact_lock on the contract id. Two people saving at once would
    otherwise both read the same last cert_no and both claim it — the same
    write-race that duplicated BPV vouchers on edit.
    """
    contract = get_contract(contract_id)
    if not contract:
        raise PaymentCertError("Contract not found")
    if contract.get('archived'):
        raise PaymentCertError("This contract is archived")

    bill_type = (bill_type or 'interim').lower().strip()
    if bill_type not in BILL_TYPES:
        raise PaymentCertError("Bill type must be one of %s" % ', '.join(BILL_TYPES))

    if not invoice_date:
        raise PaymentCertError("Invoice date is required")

    figures = compute(contract, net_paid, other_deduction)

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (0x9AC3, int(contract_id)))

            # Re-read the chain INSIDE the lock: whatever we read before it was
            # taken may already be stale.
            cur.execute(
                """SELECT coalesce(sum(amount_ex_vat), 0), coalesce(max(cert_no), 0)
                   FROM payment_certificates WHERE contract_id = %s""", (contract_id,))
            prev_certified, last_no = cur.fetchone()
            prev_certified = _money(prev_certified)

            cumulative = prev_certified + figures['amount_ex_vat']
            overrun = cumulative - _money(contract['contract_value'])
            if overrun > 0 and not allow_overrun:
                raise PaymentCertError(
                    "This certificate would take the cumulative certified amount to "
                    "{:,.2f}, which is {:,.2f} over the contract value of {:,.2f}. "
                    "Raise the contract value if this is a variation order, or confirm "
                    "to certify anyway.".format(
                        cumulative, overrun, _money(contract['contract_value'])))

            cert_no = int(last_no) + 1

            # Same atomic conditional bump as job_order_counter: read, then
            # update only if nobody moved it, so numbers are never reused.
            cur.execute("SELECT value FROM payment_cert_counter WHERE id = 1")
            counter_row = cur.fetchone()
            counter = int(counter_row[0]) if counter_row else 1
            cur.execute(
                "UPDATE payment_cert_counter SET value = value + 1 WHERE id = 1 AND value = %s",
                (counter,))
            if cur.rowcount != 1:
                cur.execute("SELECT value FROM payment_cert_counter WHERE id = 1")
                counter = int(cur.fetchone()[0])
                cur.execute("UPDATE payment_cert_counter SET value = value + 1 WHERE id = 1")

            ref = ref_number(contract.get('project_code') or 'NA', counter)

            cur.execute(
                """INSERT INTO payment_certificates
                      (contract_id, cert_no, ref_number, bill_month, invoice_date, bill_type,
                       amount_entered, amount_ex_vat, vat_portion, retention_amount,
                       other_deduction, other_deduction_note, total_amount, vat_charged,
                       net_payable, scope_amounts)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING """ + CERT_COLS,
                (contract_id, cert_no, ref, (bill_month or '').strip(), invoice_date,
                 bill_type, figures['amount_entered'], figures['amount_ex_vat'],
                 figures['vat_portion'], figures['retention_amount'],
                 figures['other_deduction'], (other_deduction_note or '').strip(),
                 figures['total_amount'], figures['vat_charged'], figures['net_payable'],
                 json.dumps(scope_amounts or {})))
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, cur.fetchone()))

    return _cert_json(db.transaction(fn))


def delete_certificate(cert_id):
    """Remove a certificate — only ever the LATEST one on its contract.

    Deleting from the middle of the chain would silently change the cumulative
    and retention-held figures on every certificate already issued after it,
    none of which get reprinted.
    """
    cert = get_certificate(cert_id)
    if not cert:
        raise PaymentCertError("Certificate not found")

    latest = db.q_one(
        "SELECT max(cert_no) AS n FROM payment_certificates WHERE contract_id = %s",
        (cert['contract_id'],))
    if int(latest['n'] or 0) != cert['cert_no']:
        raise PaymentCertError(
            "Only the latest certificate can be deleted. This is Bill No. %s and "
            "Bill No. %s was issued after it." % (cert['cert_no'], latest['n']))

    db.execute("DELETE FROM payment_certificates WHERE id = %s", (cert_id,))
    return True


def certificate_pdf_payload(cert_id):
    """Everything generate_payment_certificate() needs, in one dict.

    The cumulative block is rebuilt as it stood when this certificate was
    issued (before_cert_no), so reprinting an old one reproduces the original
    document rather than today's position.
    """
    cert = get_certificate(cert_id)
    if not cert:
        raise PaymentCertError("Certificate not found")

    contract = get_contract(cert['contract_id'])
    state = cumulative_state(contract, before_cert_no=cert['cert_no'])

    cumulative_certified = state['previously_certified'] + _money(cert['amount_ex_vat'])
    return {
        'certificate': cert,
        'contract':    contract,
        'cumulative':  {
            'previously_certified': _f(state['previously_certified']),
            'cumulative_certified': _f(cumulative_certified),
            'balance_of_contract':  _f(state['contract_value'] - cumulative_certified),
            'retention_held':       _f(state['retention_held'] + _money(cert['retention_amount'])),
            'contract_value':       _f(state['contract_value']),
        },
    }
