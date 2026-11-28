"""Newell Assistant: read-only tools over Newell's own books.

Each tool calls the existing service functions (the same ones the pages use)
and returns a trimmed JSON summary: totals are computed here over every
matching row, and long lists are cut to `limit` rows.

READ-ONLY, enforced twice:
  1. Only read functions are called, and there is no write tool at all.
  2. run() executes every tool inside db.read_only(), so Postgres itself
     rejects any INSERT/UPDATE/DELETE a service might do as a side effect
     (get_attendance, for one, seeds a month's rows, which is why the
     attendance tool reads the table directly instead).

Zoho Books is not here: its sync scripts live only in execution/ and are
backfill-only.
Read directives/accounting_assistant.md before changing this.
"""

import json
import logging
import re
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import db
import employee_attendance_service
import job_order_records_service
import lpo_records_service
import new_bpv_service
import payment_cert_service
import project_service
import supplier_ledger_service

log = logging.getLogger(__name__)

MAX_CHARS = 40000
DEFAULT_LIMIT = 40
MAX_LIMIT = 150

_bpv = new_bpv_service.NewBPVService()
_emp = employee_attendance_service.EmployeeAttendanceService()


class ToolInputError(ValueError):
    """A bad or unmatched input; the message goes back to Claude as is."""


# ---------------------------------------------------------------------------
# Tool definitions (fixed order: part of the cached prompt prefix)
# ---------------------------------------------------------------------------

_LIMIT = {"type": "integer", "description": f"Max rows to list (default {DEFAULT_LIMIT}, max {MAX_LIMIT}). Totals always cover every match."}
_DATE = {"type": "string", "description": "YYYY-MM-DD"}

DATA_TOOLS = [
    {
        "name": "newell_bpv_payments",
        "description": (
            "Newell's bank payment vouchers (BPV): every cheque payment line, with its PDC/CDC type and "
            "cheque status (PDC: Released / Not Released; CDC: Cleared / Pending). Use it for what was paid "
            "to whom and when, cheques due or not yet released, and payment totals. Filters combine (AND). "
            "Dates in the app are free text; date_iso is the app's own reading of it."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Words to find in the company or description"},
                "company": {"type": "string", "description": "Part of the payee name"},
                "bpv_no": {"type": "string"},
                "cheque_no": {"type": "string"},
                "date_from": {**_DATE, "description": "Voucher date from (YYYY-MM-DD)"},
                "date_to": {**_DATE, "description": "Voucher date to (YYYY-MM-DD)"},
                "cheque_date_from": {**_DATE, "description": "Cheque date from (YYYY-MM-DD)"},
                "cheque_date_to": {**_DATE, "description": "Cheque date to (YYYY-MM-DD)"},
                "type": {"type": "string", "enum": ["PDC", "CDC", "any"]},
                "cheque_status": {"type": "string", "enum": ["Released", "Not Released", "Cleared", "Pending", "any"]},
                "project_code": {"type": "string", "description": "e.g. S108"},
                "limit": _LIMIT,
            },
        },
    },
    {
        "name": "newell_tax_invoices",
        "description": (
            "The log of supplier TAX INVOICES Newell received (purchases, i.e. input VAT), per month: "
            "net, VAT and gross per invoice, with totals by month and by supplier, and rows whose VAT is not "
            "5% of the net. Not sales: Newell's own invoices to clients are in newell_projects."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "month_from": {"type": "integer", "description": "1-12 (default 1)"},
                "month_to": {"type": "integer", "description": "1-12 (default: month_from if given, else 12)"},
                "company": {"type": "string", "description": "Part of the supplier name"},
                "limit": _LIMIT,
            },
            "required": ["year"],
        },
    },
    {
        "name": "newell_employees",
        "description": (
            "Newell's employees with their pay rates: labour (AED per day and per hour) and staff (monthly "
            "salary and standing deductions). No joining dates, passport or bank details are kept."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["labour", "staff", "all"]},
                "search": {"type": "string", "description": "Name, designation or employee id"},
            },
        },
    },
    {
        "name": "newell_attendance",
        "description": (
            "Attendance for one month: per employee, the count of days by status (Present, Absent, Sick, "
            "Leave, Cancelled, Resigned...) and any leaving date. With 3 or fewer employees matched, also the "
            "day-by-day grid. Days after a Cancelled/Resigned cell are not paid."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "month": {"type": "integer", "description": "1-12"},
                "type": {"type": "string", "enum": ["labour", "staff", "all"]},
                "employee": {"type": "string", "description": "Name or employee id"},
            },
            "required": ["year", "month"],
        },
    },
    {
        "name": "newell_payroll",
        "description": (
            "The SAVED payroll for one month (salaries run a month behind): per employee paid days, salary, "
            "OT hours and pay (weekday OT and Sunday OT), bonus, deductions with reasons, net and cash/bank, "
            "with totals. Also the Month-End entries (OT, bonus, deduction) typed for that month."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer"},
                "month": {"type": "integer", "description": "1-12"},
                "type": {"type": "string", "enum": ["labour", "staff", "all"]},
                "employee": {"type": "string", "description": "Name or employee id"},
            },
            "required": ["year", "month"],
        },
    },
    {
        "name": "newell_projects",
        "description": (
            "Newell's client projects (what main contractors owe Newell): contract value, certified to date, "
            "the tax invoices Newell issued to the client (advance and interim, with advance recovery, "
            "retention and VAT), receipts, what the client still owes, retention held, and costs matched to the "
            "site. Without project: the list with headline figures."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"project": {"type": "string", "description": "Code (S108) or part of the name or client"}},
        },
    },
    {
        "name": "newell_payment_certificates",
        "description": (
            "Subcontracts Newell manages (e.g. the gas subcontractor) and every payment certificate issued to them: "
            "amount certified, retention held, VAT, net paid, and the balance of each subcontract."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"contract": {"type": "string", "description": "Part of the subcontractor, project or scope"}},
        },
    },
    {
        "name": "newell_supplier_ledger",
        "description": (
            "Accounts payable: what Newell owes each supplier, overdue amounts and ageing (0-30 / 31-60 / 61-90 / "
            "91-120 / 121+ days). Without supplier: every supplier with a balance, largest first. With supplier: "
            "its statement (open invoices, or all with open_only=false, and payments)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "supplier": {"type": "string", "description": "Part of the supplier name"},
                "as_of": {**_DATE, "description": "Balance date (default today)"},
                "open_only": {"type": "boolean", "description": "Statement: only invoices with a balance (default true)"},
                "limit": _LIMIT,
            },
        },
    },
    {
        "name": "newell_purchase_orders",
        "description": (
            "Purchase orders Newell issued: LPOs to suppliers and Job Orders to subcontractors, with totals. "
            "Give number to see one document's line items."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["lpo", "job_order", "both"]},
                "search": {"type": "string", "description": "Number, supplier/subcontractor or project words"},
                "date_from": _DATE,
                "date_to": _DATE,
                "number": {"type": "string", "description": "One LPO / Job Order number, for its line items"},
                "limit": _LIMIT,
            },
        },
    },
]

NAMES = {t["name"] for t in DATA_TOOLS}

STATUS_TEXT = {
    "newell_bpv_payments": "Looking at BPV payments",
    "newell_tax_invoices": "Looking at the tax invoice log",
    "newell_employees": "Looking at employees",
    "newell_attendance": "Looking at attendance",
    "newell_payroll": "Looking at payroll",
    "newell_projects": "Looking at projects",
    "newell_payment_certificates": "Looking at payment certificates",
    "newell_supplier_ledger": "Looking at the supplier ledger",
    "newell_purchase_orders": "Looking at LPOs and job orders",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _money(v):
    try:
        return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _f(d):
    return float(d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _text(v, n=200):
    s = " ".join(str(v or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _norm(v):
    return re.sub(r"[^a-z0-9]+", " ", str(v or "").lower()).strip()


def _has(haystack, needle):
    n = _norm(needle)
    return not n or n in _norm(haystack)


def _limit(inp):
    try:
        n = int(inp.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        n = DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, n))


def _iso(value, field):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise ToolInputError(f"{field} must be YYYY-MM-DD")


def _month(inp, field="month"):
    try:
        m = int(inp.get(field))
    except (TypeError, ValueError):
        raise ToolInputError(f"{field} must be 1-12")
    if not 1 <= m <= 12:
        raise ToolInputError(f"{field} must be 1-12")
    return m


def _year(inp):
    try:
        y = int(inp.get("year"))
    except (TypeError, ValueError):
        raise ToolInputError("year is required, e.g. 2026")
    if not 2000 <= y <= 2100:
        raise ToolInputError("year looks wrong")
    return y


def _three(text):
    parts = str(text or "").strip().split("/")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None


def _make(y, m, d):
    try:
        return date(y, m, d) if 1900 <= y <= 2999 else None
    except ValueError:
        return None


def cheque_date(text):
    """The app's rule (bpv-frontend/src/utils/chequeDate.js): day-first only
    when the first number is over 12, otherwise month-first."""
    p = _three(text)
    if not p:
        return None
    a, b, y = p
    return _make(y, b, a) if a > 12 else _make(y, a, b)


# Month-first voucher dates (see the cheque-date memory / fix_cheque_date_format.py):
# up to BPV #218, and the RANEENCO batches #233 and #254-#260.
_MONTH_FIRST_BPVS = {233, *range(254, 261)}


def voucher_date(text, bpv_no):
    """Voucher dates are free text: day-first since BPV #219 (except the
    RANEENCO batches), month-first before. Unambiguous dates win."""
    p = _three(text)
    if not p:
        return None
    a, b, y = p
    if a > 12:
        return _make(y, b, a)
    if b > 12:
        return _make(y, a, b)
    try:
        n = int(str(bpv_no).strip())
    except ValueError:
        n = None
    month_first = n is not None and (n <= 218 or n in _MONTH_FIRST_BPVS)
    return _make(y, a, b) if month_first else _make(y, b, a)


def _in_range(d, lo, hi):
    if lo is None and hi is None:
        return True
    if d is None:
        return False
    return (lo is None or d >= lo) and (hi is None or d <= hi)


def _top(groups, n=15):
    rows = sorted(groups.items(), key=lambda kv: kv[1]["total"], reverse=True)
    return [{"name": k, "count": v["count"], "total": _f(v["total"])} for k, v in rows[:n]]


def _add(groups, key, amount):
    g = groups.setdefault(key or "(blank)", {"count": 0, "total": Decimal(0)})
    g["count"] += 1
    g["total"] += amount


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

def bpv_payments(inp):
    lo, hi = _iso(inp.get("date_from"), "date_from"), _iso(inp.get("date_to"), "date_to")
    clo, chi = _iso(inp.get("cheque_date_from"), "cheque_date_from"), _iso(inp.get("cheque_date_to"), "cheque_date_to")
    kind = (inp.get("type") or "any").upper()
    status_filter = inp.get("cheque_status") or "any"
    pdc, cdc = _bpv.get_pdc_statuses(), _bpv.get_cdc_statuses()

    lines = []
    for v in _bpv.get_all_vouchers():
        for item in v["lineItems"]:
            t = (v.get("pdcType") or "PDC").upper()
            cheque = str(item.get("chequeNo") or "").strip()
            status = (cdc.get(cheque) or "Pending") if t == "CDC" else (pdc.get(cheque) or "Not Released")
            lines.append({
                "bpv_no": v["bpvNo"], "date": v.get("date") or "", "type": t,
                "company": (item.get("companyName") or "").strip(), "description": item.get("description") or "",
                "cheque_no": cheque, "cheque_date": item.get("chequeDate") or "",
                "amount": _money(item.get("debit")), "credit": _money(item.get("credit")),
                "project_code": item.get("projectCode") or "", "cheque_status": status, "source": "BPV",
            })
    for m in _bpv.get_manual_pdcs():
        lines.append({
            "bpv_no": "", "date": "", "type": "PDC", "company": m.get("company") or "",
            "description": m.get("description") or "", "cheque_no": str(m.get("chequeNo") or ""),
            "cheque_date": m.get("chequeDate") or "", "amount": _money(m.get("amount")), "credit": Decimal(0),
            "project_code": "", "cheque_status": m.get("status") or "Not Released", "source": "manual PDC",
        })

    matched, groups, months = [], {}, {}
    unreadable = 0
    for ln in lines:
        d = voucher_date(ln["date"], ln["bpv_no"]) if ln["bpv_no"] else None
        cd = cheque_date(ln["cheque_date"])
        if inp.get("search") and not (_has(ln["company"], inp["search"]) or _has(ln["description"], inp["search"])):
            continue
        if inp.get("company") and not _has(ln["company"], inp["company"]):
            continue
        if inp.get("bpv_no") and ln["bpv_no"] != str(inp["bpv_no"]).strip():
            continue
        if inp.get("cheque_no") and ln["cheque_no"].lstrip("0") != str(inp["cheque_no"]).strip().lstrip("0"):
            continue
        if kind in ("PDC", "CDC") and ln["type"] != kind:
            continue
        if status_filter != "any" and ln["cheque_status"] != status_filter:
            continue
        if inp.get("project_code") and _norm(ln["project_code"]).replace(" ", "") != _norm(inp["project_code"]).replace(" ", ""):
            continue
        if not _in_range(d, lo, hi) or not _in_range(cd, clo, chi):
            continue
        if (ln["date"] and ln["bpv_no"] and d is None) or (ln["cheque_date"] and cd is None):
            unreadable += 1
        ln["date_iso"] = d.isoformat() if d else None
        ln["cheque_date_iso"] = cd.isoformat() if cd else None
        matched.append(ln)
        _add(groups, ln["company"], ln["amount"])
        _add(months, (d or cd).strftime("%Y-%m") if (d or cd) else "(no date)", ln["amount"])

    matched.sort(key=lambda x: (x["date_iso"] or x["cheque_date_iso"] or ""), reverse=True)
    total = sum((x["amount"] for x in matched), Decimal(0))
    by_status = {}
    for x in matched:
        _add(by_status, f"{x['type']} {x['cheque_status']}", x["amount"])
    n = _limit(inp)
    rows = []
    for x in matched[:n]:
        row = {k: x[k] for k in ("bpv_no", "date", "date_iso", "type", "company", "cheque_no", "cheque_date",
                                 "cheque_date_iso", "cheque_status")}
        row["description"] = _text(x["description"])
        row["amount"] = _f(x["amount"])
        if x["credit"]:
            row["credit"] = _f(x["credit"])
        if x["project_code"]:
            row["project_code"] = x["project_code"]
        if x["source"] != "BPV":
            row["source"] = x["source"]
        rows.append(row)
    notes = []
    if unreadable:
        notes.append(f"{unreadable} matching line(s) have a date the app cannot read; they are listed with date_iso null.")
    payload = {
        "page": "/bpv (payments), /pdc and /cdc (cheques)",
        "matched_lines": len(matched), "total_amount": _f(total),
        "by_status": _top(by_status), "by_company": _top(groups), "by_month": sorted(_top(months, 36), key=lambda r: r["name"]),
        "lines": rows, "truncated": len(matched) > n, "notes": notes,
    }
    return payload, f"{len(matched)} lines, total AED {_f(total):,.2f}"


def tax_invoices(inp):
    year = _year(inp)
    m_from = _month(inp, "month_from") if inp.get("month_from") else 1
    m_to = _month(inp, "month_to") if inp.get("month_to") else (m_from if inp.get("month_from") else 12)
    if m_to < m_from:
        raise ToolInputError("month_to is before month_from")
    rows, months, groups, mismatches = [], {}, {}, []
    tot = {"net": Decimal(0), "vat": Decimal(0), "gross": Decimal(0)}
    for m in range(m_from, m_to + 1):
        mt = {"count": 0, "net": Decimal(0), "vat": Decimal(0), "gross": Decimal(0)}
        for r in _bpv.get_tax_invoices(m - 1, year):
            if inp.get("company") and not _has(r["company"], inp["company"]):
                continue
            net, vat, gross = _money(r["amountNoVat"]), _money(r["vat"]), _money(r["amountWithVat"])
            for k, v in (("net", net), ("vat", vat), ("gross", gross)):
                mt[k] += v
                tot[k] += v
            mt["count"] += 1
            _add(groups, (r["company"] or "").strip(), vat)
            row = {"month": f"{year}-{m:02d}", "invoice_date": r["invoiceDate"], "company": r["company"],
                   "invoice_number": r["invoiceNumber"], "net": _f(net), "vat": _f(vat), "gross": _f(gross)}
            rows.append(row)
            expected = (net * Decimal("0.05")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if abs(vat - expected) > Decimal("0.05") or abs(net + vat - gross) > Decimal("0.05"):
                mismatches.append({**row, "vat_at_5pct": _f(expected)})
        months[f"{year}-{m:02d}"] = {"count": mt["count"], "net": _f(mt["net"]), "vat": _f(mt["vat"]), "gross": _f(mt["gross"])}
    n = _limit(inp)
    payload = {
        "page": "/tax-invoices",
        "what": "Supplier tax invoices received (input VAT), as logged by the office",
        "period": f"{year}-{m_from:02d} to {year}-{m_to:02d}",
        "count": len(rows), "total_net": _f(tot["net"]), "total_vat": _f(tot["vat"]), "total_gross": _f(tot["gross"]),
        "by_month": months,
        "by_supplier_vat": _top(groups),
        "rows_not_at_5pct_or_not_adding_up": {"count": len(mismatches), "rows": mismatches[:15]},
        "invoices": rows[:n], "truncated": len(rows) > n,
    }
    return payload, f"{len(rows)} invoices, net AED {_f(tot['net']):,.2f}, VAT AED {_f(tot['vat']):,.2f}"


def _type(inp):
    t = (inp.get("type") or "all").lower()
    if t not in ("labour", "staff", "all"):
        raise ToolInputError("type must be labour, staff or all")
    return t


def _emp_match(name, emp_id, designation, needle):
    if not needle:
        return True
    needle = str(needle).strip()
    return str(emp_id).strip() == needle or _has(name, needle) or _has(designation, needle)


def employees(inp):
    t = _type(inp)
    out = []
    for e in _emp.get_employees(None if t == "all" else t):
        if not _emp_match(e["name"], e["id"], e.get("designation"), inp.get("search")):
            continue
        row = {"id": e["id"], "name": e["name"], "designation": e.get("designation") or "", "type": e["sheetType"]}
        if e["sheetType"] == "staff":
            row.update(monthly_salary=e.get("netSalary"), rate_per_day=e.get("ratePerDay"), deductions=e.get("deductions"))
        else:
            row.update(rate_per_day=e.get("ratePerDay"), rate_per_hour=e.get("ratePerHour"))
        out.append(row)
    labour = sum(1 for r in out if r["type"] == "labour")
    payload = {"page": "/employees, /salaries", "count": len(out), "labour": labour, "staff": len(out) - labour, "employees": out}
    return payload, f"{len(out)} employees ({labour} labour)"


def attendance(inp):
    year, month = _year(inp), _month(inp)
    t = _type(inp)
    # Read directly: _emp.get_attendance() seeds missing rows first (a write).
    rows = db.q(
        "SELECT * FROM attendance WHERE year = %s AND month = %s ORDER BY "
        + employee_attendance_service._EMP_ID_ORDER, (year, month))
    ends = _emp._end_dates()
    picked = [r for r in rows if (t == "all" or (r["type"] or "").lower() == t)
              and _emp_match(r["name"], r["employee_id"], r.get("designation"), inp.get("employee"))]
    if not picked and not rows:
        return {"page": "/attendance", "note": f"No attendance has been entered for {year}-{month:02d}."}, "no attendance"
    totals, people = {}, []
    for r in picked:
        end = ends.get(r["employee_id"])
        cutoff = employee_attendance_service.EmployeeAttendanceService._cutoff_day(end, year, month)
        counts, grid = {}, {}
        for d in range(1, 32):
            if _make(year, month, d) is None:
                continue
            val = (r.get(f"day{d}") or "").strip() or "(not marked)"
            counts[val] = counts.get(val, 0) + 1
            totals[val] = totals.get(val, 0) + 1
            grid[str(d)] = val
        person = {"id": r["employee_id"], "name": r["name"], "designation": r.get("designation") or "",
                  "type": r["type"], "days": counts}
        if end:
            person["left"] = {"date": end["date"].isoformat(), "reason": end["reason"],
                              "days_counted_this_month": ("none" if cutoff == 0 else f"1-{cutoff - 1}" if cutoff else "all")}
        people.append(person)
    if len(people) <= 3:
        for p, r in zip(people, picked):
            p["grid"] = {d: (r.get(f"day{d}") or "").strip() for d in range(1, 32) if (r.get(f"day{d}") or "").strip()}
    payload = {"page": "/attendance", "month": f"{year}-{month:02d}", "employees": len(people),
               "day_totals": totals, "people": people}
    return payload, f"{len(people)} employees for {year}-{month:02d}"


def payroll(inp):
    year, month = _year(inp), _month(inp)
    t = _type(inp)
    payload = {"page": "/payroll (saved), /month-end (OT, bonus, deductions)", "month": f"{year}-{month:02d}"}
    heads = []
    for kind in (("labour", "staff") if t == "all" else (t,)):
        rows, msg = _emp.get_payroll(month - 1, year, kind)
        rows = [r for r in rows if _emp_match(r["name"], r["employeeId"], r.get("designation"), inp.get("employee"))]
        if msg or not rows:
            payload[kind] = {"saved": False, "note": msg or "No matching employee in the saved payroll"}
            continue
        tot = {k: Decimal(0) for k in ("net", "ot_pay", "bonus", "deductions", "cash", "bank")}
        out = []
        for r in rows:
            net = _money(r["netSalary"])
            tot["net"] += net
            tot["ot_pay"] += _money(r["otPay"])
            tot["bonus"] += _money(r["bonus"])
            tot["deductions"] += _money(r["deductionAmount"])
            tot["cash" if r["isCash"] else "bank"] += net
            row = {"id": r["employeeId"], "name": r["name"], "designation": r.get("designation") or "",
                   "paid_days": r["paidDays"], "rate_per_hour": r["ratePerHour"],
                   "ot_hours": r["otHours"], "sunday_ot_hours": r["sundayOtHours"], "ot_pay": r["otPay"],
                   "bonus": r["bonus"], "deduction": r["deductionAmount"], "net": r["netSalary"],
                   "payment": r["paymentMethod"]}
            if "salaryBeforeOT" in r:
                row["salary_before_ot"] = r["salaryBeforeOT"]
            for k_out, k_in in (("bonus_reason", "bonusReason"), ("deduction_reason", "deductionReason"),
                                ("deduction_note", "deductionNote")):
                if r.get(k_in):
                    row[k_out] = r[k_in]
            out.append(row)
        payload[kind] = {"saved": True, "count": len(out), **{f"total_{k}": _f(v) for k, v in tot.items()}, "rows": out}
        heads.append(f"{kind} {len(out)} net AED {_f(tot['net']):,.2f}")
    entries = []
    for m in _emp.get_month_end(month - 1, year):
        if t != "all" and m["type"] != t:
            continue
        if not _emp_match(m["name"], m["id"], m.get("designation"), inp.get("employee")):
            continue
        if any(m.get(k) for k in ("otHours", "sundayOtHours", "bonus", "deductionAmount")):
            entries.append({k: m[k] for k in ("id", "name", "type", "otHours", "sundayOtHours", "otSource", "bonus",
                                             "bonusReason", "deductionAmount", "deductionReason", "deductionNote")
                            if m.get(k) not in (None, "", 0)})
    payload["month_end_entries"] = entries
    return payload, "; ".join(heads) or "payroll not saved"


def _pick(items, needle, fields, what):
    if not needle:
        return None
    hits = [i for i in items if any(_has(i.get(f), needle) for f in fields)
            or _norm(needle).replace(" ", "") in [_norm(i.get(f)).replace(" ", "") for f in fields]]
    if not hits:
        raise ToolInputError(f"No {what} matches '{needle}'. Known: " + "; ".join(str(i.get(fields[0])) for i in items[:30]))
    if len(hits) > 1:
        raise ToolInputError(f"Several {what}s match '{needle}': " + "; ".join(str(i.get(fields[0])) for i in hits[:10]) + ". Be more specific.")
    return hits[0]


def projects(inp):
    projs = project_service.list_projects()
    one = _pick(projs, inp.get("project"), ("code", "name", "client", "invoice_title"), "project")
    if one is None:
        out = []
        for p in projs:
            s = project_service.summary(p["id"])
            out.append({"code": p["code"], "name": p["name"], "client": p["client"], "summary": s})
        return {"page": "/projects", "projects": out}, f"{len(out)} projects"
    ov = project_service.overview(one["id"])
    invoices = []
    for i in ov["invoices"]:
        invoices.append({k: i.get(k) for k in (
            "invoice_no", "ref_number", "invoice_date", "kind", "client_pc", "cumulative_work", "current_work",
            "cumulative_pct", "advance_recovery", "retention", "prev_certified", "total_ex_vat", "vat", "payable",
            "received", "balance", "note") if i.get(k) not in (None, "")})
    c = ov["costs"]

    def bucket(items):
        return {"count": len(items), "total": _f(sum((_money(x.get("amount")) for x in items), Decimal(0)))}

    bpv_in = c["bpv"].get("included", [])
    costs = {
        "bpv_payments_included": {**bucket(bpv_in), "lines": [
            {k: x.get(k) for k in ("bpv_no", "date", "company", "cheque_no", "amount")} for x in bpv_in[:30]]},
        "security_cheques": bucket(c["bpv"].get("security", [])),
        "bpv_excluded": bucket(c["bpv"].get("excluded", [])),
        "lpos_included": bucket(c["lpo"].get("included", [])),
        "job_orders_included": bucket(c["job_order"].get("included", [])),
        "suggestions_awaiting_review": len(c.get("review", [])),
    }
    payload = {
        "page": "/projects",
        "project": {k: ov["project"].get(k) for k in ("code", "name", "client", "client_trn", "contract_value",
                                                       "advance_recovery_pct", "retention_pct", "vat_pct")},
        "summary": ov["summary"],
        "client_invoices": invoices,
        "unallocated_receipts": ov.get("unallocated_receipts") or [],
        "costs": costs,
        "rules": "Spend counts confirmed BPV payments only; LPOs and job orders are 'ordered', never added "
                 "(the BPV that pays an LPO is the same money). Security cheques are not cost.",
    }
    return payload, f"{one['code']}: owed by client AED {ov['summary'].get('owed_by_client', 0):,.2f}"


def payment_certificates(inp):
    contracts = payment_cert_service.list_contracts(include_archived=True)
    one = _pick(contracts, inp.get("contract"), ("subcontractor", "project", "project_code", "scope", "job_order_ref"), "subcontract")
    out = []
    for c in ([one] if one else contracts):
        certs = payment_cert_service.list_certificates(c["id"])
        state = payment_cert_service.cumulative_state(c)
        out.append({
            "subcontractor": c["subcontractor"], "project": c["project"], "project_code": c["project_code"],
            "scope": c["scope"], "job_order_ref": c["job_order_ref"], "contract_value": c["contract_value"],
            "retention_percent": c["retention_percent"], "vat_percent": c["vat_percent"], "archived": c["archived"],
            "position": {k: (_f(v) if isinstance(v, Decimal) else v) for k, v in state.items()},
            "certificates": [{k: x.get(k) for k in (
                "cert_no", "ref_number", "bill_month", "invoice_date", "bill_type", "amount_ex_vat",
                "retention_amount", "other_deduction", "other_deduction_note", "total_amount", "vat_charged",
                "net_payable") if x.get(k) not in (None, "")} for x in certs],
        })
    return ({"page": "/payment-cert",
             "how": "The office types the cash paid (net payable); everything above it is worked backwards.",
             "subcontracts": out}, f"{len(out)} subcontracts")


_SUPPLIER_FIELDS = ("name", "outstanding", "overdue", "dueSoon", "openCount", "overdueCount", "lastPayment",
                    "nextDue", "paymentTermsDays", "unallocated", "ageing")


def supplier_ledger(inp):
    as_of = _iso(inp.get("as_of"), "as_of")
    s = supplier_ledger_service.summary(as_of.isoformat() if as_of else None)
    suppliers = s["suppliers"]
    n = _limit(inp)
    if not inp.get("supplier"):
        owing = sorted((x for x in suppliers if x.get("outstanding")), key=lambda x: x["outstanding"], reverse=True)
        total = sum((_money(x["outstanding"]) for x in suppliers), Decimal(0))
        overdue = sum((_money(x["overdue"]) for x in suppliers), Decimal(0))
        ageing = {}
        for x in suppliers:
            for k, v in (x.get("ageing") or {}).items():
                ageing[k] = ageing.get(k, Decimal(0)) + _money(v)
        payload = {"page": "/supplier-ledger", "as_of": s["asOf"], "suppliers": len(suppliers),
                   "total_outstanding": _f(total), "total_overdue": _f(overdue),
                   "ageing": {k: _f(v) for k, v in ageing.items()},
                   "with_balance": [{k: x.get(k) for k in _SUPPLIER_FIELDS} for x in owing[:n]],
                   "truncated": len(owing) > n,
                   "note": "Standalone ledger: payments are entered here, not read from BPV."}
        return payload, f"owed AED {_f(total):,.2f} to {len(owing)} suppliers"
    match = supplier_ledger_service.match_supplier(inp["supplier"])
    if match is None:
        match = _pick(suppliers, inp["supplier"], ("name", "code"), "supplier")
    st = supplier_ledger_service.statement(match["id"], as_of.isoformat() if as_of else None)
    invoices = st["invoices"]
    if inp.get("open_only", True):
        invoices = [i for i in invoices if i.get("balance")]
    payload = {
        "page": "/supplier-ledger", "as_of": st["asOf"],
        "supplier": {k: st["supplier"].get(k) for k in ("name", "code", "paymentTermsDays", "trn")},
        "totals": st["totals"],
        "invoices": [{k: i.get(k) for k in ("invoiceDate", "dueDate", "reference", "description", "amount", "credit",
                                            "paid", "balance", "status", "ageDays", "daysOverdue")} for i in invoices[:n]],
        "invoices_listed": min(len(invoices), n), "invoices_matching": len(invoices),
        "payments": st["payments"][:n],
    }
    return payload, f"{st['supplier']['name']}: outstanding AED {st['totals'].get('outstanding', 0):,.2f}"


def purchase_orders(inp):
    kind = (inp.get("kind") or "both").lower()
    lo, hi = _iso(inp.get("date_from"), "date_from"), _iso(inp.get("date_to"), "date_to")
    lo_s, hi_s = (lo.isoformat() if lo else ""), (hi.isoformat() if hi else "")
    n = _limit(inp)
    sources = []
    if kind in ("lpo", "both"):
        sources.append(("lpo", lpo_records_service, "lpo_number", "lpo_date", "supplier_name"))
    if kind in ("job_order", "both"):
        sources.append(("job_order", job_order_records_service, "ref_number", "jo_date", "subcontractor_name"))
    if not sources:
        raise ToolInputError("kind must be lpo, job_order or both")

    number = _norm(inp.get("number")).replace(" ", "")
    payload = {"page": "/lpo, /job-order (History)"}
    heads = []
    for name, svc, num_f, date_f, party_f in sources:
        records = svc.list_records("", lo_s, hi_s)
        if number:
            hit = next((r for r in records if _norm(r[num_f]).replace(" ", "") == number
                        or _norm(r[num_f]).replace(" ", "").endswith(number)), None)
            if hit:
                full = svc.get_record(hit["id"])
                payload["document"] = {"kind": name, **{k: v for k, v in full.items()
                                                       if k not in ("id", "created_at", "updated_at")}}
                return payload, f"{name} {hit[num_f]}"
            continue
        if inp.get("search"):
            records = [r for r in records if any(_has(r.get(f), inp["search"]) for f in (num_f, party_f, "project"))]
        total = sum((_money(r.get("total_amount")) for r in records), Decimal(0))
        groups = {}
        for r in records:
            _add(groups, (r.get(party_f) or "").strip(), _money(r.get("total_amount")))
        payload[name] = {"count": len(records), "total": _f(total), "by_party": _top(groups),
                         "records": [{"number": r[num_f], "date": str(r.get(date_f) or ""), "party": r.get(party_f),
                                      "project": _text(r.get("project"), 90), "total": _f(_money(r.get("total_amount")))}
                                     for r in records[:n]],
                         "truncated": len(records) > n}
        heads.append(f"{name} {len(records)} AED {_f(total):,.2f}")
    if number:
        what = {"lpo": "LPO", "job_order": "job order"}.get(kind, "LPO or job order")
        raise ToolInputError(f"No {what} numbered '{inp['number']}'")
    payload["note"] = "Ordered amounts, not payments (payments are BPVs). A job order total of 0 means it has no totals shown."
    return payload, "; ".join(heads)


_HANDLERS = {
    "newell_bpv_payments": bpv_payments,
    "newell_tax_invoices": tax_invoices,
    "newell_employees": employees,
    "newell_attendance": attendance,
    "newell_payroll": payroll,
    "newell_projects": projects,
    "newell_payment_certificates": payment_certificates,
    "newell_supplier_ledger": supplier_ledger,
    "newell_purchase_orders": purchase_orders,
}


def _fit(payload):
    """JSON text under MAX_CHARS: shorten the longest list until it fits."""
    text = json.dumps(payload, default=str, ensure_ascii=False)
    for _ in range(12):
        if len(text) <= MAX_CHARS:
            return text
        longest, parent, key = None, None, None
        stack = [payload]
        while stack:
            node = stack.pop()
            items = node.items() if isinstance(node, dict) else []
            for k, v in items:
                if isinstance(v, list) and len(v) > 5 and (longest is None or len(v) > len(longest)):
                    longest, parent, key = v, node, k
                if isinstance(v, dict):
                    stack.append(v)
        if longest is None:
            break
        parent[key] = longest[: len(longest) // 2]
        parent["truncated"] = True
        text = json.dumps(payload, default=str, ensure_ascii=False)
    return text[:MAX_CHARS]


def run(name, inp):
    """Run one data tool read-only. Returns (text, headline, is_error)."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool {name}", "", True
    inp = inp if isinstance(inp, dict) else {}
    try:
        with db.read_only():
            payload, headline = handler(inp)
    except ToolInputError as e:
        return f"Error: {e}", "", True
    except Exception as e:
        if "read-only transaction" in str(e):
            log.error("assistant data tool %s tried to write; blocked by read_only()", name)
        else:
            log.exception("assistant data tool %s failed", name)
        return "Error: Newell's data couldn't be read just now. Say so rather than guessing the figures.", "", True
    return _fit(payload), headline, False
