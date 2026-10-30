"""
Labour daily site log — the digital replacement for the paper TIME SHEET
ATTENDANCE that site engineers used to fill by hand.

Table (see schema_supabase.sql): labour_day_log — one row per labour per day,
holding the same three things the paper sheet held: the site(s) worked, OT
hours, and the day's status (see STATUSES).

Who writes it: the site engineers, from their phones, at
/crew/<employees.checkin_token> — the same unguessable token as the Time Log
check-in link (this app has no login system). Only employees flagged
`time_log_enabled` may open the crew sheet.

Engineers pick from the FULL labour list rather than a fixed crew, because
labours move between engineers day to day. Two engineers can therefore mark the
same man on the same day: the unique (employee_id, entry_date) constraint makes
the second save overwrite the first and raise `conflict`, which the office sees
on the Site Log page.

Saving also keeps two existing places in sync, so nothing has to be re-typed:
  * `attendance`.day{N} — Present/Absent, the grid on the /attendance page
  * `month_end`.ot_hours — the month's OT total that drives payroll, but ONLY
    while month_end.ot_source <> 'manual'. The moment the office types an OT
    value on the Month-End page that employee/month is pinned to 'manual' and
    this module stops touching it. Office edits win.
"""

from datetime import date, datetime, timedelta, timezone

from psycopg2.extras import execute_values

import db

# The company works in the UAE but the backend runs on Railway in UTC, so
# date.today() there is 4 hours behind: between midnight and 4am local the
# server would hand the engineer YESTERDAY's sheet and reject today's date as
# "in the future". The Gulf has no DST, so a fixed +04:00 offset is exact.
UAE_TZ = timezone(timedelta(hours=4))


def today():
    return datetime.now(UAE_TZ).date()

# Statuses the site log can hold. These are stored verbatim into
# attendance.day{N}, so they must match employee_attendance_service.
# CANONICAL_STATUSES casing.
#
# 'Resigned' is shown to the office as "Left company". It is TERMINAL in
# employee_attendance_service: the day itself is not paid and nothing after it
# counts, in this month or any later one.
#
# 'Off' is the Sunday rest day, the same value the /attendance page fills
# Sundays with. A man who comes in on a Sunday is logged as Off + OT ("OT
# only" on the phone): the day stays the paid rest day it always was, and the
# hours he worked count purely as overtime — no extra 8-hour normal day.
STATUSES = ('Present', 'Absent', 'Leave', 'Sick', 'Off', 'Joined', 'Resigned')

# The subset an engineer may set from his phone. Joined/Resigned are HR events
# the office owns, not something an engineer observes on site — and a mis-tap
# on Resigned would wipe the rest of the man's month.
ENGINEER_STATUSES = ('Present', 'Absent', 'Leave', 'Sick', 'Off')

# How many days back an engineer may still edit. Engineers forget to fill the
# sheet on site, but a window that never closes would let them rewrite a month
# that has already been paid.
EDIT_WINDOW_DAYS = 3

# A normal working day. Hours are DERIVED from the status rather than typed:
# labour is paid per day plus OT per hour, which is why the WORKING HOUR column
# on the paper sheet was always left blank. The engineers only ever record the
# OT on top of this.
STANDARD_DAY_HOURS = 8

# Statuses that earn a full day's hours. This mirrors isPaidDay() in the
# frontend's payrollCalculation.js — keep the two in step.
#
# Deliberately an ALLOWLIST, not "everything except Absent": a status added to
# STATUSES later must opt IN to being paid, or it silently books a man 8 hours
# for a day he did not work.
PAID_STATUSES = ('Present', 'Off', 'Sick')

# Statuses that put a normal 8-hour day on the site sheet. Off is paid (above)
# but is the rest day, so a Sunday worked as OT shows 0 normal hours and is
# not counted as a present day.
DAY_HOURS_STATUSES = ('Present', 'Sick')

# Only a man who actually worked can have overtime on top of the day. Anything
# else (absent, on leave, sick, joined mid-month, left) earns no OT, so stale
# values from a mis-tap never ride along into payroll. Off carries OT because
# that is exactly what a Sunday worked is.
OT_STATUSES = ('Present', 'Off')


def hours_for(status):
    return STANDARD_DAY_HOURS if status in DAY_HOURS_STATUSES else 0


def earns_ot(status):
    return status in OT_STATUSES


def _num(val):
    if val is None or val == '':
        return 0
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0


def _normalize_status(status, allowed=STATUSES):
    """Canonical casing for a status, restricted to `allowed`.

    Anything unrecognised falls back to Present, which is also how the phone is
    prevented from setting an office-only status: the crew sheet save passes
    ENGINEER_STATUSES, so a hand-crafted request asking for Resigned lands on
    Present rather than silently ending a man's employment.
    """
    for s in allowed:
        if str(status or '').strip().lower() == s.lower():
            return s
    return 'Present'


def parse_date(value):
    """'YYYY-MM-DD' -> date. Returns None if unparseable."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def date_in_window(entry_date, ref_date=None):
    """The engineer may fill today and the previous EDIT_WINDOW_DAYS days."""
    ref_date = ref_date or today()
    return (ref_date - timedelta(days=EDIT_WINDOW_DAYS)) <= entry_date <= ref_date


# ---------------------------------------------------------------------------
# Engineer-facing (token-scoped)
# ---------------------------------------------------------------------------

def get_engineer_by_token(token):
    """Who is behind a crew-sheet link.

    Gated on `crew_sheet_enabled`, NOT on time_log_enabled: filling the labour
    sheet and checking yourself in are different jobs done by overlapping but
    different people (a foreman marks the crew without being on the engineers'
    check-in roster). Everyone has a checkin_token, so without this gate any
    labour's own link would open a page that edits everyone's attendance.
    """
    return db.q_one(
        """SELECT id, name, designation FROM employees
           WHERE checkin_token = %s AND crew_sheet_enabled = true""",
        (token,))


def set_crew_access(employee_id, enabled):
    """Office switches the labour sheet on/off for one person.

    Turning it on mints a checkin_token if they don't have one yet (the same
    token their /checkin link uses). Turning it off leaves the token alone —
    it stays valid for check-in, only /crew stops opening.

    Returns the row, or None if there is no such employee.
    """
    import time_entries_service as time_log

    employee_id = str(employee_id)
    row = db.q_one(
        "SELECT id, name, checkin_token FROM employees WHERE id = %s", (employee_id,))
    if row is None:
        return None

    token = row['checkin_token']
    if enabled and not token:
        token = time_log.generate_token()
        db.execute("UPDATE employees SET checkin_token = %s WHERE id = %s",
                   (token, employee_id))
    db.execute("UPDATE employees SET crew_sheet_enabled = %s WHERE id = %s",
               (bool(enabled), employee_id))
    return {'employeeId': employee_id, 'name': row['name'],
            'crewSheetEnabled': bool(enabled), 'checkinToken': token}


def list_sites():
    """Project sites for the chip picker (G+13, G+7, ...).

    Its own table, NOT lpo_sites: those are the LPO generator's material
    delivery addresses, which are a different list from the projects labour is
    assigned to. Ordered by id so the office controls the chip order by the
    order it adds them.
    """
    return [r['name'] for r in db.q("SELECT name FROM labour_sites ORDER BY id")]


def add_site(name):
    """Office adds a site. Idempotent — re-adding an existing name is a no-op."""
    name = (name or '').strip()
    if not name:
        raise ValueError('Site name required')
    db.execute(
        "INSERT INTO labour_sites (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        (name,))
    return list_sites()


def delete_site(name):
    """Office removes a site. Days already logged against it keep their text —
    `sites` is stored as plain text, so history is never rewritten."""
    db.execute("DELETE FROM labour_sites WHERE name = %s", ((name or '').strip(),))
    return list_sites()


def list_labours_for_date(entry_date, engineer_id=None):
    """Every labour, with that day's log entry if one exists.

    `markedByName` / `markedById` are what the phone shows as the amber
    "already marked by ..." warning when another engineer got there first.
    """
    rows = db.q(
        """SELECT e.id, e.name, e.designation,
                  l.id AS entry_id, l.status, l.ot_hours, l.sites, l.note,
                  l.marked_by, l.conflict, eng.name AS marked_by_name
           FROM employees e
           LEFT JOIN labour_day_log l
             ON l.employee_id = e.id AND l.entry_date = %s
           LEFT JOIN employees eng ON eng.id = l.marked_by
           WHERE lower(e.type) = 'labour'
           ORDER BY (CASE WHEN e.id ~ '^\\d+$' THEN e.id::int ELSE 2147483647 END), e.id""",
        (entry_date,))
    return [{
        'employeeId': r['id'],
        'name': r['name'],
        'designation': r['designation'] or '',
        'entryId': r['entry_id'],
        'status': r['status'],
        'otHours': _num(r['ot_hours']) if r['entry_id'] else 0,
        'sites': r['sites'] or '',
        'note': r['note'] or '',
        'markedById': r['marked_by'],
        'markedByName': r['marked_by_name'] or '',
        'isMine': bool(engineer_id and r['marked_by'] == str(engineer_id)),
        'conflict': bool(r['conflict']),
    } for r in rows]


def save_day(engineer_id, entry_date, rows):
    """Upsert the rows one engineer touched for one day, then re-sync the
    attendance grid and the month's OT total.

    rows: [{employeeId, status, otHours, sites, note}] — sites may be a list or
    an already-joined string. Returns the number of rows written.
    """
    engineer_id = str(engineer_id)
    payload = []
    for r in rows:
        emp_id = str(r.get('employeeId') or '').strip()
        if not emp_id:
            continue
        status = _normalize_status(r.get('status'), ENGINEER_STATUSES)
        sites = r.get('sites') or ''
        if isinstance(sites, (list, tuple)):
            sites = ', '.join(str(s).strip() for s in sites if str(s).strip())
        # A man who did not work the day worked no site and earned no OT —
        # don't let stale values from a mis-tap ride along into payroll.
        if not earns_ot(status):
            ot, sites = 0, ''
        else:
            ot = _num(r.get('otHours'))
        payload.append([emp_id, entry_date, status, ot, sites,
                        (r.get('note') or '').strip(), engineer_id])
    if not payload:
        return 0

    emp_ids = [p[0] for p in payload]
    year, month = entry_date.year, entry_date.month

    def fn(conn):
        with conn.cursor() as cur:
            execute_values(cur,
                """INSERT INTO labour_day_log
                     (employee_id, entry_date, status, ot_hours, sites, note, marked_by)
                   VALUES %s
                   ON CONFLICT (employee_id, entry_date) DO UPDATE SET
                     status = EXCLUDED.status,
                     ot_hours = EXCLUDED.ot_hours,
                     sites = EXCLUDED.sites,
                     note = EXCLUDED.note,
                     marked_by = EXCLUDED.marked_by,
                     -- flag only when a DIFFERENT engineer overwrites; an
                     -- engineer correcting his own entry is not a conflict
                     -- (IS DISTINCT FROM: marked_by is null once that engineer is deleted)
                     conflict = (labour_day_log.marked_by IS DISTINCT FROM EXCLUDED.marked_by),
                     updated_at = now()""",
                payload, page_size=200)
            _sync_attendance(cur, payload, entry_date)
            _sync_month_end_ot(cur, emp_ids, year, month)
    db.transaction(fn)
    return len(payload)


def clear_day(engineer_id, employee_id, entry_date):
    """Engineer undoes a labour he marked — wrong man, wrong day, mis-tap.

    Deliberately narrower than save_day's 3-day window: only TODAY can be
    cleared. A save can be corrected by saving again at any point in the
    window, but deleting a record outright is not something to allow days
    later, once the office may have acted on it.

    Returns (ok, error_message).
    """
    if entry_date != today():
        return False, 'You can only clear an entry on the same day you saved it.'

    row = db.q_one(
        "SELECT id, marked_by FROM labour_day_log WHERE employee_id = %s AND entry_date = %s",
        (str(employee_id), entry_date))
    if row is None:
        return False, 'Nothing saved for this labour today.'
    if row['marked_by'] != str(engineer_id):
        # Overwriting another engineer's entry is allowed (second save wins);
        # deleting his record is not.
        return False, 'Another engineer saved this one. Ask the office to remove it.'

    delete_entry(row['id'])
    return True, None


# ---------------------------------------------------------------------------
# Sync into the existing attendance / month_end tables
# ---------------------------------------------------------------------------

def _sync_attendance(cur, payload, entry_date):
    """Write Present/Absent into attendance.day{N} for the affected labours.

    Seeds any missing attendance row first: the /attendance page seeds a whole
    month at once the first time it is opened (employee_attendance_service.
    _ensure_month_seeded), which may not have happened yet when an engineer
    marks a day early in the month.
    """
    day = entry_date.day
    year, month = entry_date.year, entry_date.month
    emp_ids = [p[0] for p in payload]

    cur.execute(
        """INSERT INTO attendance (employee_id, year, month, type, name, designation)
           SELECT e.id, %s, %s, lower(e.type), e.name, e.designation
           FROM employees e
           WHERE e.id = ANY(%s)
           ON CONFLICT (employee_id, year, month) DO NOTHING""",
        (year, month, emp_ids))

    # One UPDATE per distinct status (at most two) instead of one per labour —
    # the database is remote, so round-trips are the cost that matters.
    by_status = {}
    for emp_id, _d, status, *_rest in payload:
        by_status.setdefault(status, []).append(emp_id)
    for status, ids in by_status.items():
        # `day` comes from a parsed date, so the column name is safe to interpolate
        cur.execute(
            f"""UPDATE attendance SET day{day} = %s
                WHERE employee_id = ANY(%s) AND year = %s AND month = %s""",
            (status, ids, year, month))


def _sync_month_end_ot(cur, emp_ids, year, month):
    """Re-total the month's OT from the log into month_end.ot_hours.

    The `WHERE month_end.ot_source <> 'manual'` on the conflict clause is what
    makes an office override permanent: once someone types an OT value on the
    Month-End page, later engineer saves leave it alone.
    """
    # Driven from the employee list rather than from the log rows: grouping the
    # log alone yields NO row for an employee whose last day was just deleted,
    # so the upsert would silently skip him and leave a stale OT total behind in
    # payroll. The LEFT JOIN guarantees one row per employee, zero included.
    cur.execute(
        """INSERT INTO month_end (employee_id, year, month, ot_hours, ot_source)
           SELECT ids.id, %s, %s, COALESCE(SUM(l.ot_hours), 0), 'log'
           FROM unnest(%s::text[]) AS ids(id)
           LEFT JOIN labour_day_log l
             ON l.employee_id = ids.id
            AND EXTRACT(YEAR FROM l.entry_date) = %s
            AND EXTRACT(MONTH FROM l.entry_date) = %s
           GROUP BY ids.id
           ON CONFLICT (employee_id, year, month) DO UPDATE SET
             ot_hours = EXCLUDED.ot_hours, ot_source = 'log'
           WHERE month_end.ot_source <> 'manual'""",
        (year, month, [str(e) for e in emp_ids], year, month))


# ---------------------------------------------------------------------------
# Office-facing (the /site-log page)
# ---------------------------------------------------------------------------

def list_month(year, month):
    """The paper sheet, rebuilt: every labour x every day of the month.

    month is 1-12 here (the API converts from the frontend's 0-indexed month,
    like the other endpoints).
    """
    year, month = int(year), int(month)
    rows = db.q(
        """SELECT e.id, e.name, e.designation,
                  l.id AS entry_id, l.entry_date, l.status, l.ot_hours,
                  l.sites, l.note, l.conflict, eng.name AS marked_by_name
           FROM employees e
           LEFT JOIN labour_day_log l
             ON l.employee_id = e.id
            AND EXTRACT(YEAR FROM l.entry_date) = %s
            AND EXTRACT(MONTH FROM l.entry_date) = %s
           LEFT JOIN employees eng ON eng.id = l.marked_by
           WHERE lower(e.type) = 'labour'
           ORDER BY (CASE WHEN e.id ~ '^\\d+$' THEN e.id::int ELSE 2147483647 END), e.id,
                    l.entry_date""",
        (year, month))

    by_emp = {}
    order = []
    for r in rows:
        emp_id = r['id']
        if emp_id not in by_emp:
            by_emp[emp_id] = {
                'employeeId': emp_id, 'name': r['name'],
                'designation': r['designation'] or '',
                'days': {}, 'totalOt': 0, 'totalHours': 0,
                'presentDays': 0, 'absentDays': 0,
                'hasConflict': False,
            }
            order.append(emp_id)
        if not r['entry_id']:
            continue
        rec = by_emp[emp_id]
        ot = _num(r['ot_hours'])
        hours = hours_for(r['status'])
        rec['days'][r['entry_date'].day] = {
            'entryId': r['entry_id'],
            'status': r['status'],
            'hours': hours,
            'otHours': ot,
            'sites': r['sites'] or '',
            'note': r['note'] or '',
            'conflict': bool(r['conflict']),
            'markedByName': r['marked_by_name'] or '',
        }
        rec['totalOt'] += ot
        rec['totalHours'] += hours
        # Absent is the only status the absence penalty in payroll charges for,
        # and only a normal working day counts as present — a Leave, Joined,
        # Resigned or Sunday-OT (Off) day is neither.
        if r['status'] == 'Absent':
            rec['absentDays'] += 1
        elif r['status'] in DAY_HOURS_STATUSES:
            rec['presentDays'] += 1
        rec['hasConflict'] = rec['hasConflict'] or bool(r['conflict'])
    return [by_emp[i] for i in order]


def update_entry(entry_id, status=None, ot_hours=None, sites=None, note=None):
    """Office correction of a single logged day. Re-runs both syncs so the
    attendance grid and the OT total follow the correction."""
    row = db.q_one(
        "SELECT employee_id, entry_date, status FROM labour_day_log WHERE id = %s",
        (entry_id,))
    if row is None:
        return None

    new_status = _normalize_status(status) if status is not None else row['status']
    if isinstance(sites, (list, tuple)):
        sites = ', '.join(str(s).strip() for s in sites if str(s).strip())
    # Same rule as save_day: a day that was not worked keeps neither OT nor a
    # site, whatever the modal happened to still have in its fields.
    if earns_ot(new_status):
        new_ot = _num(ot_hours) if ot_hours is not None else None
    else:
        new_ot, sites = 0, ''

    entry_date = row['entry_date']
    emp_id = row['employee_id']

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE labour_day_log SET
                     status = %s,
                     ot_hours = COALESCE(%s, ot_hours),
                     sites = COALESCE(%s, sites),
                     note = COALESCE(%s, note),
                     conflict = false,          -- the office has now reviewed it
                     updated_at = now()
                   WHERE id = %s""",
                (new_status, new_ot, sites, note, entry_id))
            _sync_attendance(cur, [[emp_id, entry_date, new_status]], entry_date)
            _sync_month_end_ot(cur, [emp_id], entry_date.year, entry_date.month)
    db.transaction(fn)

    return db.q_one("SELECT * FROM labour_day_log WHERE id = %s", (entry_id,))


def delete_entry(entry_id):
    """Remove a logged day entirely (e.g. the engineer marked the wrong man).
    Clears the attendance cell and re-totals OT."""
    row = db.q_one(
        "SELECT employee_id, entry_date FROM labour_day_log WHERE id = %s",
        (entry_id,))
    if row is None:
        return False
    emp_id, entry_date = row['employee_id'], row['entry_date']
    day = entry_date.day

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM labour_day_log WHERE id = %s", (entry_id,))
            cur.execute(
                f"""UPDATE attendance SET day{day} = ''
                    WHERE employee_id = %s AND year = %s AND month = %s""",
                (emp_id, entry_date.year, entry_date.month))
            _sync_month_end_ot(cur, [emp_id], entry_date.year, entry_date.month)
    db.transaction(fn)
    return True
