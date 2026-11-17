"""
Client projects: the /projects page. A site's balance sheet.

Newell is the MEP subcontractor; the client (e.g. Sumer Contracting on Warsan,
site S108) certifies work monthly and Newell invoices UPWARD against each
certificate. This page answers "how much is the job worth, how much have we
been paid, and how much have we spent on it".

Tables (migrate_projects.py / schema_supabase.sql): projects, project_invoices,
project_receipts, project_cost_links, plus bpv_line_items.project_code.

Money in
  The user types ONE figure per invoice: the client's CUMULATIVE certified work
  to date (as printed on their PC). Everything else is derived from it and the
  stored invoice chain:
      advance recovery = adv% x cumulative      retention = ret% x cumulative
      total (ex VAT)   = cumulative - advance - retention - previous certified
      VAT              = vat% x total           payable   = total + VAT
  This is the client's own arithmetic (Sumer's register), verified to the fil
  against PC1-PC3 on 2026-11-16. Percentages: cumulative % is rounded, and
  current % = cumulative - previous so the row adds up on the printed invoice.
  Every derived figure is STORED at issue, so a reprint shows what went out.

Money out
  Nothing in the app carries a site field except the new BPV project_code, and
  BPV descriptions are free text ("PROJECT# 108", "Warsan site", or nothing).
  So costs are SUGGESTED by matching the project's terms, and the user decides
  each one once (included / excluded / security). Decisions on BPV lines are
  keyed on (bpv_no, cheque_no, debit) because the BPV service deletes and
  re-inserts every line on edit, so row ids are not stable. A changed line
  comes back to review rather than being counted silently.

  Spend = included BPV payments (cash actually paid). LPOs and job orders are
  commitments: shown as "ordered", never added to spend, or the same money
  would be counted twice once the BPV that pays them is included.
"""

import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

import db

TWOPLACES = Decimal('0.01')
KINDS = ('advance', 'interim', 'final')
STATUSES = ('included', 'excluded', 'security')
_LOCK_NS = 0x9AC4  # pg_advisory_xact_lock(namespace, project_id)


class ProjectError(Exception):
    """A refusal written for the user; api.py returns it verbatim."""


def _money(value):
    if value is None or value == '':
        return Decimal('0.00')
    try:
        return Decimal(str(value)).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        raise ProjectError("'%s' is not a valid amount" % (value,))


def _r2(d):
    return d.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def _f(value):
    return float(value or 0)


def _iso(value):
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m-%d')
    return value or ''


def bpv_key(bpv_no, cheque_no, debit):
    """Stable identity for a BPV line. Must match migrate_projects.bpv_key()."""
    return '%s|%s|%.2f' % (str(bpv_no).strip(), str(cheque_no or '').strip(), float(debit or 0))


# -- projects ----------------------------------------------------------------

PROJECT_COLS = """id, code, name, client, client_trn, invoice_title, contract_value,
                  advance_recovery_pct, retention_pct, vat_pct, match_terms, archived, created_at"""


def _project_json(row):
    if not row:
        return None
    out = dict(row)
    for k in ('contract_value', 'advance_recovery_pct', 'retention_pct', 'vat_pct'):
        out[k] = _f(out[k])
    out['match_terms'] = list(out.get('match_terms') or [])
    out['created_at'] = _iso(out.get('created_at'))
    return out


def list_projects(include_archived=False):
    sql = "SELECT %s FROM projects" % PROJECT_COLS
    if not include_archived:
        sql += " WHERE archived = false"
    return [_project_json(r) for r in db.q(sql + " ORDER BY code")]


def get_project(project_id):
    return _project_json(db.q_one("SELECT %s FROM projects WHERE id = %%s" % PROJECT_COLS, (project_id,)))


def _clean_project(data):
    code = re.sub(r'\s+', '', str(data.get('code') or '')).upper()
    if not code:
        raise ProjectError("Project code is required (e.g. S108)")
    for field in ('name', 'client'):
        if not str(data.get(field) or '').strip():
            raise ProjectError("%s is required" % field.capitalize())
    value = _money(data.get('contract_value'))
    if value <= 0:
        raise ProjectError("Contract value must be greater than zero")
    pcts = {}
    for k, default in (('advance_recovery_pct', 10), ('retention_pct', 10), ('vat_pct', 5)):
        p = _money(data.get(k, default) if data.get(k, '') != '' else default)
        if p < 0 or p >= 100:
            raise ProjectError("%s must be between 0 and 100" % k.replace('_', ' '))
        pcts[k] = p
    terms = data.get('match_terms') or []
    if isinstance(terms, str):
        terms = terms.split(',')
    terms = [t.strip() for t in terms if t and t.strip()]
    return dict(code=code, name=str(data['name']).strip(), client=str(data['client']).strip(),
                client_trn=str(data.get('client_trn') or '').strip(),
                invoice_title=str(data.get('invoice_title') or '').strip(),
                contract_value=value, match_terms=terms, **pcts)


def create_project(data):
    p = _clean_project(data)
    if db.q_one("SELECT 1 AS x FROM projects WHERE code = %s", (p['code'],)):
        raise ProjectError("Project %s already exists" % p['code'])
    row = db.q_one(
        """INSERT INTO projects (code, name, client, client_trn, invoice_title, contract_value,
                                 advance_recovery_pct, retention_pct, vat_pct, match_terms)
           VALUES (%(code)s, %(name)s, %(client)s, %(client_trn)s, %(invoice_title)s,
                   %(contract_value)s, %(advance_recovery_pct)s, %(retention_pct)s,
                   %(vat_pct)s, %(match_terms)s)
           RETURNING """ + PROJECT_COLS, p)
    return _project_json(row)


def update_project(project_id, data):
    if not get_project(project_id):
        raise ProjectError("Project not found")
    p = _clean_project(data)
    clash = db.q_one("SELECT id FROM projects WHERE code = %s AND id <> %s", (p['code'], project_id))
    if clash:
        raise ProjectError("Another project already uses code %s" % p['code'])
    p['id'] = project_id
    row = db.q_one(
        """UPDATE projects SET code=%(code)s, name=%(name)s, client=%(client)s,
                  client_trn=%(client_trn)s, invoice_title=%(invoice_title)s,
                  contract_value=%(contract_value)s, advance_recovery_pct=%(advance_recovery_pct)s,
                  retention_pct=%(retention_pct)s, vat_pct=%(vat_pct)s, match_terms=%(match_terms)s
           WHERE id=%(id)s RETURNING """ + PROJECT_COLS, p)
    return _project_json(row)


# -- invoices (money in) -----------------------------------------------------

INVOICE_COLS = """id, project_id, invoice_no, ref_number, invoice_date, kind, client_pc,
                  cumulative_work, prev_work, current_work, prev_pct, current_pct, cumulative_pct,
                  advance_recovery, retention, prev_certified, total_ex_vat, vat, payable, note,
                  created_at"""
_INV_MONEY = ('cumulative_work', 'prev_work', 'current_work', 'prev_pct', 'current_pct',
              'cumulative_pct', 'advance_recovery', 'retention', 'prev_certified',
              'total_ex_vat', 'vat', 'payable')


def _invoice_json(row):
    if not row:
        return None
    out = dict(row)
    for k in _INV_MONEY:
        out[k] = _f(out[k])
    out['invoice_date'] = _iso(out['invoice_date'])
    out['created_at'] = _iso(out.get('created_at'))
    return out


def list_invoices(project_id):
    rows = db.q("SELECT %s FROM project_invoices WHERE project_id = %%s ORDER BY invoice_no::int, id"
                % INVOICE_COLS, (project_id,))
    return [_invoice_json(r) for r in rows]


def get_invoice(invoice_id):
    return _invoice_json(db.q_one("SELECT %s FROM project_invoices WHERE id = %%s" % INVOICE_COLS,
                                  (invoice_id,)))


def _chain(rows):
    """Where the certification stands, from interim/final invoices only (the
    advance invoice certifies no work)."""
    work = [r for r in rows if r['kind'] in ('interim', 'final')]
    if not work:
        return {'prev_work': Decimal('0'), 'prev_pct': Decimal('0'), 'prev_certified': Decimal('0')}
    last = max(work, key=lambda r: int(r['invoice_no']))
    return {'prev_work': _money(last['cumulative_work']),
            'prev_pct': _money(last['cumulative_pct']),
            'prev_certified': sum((_money(r['total_ex_vat']) for r in work), Decimal('0'))}


def compute_interim(project, cumulative_work, chain):
    """The invoice arithmetic. Pure."""
    cum = _money(cumulative_work)
    if cum <= 0:
        raise ProjectError("Cumulative work certified must be greater than zero")
    if cum < chain['prev_work']:
        raise ProjectError("Cumulative work {:,.2f} is below the previous invoice's {:,.2f}. "
                           "Enter the client's CUMULATIVE figure to date, not this month's."
                           .format(cum, chain['prev_work']))
    contract = _money(project['contract_value'])
    cum_pct = _r2(cum / contract * 100)
    advance = _r2(cum * _money(project['advance_recovery_pct']) / 100)
    retention = _r2(cum * _money(project['retention_pct']) / 100)
    total = cum - advance - retention - chain['prev_certified']
    vat = _r2(total * _money(project['vat_pct']) / 100)
    return {
        'cumulative_work': cum, 'prev_work': chain['prev_work'], 'current_work': cum - chain['prev_work'],
        'prev_pct': chain['prev_pct'], 'current_pct': cum_pct - chain['prev_pct'], 'cumulative_pct': cum_pct,
        'advance_recovery': advance, 'retention': retention, 'prev_certified': chain['prev_certified'],
        'total_ex_vat': total, 'vat': vat, 'payable': total + vat,
    }


def compute_advance(project, amount_ex_vat):
    total = _money(amount_ex_vat)
    if total <= 0:
        raise ProjectError("Advance amount must be greater than zero")
    vat = _r2(total * _money(project['vat_pct']) / 100)
    return {k: Decimal('0') for k in ('cumulative_work', 'prev_work', 'current_work', 'prev_pct',
                                      'current_pct', 'cumulative_pct', 'advance_recovery',
                                      'retention', 'prev_certified')} | {
        'total_ex_vat': total, 'vat': vat, 'payable': total + vat}


def _figures(project, kind, data, rows):
    if kind == 'advance':
        return compute_advance(project, data.get('amount_ex_vat'))
    return compute_interim(project, data.get('cumulative_work'), _chain(rows))


def _next_no(rows):
    return '%02d' % (max([int(r['invoice_no']) for r in rows] or [0]) + 1)


def preview_invoice(project_id, data):
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found")
    kind = (data.get('kind') or 'interim').lower()
    if kind not in KINDS:
        raise ProjectError("Kind must be one of %s" % ', '.join(KINDS))
    rows = list_invoices(project_id)
    figs = _figures(project, kind, data, rows)
    out = {k: _f(v) for k, v in figs.items()}
    out['invoice_no'] = _next_no(rows)
    out['over_contract'] = figs['cumulative_work'] > _money(project['contract_value'])
    out['amount_in_words'] = words(figs['payable'])
    return out


def save_invoice(project_id, data):
    """Issue the next invoice. Runs under a per-project advisory lock and
    re-reads the chain inside it, so two saves can't both claim one number or
    both build on the same 'previous certified'."""
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found")
    if project['archived']:
        raise ProjectError("This project is archived")
    kind = (data.get('kind') or 'interim').lower()
    if kind not in KINDS:
        raise ProjectError("Kind must be one of %s" % ', '.join(KINDS))
    inv_date = data.get('invoice_date')
    if not inv_date:
        raise ProjectError("Invoice date is required")

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_LOCK_NS, int(project_id)))
            cur.execute("SELECT invoice_no, kind, cumulative_work, cumulative_pct, total_ex_vat "
                        "FROM project_invoices WHERE project_id = %s", (project_id,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            figs = _figures(project, kind, data, rows)
            if figs['cumulative_work'] > _money(project['contract_value']) and not data.get('allow_overrun'):
                raise ProjectError("Cumulative work {:,.2f} is over the contract value {:,.2f}. "
                                   "Raise the contract value for a variation, or confirm to issue anyway."
                                   .format(figs['cumulative_work'], _money(project['contract_value'])))
            no = _next_no(rows)
            year = str(inv_date)[:4]
            ref = (data.get('ref_number') or '').strip() or 'NWEM/PC/%s/%s' % (no, year)
            cur.execute(
                """INSERT INTO project_invoices
                     (project_id, invoice_no, ref_number, invoice_date, kind, client_pc,
                      cumulative_work, prev_work, current_work, prev_pct, current_pct,
                      cumulative_pct, advance_recovery, retention, prev_certified,
                      total_ex_vat, vat, payable, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING """ + INVOICE_COLS,
                (project_id, no, ref, inv_date, kind, (data.get('client_pc') or '').strip(),
                 figs['cumulative_work'], figs['prev_work'], figs['current_work'], figs['prev_pct'],
                 figs['current_pct'], figs['cumulative_pct'], figs['advance_recovery'],
                 figs['retention'], figs['prev_certified'], figs['total_ex_vat'], figs['vat'],
                 figs['payable'], (data.get('note') or '').strip()))
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, cur.fetchone()))

    return _invoice_json(db.transaction(fn))


def delete_invoice(invoice_id):
    """Only the latest invoice: removing one from the middle would change the
    'previous certified' on every invoice already issued after it."""
    inv = get_invoice(invoice_id)
    if not inv:
        raise ProjectError("Invoice not found")
    latest = db.q_one("SELECT max(invoice_no::int) AS n FROM project_invoices WHERE project_id = %s",
                      (inv['project_id'],))
    if int(latest['n'] or 0) != int(inv['invoice_no']):
        raise ProjectError("Only the latest invoice can be deleted (Invoice %02d was issued after this one)"
                           % int(latest['n']))
    db.execute("DELETE FROM project_invoices WHERE id = %s", (invoice_id,))
    return True


# -- receipts ----------------------------------------------------------------

RECEIPT_COLS = "id, project_id, invoice_id, receipt_date, cheque_no, bank, amount, note, created_at"


def _receipt_json(row):
    out = dict(row)
    out['amount'] = _f(out['amount'])
    out['receipt_date'] = _iso(out['receipt_date'])
    out['created_at'] = _iso(out.get('created_at'))
    return out


def list_receipts(project_id):
    return [_receipt_json(r) for r in db.q(
        "SELECT %s FROM project_receipts WHERE project_id = %%s ORDER BY receipt_date, id" % RECEIPT_COLS,
        (project_id,))]


def add_receipt(project_id, data):
    if not get_project(project_id):
        raise ProjectError("Project not found")
    amount = _money(data.get('amount'))
    if amount <= 0:
        raise ProjectError("Amount must be greater than zero")
    if not data.get('receipt_date'):
        raise ProjectError("Date received is required")
    invoice_id = data.get('invoice_id') or None
    if invoice_id:
        inv = get_invoice(invoice_id)
        if not inv or inv['project_id'] != int(project_id):
            raise ProjectError("That invoice is not on this project")
    row = db.q_one(
        """INSERT INTO project_receipts (project_id, invoice_id, receipt_date, cheque_no, bank, amount, note)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING """ + RECEIPT_COLS,
        (project_id, invoice_id, data['receipt_date'], str(data.get('cheque_no') or '').strip(),
         str(data.get('bank') or '').strip(), amount, str(data.get('note') or '').strip()))
    return _receipt_json(row)


def delete_receipt(receipt_id):
    if not db.execute("DELETE FROM project_receipts WHERE id = %s", (receipt_id,)):
        raise ProjectError("Receipt not found")
    return True


# -- costs (money out) -------------------------------------------------------

def _term_regex(terms):
    """Digits must stand alone, so '108' matches 'S# 108' / 'S108' but not
    cheque #1081 or an amount like 21,080."""
    parts = []
    for t in terms:
        t = t.strip()
        if not t:
            continue
        esc = re.escape(t)
        parts.append(r'(?<!\d)%s(?!\d)' % esc if t[0].isdigit() or t[-1].isdigit() else esc)
    return re.compile('|'.join(parts), re.I) if parts else None


_CHEQUE_BEFORE = re.compile(r'(?:CHEQUE|CHQ)\s*(?:NO\.?|COPY)?\s*[#:(]?\s*$', re.I)
_PROJECT_NO = re.compile(r'(?:PROJECTS?\s*S?\s*#|S\s*#|PROJECT\s+#?)\s*(\d{3})\b', re.I)


def _find(rx, text):
    """First term hit that is not a cheque number ('CHEQUE # 108' is cheque
    108, not site 108)."""
    if not rx:
        return None
    for m in rx.finditer(text):
        if m.group(0)[0].isdigit() and _CHEQUE_BEFORE.search(text[max(0, m.start() - 14):m.start()]):
            continue
        return m
    return None


_LPO_IN_TEXT = re.compile(r'LPO\s*(?:NO\.?|NUMBER|#)?\s*[:(#]?\s*(\d{3,5})', re.I)
_LPO_DIGITS = re.compile(r'(\d{3,5})(?:-R)?\s*$', re.I)


def _lpo_core(lpo_number):
    m = _LPO_DIGITS.search(lpo_number or '')
    return m.group(1) if m else None


def _decisions(project_id):
    return {(r['source'], r['source_key']): r['status'] for r in db.q(
        "SELECT source, source_key, status FROM project_cost_links WHERE project_id = %s", (project_id,))}


def costs(project_id):
    """Every cost line relevant to the project, sorted into buckets.

    Returns dict with bpv {included, security, excluded}, lpo/job_order
    {included, excluded}, and review (undecided suggestions, each with the
    reason it matched)."""
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found")
    rx = _term_regex(project['match_terms'])
    decided = _decisions(project_id)
    code_norm = project['code'].replace(' ', '').upper()

    lpos = db.q("""SELECT lpo_number, to_char(lpo_date,'YYYY-MM-DD') AS date, supplier_name, project,
                          delivery_site, total_amount FROM lpo_records ORDER BY lpo_date, lpo_number""")
    jos = db.q("""SELECT ref_number, to_char(jo_date,'YYYY-MM-DD') AS date, subcontractor_name, project,
                         project_code, sub_scope, total_amount FROM job_order_records ORDER BY jo_date""")
    bpvs = db.q("""SELECT bpv_no, date, type, description, company, cheque_no, cheque_date, debit, project_code
                   FROM bpv_line_items WHERE debit > 0 ORDER BY id""")

    out = {'bpv': {s: [] for s in STATUSES}, 'lpo': {'included': [], 'excluded': []},
           'job_order': {'included': [], 'excluded': []}, 'review': []}

    def place(source, item, key, reason, tagged=False):
        item['source'], item['key'] = source, key
        status = 'included' if tagged else decided.get((source, key))
        if tagged:
            item['reason'] = 'Tagged %s on the BPV' % project['code']
        if status:
            item['status'] = status
            out[source][status].append(item)
        elif reason:
            item['reason'] = reason
            out['review'].append(item)

    project_lpo_cores = {}
    for r in lpos:
        text = ' '.join(str(r.get(k) or '') for k in ('project', 'delivery_site', 'lpo_number'))
        m = rx.search(text) if rx else None
        item = {'lpo_number': r['lpo_number'], 'date': r['date'], 'company': r['supplier_name'],
                'project': r['project'] or '', 'amount': _f(r['total_amount'])}
        place('lpo', item, r['lpo_number'], m and 'LPO project mentions "%s"' % m.group(0))
        core = _lpo_core(r['lpo_number'])
        if core and (m or decided.get(('lpo', r['lpo_number'])) == 'included'):
            project_lpo_cores[core] = r['lpo_number']

    for r in jos:
        code = str(r.get('project_code') or '').replace(' ', '').upper()
        text = ' '.join(str(r.get(k) or '') for k in ('project', 'ref_number'))
        m = rx.search(text) if rx else None
        reason = ('Job order code %s' % r['project_code']) if code == code_norm else (
            m and 'Job order project mentions "%s"' % m.group(0))
        item = {'ref_number': r['ref_number'], 'date': r['date'], 'company': r['subcontractor_name'],
                'scope': r['sub_scope'] or '', 'amount': _f(r['total_amount'])}
        place('job_order', item, r['ref_number'], reason)

    # The same cheque + amount under two BPV numbers is usually a double entry;
    # including both would overstate spend, so the review row says so.
    seen = {}
    for r in bpvs:
        seen.setdefault((str(r['cheque_no'] or '').strip().lstrip('0'), _f(r['debit'])), []).append(
            str(r['bpv_no']).strip())
    own_digits = re.sub(r'\D', '', code_norm)

    paid_lpo_cores = set()
    for r in bpvs:
        text = '%s %s' % (r['description'] or '', r['company'] or '')
        key = bpv_key(r['bpv_no'], r['cheque_no'], r['debit'])
        item = {'bpv_no': str(r['bpv_no']).strip(), 'date': r['date'], 'type': r['type'],
                'description': (r['description'] or '').strip(), 'company': (r['company'] or '').strip(),
                'cheque_no': r['cheque_no'], 'cheque_date': r['cheque_date'], 'amount': _f(r['debit'])}
        lpo_refs = [n for n in _LPO_IN_TEXT.findall(text)]
        item['lpo_refs'] = lpo_refs
        warnings = []
        others = sorted({n for n in _PROJECT_NO.findall(text) if n != own_digits})
        if others:
            warnings.append('Description says project %s' % ', '.join(others))
        twins = [b for b in seen.get((str(r['cheque_no'] or '').strip().lstrip('0'), _f(r['debit'])), [])
                 if b != item['bpv_no']]
        if twins and str(r['cheque_no'] or '').strip():
            warnings.append('Same cheque and amount as BPV %s' % ', '.join(twins))
        item['warnings'] = warnings
        tagged = str(r.get('project_code') or '').replace(' ', '').upper() == code_norm
        reason = None
        if not tagged:
            hit = [project_lpo_cores[n] for n in lpo_refs if n in project_lpo_cores]
            m = _find(rx, text)
            if m:
                reason = 'Mentions "%s"' % m.group(0).strip()
            elif hit:
                reason = 'Pays %s (a %s LPO)' % (hit[0], project['code'])
        place('bpv', item, key, reason, tagged=tagged)
        if tagged or decided.get(('bpv', key)) == 'included':
            paid_lpo_cores.update(lpo_refs)

    for item in out['lpo']['included']:
        item['paid'] = _lpo_core(item['lpo_number']) in paid_lpo_cores
    return out


def decide(project_id, items):
    """items: [{source, key, status}] where status is included/excluded/security,
    or '' / None to send the line back to review."""
    if not get_project(project_id):
        raise ProjectError("Project not found")
    sets, clears = [], []
    for it in items or []:
        source, key, status = it.get('source'), str(it.get('key') or ''), it.get('status') or ''
        if source not in ('bpv', 'lpo', 'job_order') or not key:
            raise ProjectError("Bad cost reference")
        if status and status not in STATUSES:
            raise ProjectError("Status must be one of %s" % ', '.join(STATUSES))
        if status == 'security' and source != 'bpv':
            raise ProjectError("Only a cheque (BPV line) can be a security cheque")
        (sets if status else clears).append((source, key, status))

    def fn(conn):
        with conn.cursor() as cur:
            for source, key, status in sets:
                cur.execute(
                    """INSERT INTO project_cost_links (project_id, source, source_key, status)
                       VALUES (%s,%s,%s,%s)
                       ON CONFLICT (project_id, source, source_key)
                       DO UPDATE SET status = EXCLUDED.status, decided_at = now()""",
                    (project_id, source, key, status))
            for source, key, _ in clears:
                cur.execute("DELETE FROM project_cost_links WHERE project_id=%s AND source=%s AND source_key=%s",
                            (project_id, source, key))
        return len(sets) + len(clears)

    return db.transaction(fn)


# -- the balance sheet -------------------------------------------------------

def summary(project_id, cost_data=None):
    """Every headline figure, derived on read. Both money-in and money-out
    figures are cash and include VAT."""
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found")
    invoices = list_invoices(project_id)
    receipts = list_receipts(project_id)
    c = cost_data or costs(project_id)

    work = [i for i in invoices if i['kind'] in ('interim', 'final')]
    last = max(work, key=lambda i: int(i['invoice_no'])) if work else None
    advance_ex_vat = sum(i['total_ex_vat'] for i in invoices if i['kind'] == 'advance')
    recovered = last['advance_recovery'] if last else 0

    invoiced = sum(i['payable'] for i in invoices)
    received = sum(r['amount'] for r in receipts)
    paid_out = sum(x['amount'] for x in c['bpv']['included'])
    lpo_total = sum(x['amount'] for x in c['lpo']['included'])
    lpo_unpaid = sum(x['amount'] for x in c['lpo']['included'] if not x.get('paid'))
    jo_total = sum(x['amount'] for x in c['job_order']['included'])

    r = lambda x: round(x, 2)
    return {
        'contract_value': project['contract_value'],
        'certified_to_date': last['cumulative_work'] if last else 0,
        'certified_pct': last['cumulative_pct'] if last else 0,
        'balance_of_contract': r(project['contract_value'] - (last['cumulative_work'] if last else 0)),
        'invoiced': r(invoiced),
        'received': r(received),
        'owed_by_client': r(invoiced - received),
        'retention_held': last['retention'] if last else 0,
        'advance_ex_vat': r(advance_ex_vat),
        'advance_recovered': recovered,
        'advance_left': r(advance_ex_vat - recovered),
        'paid_out': r(paid_out),
        'security_held': r(sum(x['amount'] for x in c['bpv']['security'])),
        'lpo_ordered': r(lpo_total),
        'lpo_unpaid': r(lpo_unpaid),
        'job_orders_ordered': r(jo_total),
        'cash_position': r(received - paid_out),
        'to_review': len(c['review']),
    }


def overview(project_id):
    """One call for the page: project, summary, invoices with their receipts, costs."""
    project = get_project(project_id)
    if not project:
        raise ProjectError("Project not found")
    c = costs(project_id)
    invoices = list_invoices(project_id)
    receipts = list_receipts(project_id)
    for inv in invoices:
        got = [x for x in receipts if x['invoice_id'] == inv['id']]
        inv['receipts'] = got
        inv['received'] = round(sum(x['amount'] for x in got), 2)
        inv['balance'] = round(inv['payable'] - inv['received'], 2)
    return {'project': project, 'summary': summary(project_id, c), 'invoices': invoices,
            'unallocated_receipts': [x for x in receipts if not x['invoice_id']], 'costs': c}


# -- documents ---------------------------------------------------------------

ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
        "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _under1000(n):
    h, rest, out = n // 100, n % 100, []
    if h:
        out.append(ONES[h] + " Hundred")
    if rest >= 20:
        out.append(TENS[rest // 10] + ("-" + ONES[rest % 10] if rest % 10 else ""))
    elif rest:
        out.append(ONES[rest])
    return " ".join(out)


def words(amount):
    x = _money(amount)
    whole = int(x)
    fils = int((x - whole) * 100)
    parts, left = [], whole
    for unit, name in ((10**9, "Billion"), (10**6, "Million"), (10**3, "Thousand"), (1, "")):
        chunk, left = left // unit, left % unit
        if chunk:
            parts.append(_under1000(chunk) + (" " + name if name else ""))
    return "UAE Dirhams %s%s Only" % (" ".join(parts) or "Zero",
                                      " and %s Fils" % _under1000(fils) if fils else "")


def invoice_pdf_payload(invoice_id):
    inv = get_invoice(invoice_id)
    if not inv:
        raise ProjectError("Invoice not found")
    project = get_project(inv['project_id'])
    return {'invoice': inv, 'project': project, 'amount_in_words': words(inv['payable'])}


def statement_payload(project_id):
    data = overview(project_id)
    s = data['summary']
    data['balance_in_words'] = words(s['owed_by_client'])
    return data
