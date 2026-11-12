"""
WPS roster: who is on each company's bank salary-transfer (WPS) file.

The bank file is the only place that holds each employee's MOL code, legal
name, bank routing code and account number. The office used to upload last
month's file every month; now it is uploaded once (and again when someone
joins or changes bank) and kept here, so the Payroll page can build the
month's file straight from the payroll.

Table (see schema_supabase.sql): wps_roster — one row per WPS line, keyed by
EMPMOLCODE, grouped by COMPMOLCODE (Newell / Najmat Al Waad). employee_id is
the payroll employees.id paired with that line ('' = not paired yet, so the
page name-matches it; '-' = the office set it to "not in payroll").
All values are text so MOL codes and account numbers keep their leading zeros.
"""

import db


def _row(r):
    return {
        'molCode': r['mol_code'],
        'compMol': r['comp_mol'],
        'name': r['emp_name'],
        'bankCode': r['bank_code'],
        'accNo': r['acc_no'],
        'employeeId': r['employee_id'] or '',
    }


def get_roster():
    rows = db.q("SELECT * FROM wps_roster ORDER BY comp_mol, sort_order, mol_code")
    return [_row(r) for r in rows]


def replace_company(comp_mol, rows):
    """Replace one company's lines with those of a freshly uploaded file.

    A line that was already paired keeps its employee, unless the upload
    carries a pairing of its own (rows[i]['employeeId']).
    Returns the number of lines saved.
    """
    comp_mol = str(comp_mol).strip()
    clean = []
    seen = set()
    for r in rows:
        mol = str(r.get('molCode') or '').strip()
        if not mol or mol in seen:
            continue
        seen.add(mol)
        clean.append(r)
    if not clean:
        raise ValueError('No employee rows to save')

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("SELECT mol_code, employee_id FROM wps_roster")
            existing = {m: e for m, e in cur.fetchall()}
            cur.execute("DELETE FROM wps_roster WHERE comp_mol = %s", (comp_mol,))
            # A MOL code moving from the other company's file moves its row too
            cur.execute("DELETE FROM wps_roster WHERE mol_code = ANY(%s)", ([str(r['molCode']).strip() for r in clean],))
            for i, r in enumerate(clean):
                mol = str(r['molCode']).strip()
                emp = r.get('employeeId')
                emp = existing.get(mol, '') if emp is None else str(emp)
                cur.execute(
                    """INSERT INTO wps_roster
                         (mol_code, comp_mol, emp_name, bank_code, acc_no, employee_id, sort_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (mol, comp_mol, str(r.get('name') or '').strip(),
                     str(r.get('bankCode') or '').strip(), str(r.get('accNo') or '').strip(),
                     emp, i),
                )
    db.transaction(fn)
    return len(clean)


def save_pairings(pairings):
    """pairings: {molCode: employeeId ('' = not in payroll)}. Returns rows updated."""
    params = [(str(emp or ''), str(mol)) for mol, emp in (pairings or {}).items()]
    if not params:
        return 0
    return db.execute_many("UPDATE wps_roster SET employee_id = %s WHERE mol_code = %s", params)
