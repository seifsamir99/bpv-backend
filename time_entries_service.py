"""
Site Time Log service — daily check-in/check-out for site engineers.

Table (see schema_supabase.sql): time_entries — one row per employee per
calendar day. Employees identify themselves via their personal, unguessable
`employees.checkin_token` link (no login system exists in this app); times are
always the server clock, lat/lng are best-effort GPS captured client-side.

status ('absent' | 'sick' | None) is self-reported by the engineer (or set by
the office) for a day they never check in at all. early_leave + note are set
at checkout when the engineer flags they're leaving before end of shift.

Every write here also keeps `attendance`.day{N} in step, so a check-in never
has to be re-typed on the /attendance page — see the Attendance sync section
below.
"""

import secrets
from datetime import date, datetime, timedelta, timezone

from psycopg2.extras import RealDictCursor

import db

STATUSES = {'absent', 'sick'}

# The company runs on UAE time (UTC+4, no DST). The server clock is not
# trusted for this — Railway runs in UTC.
UAE_TZ = timezone(timedelta(hours=4))
AUTO_ABSENT_HOUR = 9
AUTO_ABSENT_NOTE = 'Auto: no check-in by 9:00 AM'


def _as_date(value):
    """'YYYY-MM-DD' (what the API sends) or a date/datetime -> date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], '%Y-%m-%d').date()


# ---------------------------------------------------------------------------
# Attendance sync
# ---------------------------------------------------------------------------
# Same job, and very nearly the same SQL, as labour_log_service._sync_attendance
# does for the crew sheet: what the Time Log records for a day is written
# straight into that day's cell on the /attendance grid.
#
# The cell is written on the time-log EVENT and never re-derived on read. That
# is what keeps the office in control: once someone edits a day by hand on the
# Attendance page it stays edited, because nothing comes back to overwrite it
# unless that time entry is touched again. It is also why check_out
# deliberately does NOT sync — the check-in already wrote Present, and writing
# it a second time at checkout would clobber an edit made in between.
#
# A labour who is both time_log_enabled and marked on a crew sheet would have
# two writers for the same cell (last write wins). Nobody is on both today.

# Time Log status -> /attendance status. Values must match
# employee_attendance_service.CANONICAL_STATUSES casing exactly.
ATTENDANCE_STATUS = {'absent': 'Absent', 'sick': 'Sick'}


def _attendance_status(row):
    """The attendance cell value a time_entries row implies.

    A check-in means Present whether or not the man ever checked out, whether
    the day was short or an early leave, and whether it was a Sunday — he was
    on site. '' clears the cell (the office reset the day, or the row is gone).
    """
    if row is None:
        return ''
    if row.get('check_in_at'):
        return 'Present'
    return ATTENDANCE_STATUS.get((row.get('status') or '').lower(), '')


def _sync_attendance(cur, employee_id, entry_date, status):
    """Write `status` into attendance.day{N} for one employee-day.

    Seeds the month's attendance row first: /attendance seeds a whole month at
    once the first time it is opened (employee_attendance_service.
    _ensure_month_seeded), which may not have happened yet when an engineer
    checks in early in the month. Works for staff as well as labour — the site
    engineers on the Time Log are mostly staff.
    """
    year, month, day = entry_date.year, entry_date.month, entry_date.day
    cur.execute(
        """INSERT INTO attendance (employee_id, year, month, type, name, designation)
           SELECT e.id, %s, %s, lower(e.type), e.name, e.designation
           FROM employees e WHERE e.id = %s
           ON CONFLICT (employee_id, year, month) DO NOTHING""",
        (year, month, str(employee_id)))
    # `day` comes from a real date, so the column name is safe to interpolate
    cur.execute(
        f"""UPDATE attendance SET day{day} = %s
            WHERE employee_id = %s AND year = %s AND month = %s""",
        (status, str(employee_id), year, month))


def _write_and_sync(employee_id, entry_date, sql, params):
    """Run one time_entries write, then push the day it now describes into the
    attendance grid — both in a single transaction, so the grid can never be
    left disagreeing with the log.

    Re-reads the row rather than trusting the caller: an upsert's COALESCE may
    keep an older check-in time, and an admin edit may have cleared one.
    """
    def fn(conn):
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params)
            cur.execute(
                """SELECT check_in_at, status FROM time_entries
                   WHERE employee_id = %s AND entry_date = %s""",
                (str(employee_id), entry_date))
            row = cur.fetchone()
            _sync_attendance(cur, employee_id, entry_date, _attendance_status(row))
    db.transaction(fn)


# ---------------------------------------------------------------------------
# Auto-absent
# ---------------------------------------------------------------------------
# From 9:00 AM UAE time, any time-log employee with nothing recorded for the
# day is marked Absent — same row and same attendance write as the office's
# "Mark Absent" button, with a note so the office can tell it apart.
#
# Only fills gaps, never overwrites:
#   - an existing time_entries row (a check-in, a self-marked Sick, or an
#     office "Undo" that left the row with no status) is left alone, so an
#     Undo sticks for the rest of the day;
#   - an attendance cell the office already filled (Leave, Vacation, ...) is
#     left alone and the employee is skipped.
# Sundays are skipped (the weekly day off). A late arrival is blocked from
# self check-in like any Absent day; the office fixes it with Undo or the
# pencil (manual check-in clears the status).


def auto_mark_absent(now=None):
    """Mark today's no-shows Absent once it is past AUTO_ABSENT_HOUR.

    Idempotent — safe to call as often as you like. Returns the employee
    ids marked by this call."""
    now = now or datetime.now(UAE_TZ)
    today = now.date()
    if today.weekday() == 6 or now.hour < AUTO_ABSENT_HOUR:
        return []

    def fn(conn):
        with conn.cursor() as cur:
            # `today.day` comes from a real date, so the column name is safe
            cur.execute(
                f"""INSERT INTO time_entries (employee_id, entry_date, status, note)
                    SELECT e.id, %s, 'absent', %s
                    FROM employees e
                    LEFT JOIN attendance a
                      ON a.employee_id = e.id AND a.year = %s AND a.month = %s
                    WHERE e.time_log_enabled = true
                      AND COALESCE(a.day{today.day}, '') = ''
                    ON CONFLICT (employee_id, entry_date) DO NOTHING
                    RETURNING employee_id""",
                (today, AUTO_ABSENT_NOTE, today.year, today.month))
            marked = [r[0] for r in cur.fetchall()]
            for employee_id in marked:
                _sync_attendance(cur, employee_id, today, ATTENDANCE_STATUS['absent'])
            return marked
    return db.transaction(fn)


def generate_token():
    return secrets.token_urlsafe(16)


def get_employee_by_token(token):
    """Who is behind a /checkin link. Gated on time_log_enabled: everyone has a
    checkin_token (the /crew link shares it), so without the gate the office
    could not take a check-in link away."""
    return db.q_one(
        """SELECT id, name, designation FROM employees
           WHERE checkin_token = %s AND time_log_enabled = true""",
        (token,))


def set_time_log_access(employee_id, enabled):
    """Office adds/removes one person's check-in link.

    Turning it on mints a checkin_token if they don't have one yet. Turning it
    off leaves the token alone — it is shared with /crew, so rotating it would
    break their labour-sheet link; /checkin just stops opening.

    Returns the row, or None if there is no such employee.
    """
    employee_id = str(employee_id)
    row = db.q_one(
        "SELECT id, name, checkin_token FROM employees WHERE id = %s", (employee_id,))
    if row is None:
        return None

    token = row['checkin_token']
    if enabled and not token:
        token = generate_token()
        db.execute("UPDATE employees SET checkin_token = %s WHERE id = %s",
                   (token, employee_id))
    db.execute("UPDATE employees SET time_log_enabled = %s WHERE id = %s",
               (bool(enabled), employee_id))
    return {'employeeId': employee_id, 'name': row['name'],
            'timeLogEnabled': bool(enabled), 'checkinToken': token}


def get_today_entry(employee_id, entry_date=None):
    entry_date = entry_date or date.today()
    return db.q_one(
        "SELECT * FROM time_entries WHERE employee_id = %s AND entry_date = %s",
        (employee_id, entry_date))


def check_in(employee_id, entry_date=None, lat=None, lng=None):
    entry_date = _as_date(entry_date or date.today())
    _write_and_sync(
        employee_id, entry_date,
        """INSERT INTO time_entries (employee_id, entry_date, check_in_at, check_in_lat, check_in_lng)
           VALUES (%s, %s, now(), %s, %s)
           ON CONFLICT (employee_id, entry_date) DO UPDATE SET
             check_in_at = COALESCE(time_entries.check_in_at, EXCLUDED.check_in_at),
             check_in_lat = COALESCE(time_entries.check_in_lat, EXCLUDED.check_in_lat),
             check_in_lng = COALESCE(time_entries.check_in_lng, EXCLUDED.check_in_lng),
             status = NULL,
             updated_at = now()""",
        (employee_id, entry_date, lat, lng))
    return get_today_entry(employee_id, entry_date)


def check_out(employee_id, entry_date=None, lat=None, lng=None, early_leave=False, note=None):
    # No attendance sync here on purpose: check_in already wrote Present, and
    # rewriting it now would undo an office correction made during the day.
    entry_date = _as_date(entry_date or date.today())
    db.execute(
        """UPDATE time_entries SET check_out_at = now(), check_out_lat = %s,
             check_out_lng = %s, early_leave = %s, note = COALESCE(%s, note),
             updated_at = now()
           WHERE employee_id = %s AND entry_date = %s
             AND check_in_at IS NOT NULL AND check_out_at IS NULL""",
        (lat, lng, bool(early_leave), note, employee_id, entry_date))
    return get_today_entry(employee_id, entry_date)


def set_status(employee_id, entry_date=None, status=None, note=None):
    """Set (or clear, with status=None) a whole-day status like Absent/Sick.
    Used both by the engineer's self-service page and the admin Day view."""
    entry_date = _as_date(entry_date or date.today())
    if status is not None and status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")
    _write_and_sync(
        employee_id, entry_date,
        """INSERT INTO time_entries (employee_id, entry_date, status, note)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT (employee_id, entry_date) DO UPDATE SET
             status = EXCLUDED.status, note = EXCLUDED.note,
             -- A day marked Absent/Sick is a day not worked, so any check-in
             -- already recorded for it is dropped: the row must not claim both
             -- (the schema's rule), and the attendance cell is derived from
             -- these columns -- a stale check-in time would keep the grid
             -- saying Present after the office marked the man Sick.
             -- Clearing a status (status=None) leaves a real check-in alone.
             check_in_at   = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_in_at   END,
             check_in_lat  = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_in_lat  END,
             check_in_lng  = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_in_lng  END,
             check_out_at  = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_out_at  END,
             check_out_lat = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_out_lat END,
             check_out_lng = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.check_out_lng END,
             early_leave   = CASE WHEN EXCLUDED.status IS NULL THEN time_entries.early_leave
                                  ELSE false END,
             updated_at = now()""",
        (employee_id, entry_date, status, note))
    return get_today_entry(employee_id, entry_date)


def list_entries(year, month, employee_id=None):
    """Admin Time Log listing for a given month (month is 1-12). Only
    employees flagged time_log_enabled (the site engineers) are included."""
    params = [year, month]
    where = ("WHERE e.time_log_enabled = true"
             " AND EXTRACT(YEAR FROM t.entry_date) = %s AND EXTRACT(MONTH FROM t.entry_date) = %s")
    if employee_id:
        where += " AND t.employee_id = %s"
        params.append(str(employee_id))
    rows = db.q(
        f"""SELECT t.*, e.name, e.designation, e.type
            FROM time_entries t
            JOIN employees e ON e.id = t.employee_id
            {where}
            ORDER BY t.entry_date DESC, e.name""",
        tuple(params))
    return [_entry_json(r) for r in rows]


def list_day(entry_date, employee_id=None):
    """Full roster of site engineers for a single day (unlike list_entries,
    includes employees with no time_entries row yet) — for the admin Day view.
    Only employees flagged time_log_enabled are included."""
    sql = """SELECT e.id AS employee_id, e.name, e.designation, e.type,
                    t.id AS entry_id, t.check_in_at, t.check_in_lat, t.check_in_lng,
                    t.check_out_at, t.check_out_lat, t.check_out_lng,
                    t.status, t.note, t.early_leave
             FROM employees e
             LEFT JOIN time_entries t ON t.employee_id = e.id AND t.entry_date = %s
             WHERE e.time_log_enabled = true"""
    params = [entry_date]
    if employee_id:
        sql += " AND e.id = %s"
        params.append(str(employee_id))
    sql += " ORDER BY e.name"
    rows = db.q(sql, tuple(params))
    return [
        {
            'employeeId': r['employee_id'],
            'name': r['name'],
            'designation': r['designation'],
            'type': r['type'],
            'entryId': r['entry_id'],
            'checkInAt': r['check_in_at'].isoformat() if r['check_in_at'] else None,
            'checkOutAt': r['check_out_at'].isoformat() if r['check_out_at'] else None,
            'checkInLat': r['check_in_lat'],
            'checkInLng': r['check_in_lng'],
            'checkOutLat': r['check_out_lat'],
            'checkOutLng': r['check_out_lng'],
            'status': r['status'],
            'note': r['note'],
            'earlyLeave': bool(r['early_leave']),
        }
        for r in rows
    ]


def _entry_json(r):
    return {
        'id': r['id'],
        'employeeId': r['employee_id'],
        'name': r['name'],
        'designation': r['designation'],
        'type': r['type'],
        'entryDate': r['entry_date'].isoformat(),
        'checkInAt': r['check_in_at'].isoformat() if r['check_in_at'] else None,
        'checkOutAt': r['check_out_at'].isoformat() if r['check_out_at'] else None,
        'checkInLat': r['check_in_lat'],
        'checkInLng': r['check_in_lng'],
        'checkOutLat': r['check_out_lat'],
        'checkOutLng': r['check_out_lng'],
        'status': r['status'],
        'note': r['note'],
        'earlyLeave': bool(r['early_leave']),
    }


def delete_entry(entry_id):
    """Admin reset: wipe a day's entry entirely (check-in/out, status, note)
    so the employee can start fresh — e.g. they tapped the wrong button.

    Clears that day's attendance cell too: the day is now unrecorded, and
    leaving a Present behind would be a check-in the Time Log no longer has.
    """
    row = db.q_one(
        "SELECT employee_id, entry_date FROM time_entries WHERE id = %s", (entry_id,))
    if row is None:
        return False

    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM time_entries WHERE id = %s", (entry_id,))
            _sync_attendance(cur, row['employee_id'], row['entry_date'], '')
    db.transaction(fn)
    return True


def update_entry(entry_id, check_in_at=None, check_out_at=None, note=None):
    """Manual admin correction (e.g. a forgotten checkout, or a note).

    Re-syncs attendance because a correction can clear check_in_at, which turns
    a Present day back into a blank one.
    """
    row = db.q_one(
        "SELECT employee_id, entry_date FROM time_entries WHERE id = %s", (entry_id,))
    if row is None:
        return None
    _write_and_sync(
        row['employee_id'], row['entry_date'],
        """UPDATE time_entries SET check_in_at = %s, check_out_at = %s, note = %s, updated_at = now()
           WHERE id = %s""",
        (check_in_at, check_out_at, note, entry_id))
    return db.q_one("SELECT * FROM time_entries WHERE id = %s", (entry_id,))


def manual_check_in(employee_id, entry_date, check_in_at=None, check_out_at=None, note=None):
    """Admin creates a day's entry directly, with no GPS involved at all —
    e.g. the engineer's browser has location permanently denied and can't
    self check-in. Overwrites any existing status (Absent/Sick) for that day."""
    entry_date = _as_date(entry_date)
    _write_and_sync(
        employee_id, entry_date,
        """INSERT INTO time_entries (employee_id, entry_date, check_in_at, check_out_at, note, status)
           VALUES (%s, %s, %s, %s, %s, NULL)
           ON CONFLICT (employee_id, entry_date) DO UPDATE SET
             check_in_at = EXCLUDED.check_in_at, check_out_at = EXCLUDED.check_out_at,
             note = EXCLUDED.note, status = NULL, updated_at = now()""",
        (employee_id, entry_date, check_in_at, check_out_at, note))
    return get_today_entry(employee_id, entry_date)
