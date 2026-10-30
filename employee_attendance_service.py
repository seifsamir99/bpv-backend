"""
Employee, Attendance, and Payroll service backed by Supabase Postgres.

Tables (see schema_supabase.sql):
  employees  — labour + staff unified, distinguished by the `type` column
  attendance — one row per employee per month, day1..day31 grid (month 1-12)
  payroll    — saved payroll snapshots keyed by (year, month, emp_type, employee_id)

Public method names, argument conventions, and return shapes are identical to
the old Google-Sheets implementation (preserved in legacy_sheets_backup/):
in particular, `month` arguments are 0-indexed (0=Jan) exactly as the frontend
sends them; the database stores months 1-12.
"""

import calendar
import secrets
from datetime import datetime

from psycopg2.extras import execute_values

import db

MONTH_ABBRS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

CANONICAL_STATUSES = {
    'present': 'Present', 'absent': 'Absent', 'leave': 'Leave',
    'off': 'Off', 'sick': 'Sick', 'joined': 'Joined', 'holiday': 'Holiday',
    'cancelled': 'Cancelled', 'canceled': 'Cancelled', 'resigned': 'Resigned'
}

# Statuses that END a man's employment: the day itself is not paid and nothing
# after it counts, in this month or any later one. See _end_dates().
TERMINAL_STATUSES = ('cancelled', 'resigned')

# Natural numeric ordering for the text employee IDs ('2' before '10')
_ID_ORDER = r"(CASE WHEN id ~ '^\d+$' THEN id::int ELSE 2147483647 END), id"
_EMP_ID_ORDER = r"(CASE WHEN employee_id ~ '^\d+$' THEN employee_id::int ELSE 2147483647 END), employee_id"


def _parse_num(val):
    """Parse a possibly comma-formatted number string to float."""
    if val is None or val == '':
        return 0
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0


def _normalize_status(status):
    if not status:
        return status
    return CANONICAL_STATUSES.get(status.lower(), status)


def _adjustments(row):
    """Month-End manual adjustment fields (present on every employee row)."""
    return {
        'bonus': _parse_num(row.get('bonus', 0)),
        'bonusReason': row.get('bonus_reason') or '',
        'deductionAmount': _parse_num(row.get('month_end_deduction', 0)),
        'deductionReason': row.get('deduction_reason') or '',
        'deductionNote': row.get('deduction_note') or '',
    }


def _labour_dict(row, row_index):
    return {
        'id': row['id'], 'employeeId': row['id'], 'name': row['name'],
        'designation': row['designation'],
        'ratePerDay': _parse_num(row['rate_per_day']),
        'ratePerHour': _parse_num(row['rate_per_hour']),
        'otHours': _parse_num(row['ot_hours']),
        'netSalary': _parse_num(row['net_salary']),
        'type': row['type'] or 'Labour',
        'rowIndex': row_index, 'sheetType': 'labour',
        'checkinToken': row.get('checkin_token'),
        'timeLogEnabled': bool(row.get('time_log_enabled')),
        'crewSheetEnabled': bool(row.get('crew_sheet_enabled')),
        **_adjustments(row),
    }


def _staff_dict(row, row_index):
    return {
        'id': row['id'], 'employeeId': row['id'], 'name': row['name'],
        'ratePerDay': _parse_num(row['rate_per_day']),
        'designation': row['designation'],
        'deductions': _parse_num(row['deductions']),
        'netSalary': _parse_num(row['net_salary']),
        'type': row['type'] or 'Staff',
        'rowIndex': row_index, 'sheetType': 'staff',
        'checkinToken': row.get('checkin_token'),
        'timeLogEnabled': bool(row.get('time_log_enabled')),
        'crewSheetEnabled': bool(row.get('crew_sheet_enabled')),
        **_adjustments(row),
    }


class EmployeeAttendanceService:

    # ---------- EMPLOYEES ----------

    def get_employees(self, emp_type=None):
        # One round-trip; partition labour/staff in Python (labour listed first)
        rows = db.q(f"SELECT * FROM employees ORDER BY {_ID_ORDER}")
        labour = [r for r in rows if (r['type'] or '').lower() != 'staff']
        staff = [r for r in rows if (r['type'] or '').lower() == 'staff']
        employees = []
        if not emp_type or emp_type == 'labour':
            employees += [_labour_dict(r, i) for i, r in enumerate(labour, start=2)]
        if not emp_type or emp_type == 'staff':
            employees += [_staff_dict(r, i) for i, r in enumerate(staff, start=2)]
        return employees

    def get_employee(self, employee_id):
        row = db.q_one("SELECT * FROM employees WHERE id = %s", (str(employee_id),))
        if row is None:
            return None
        if (row['type'] or '').lower() == 'staff':
            return _staff_dict(row, 2)
        return _labour_dict(row, 2)

    def validate_employees(self):
        """Cross-check the employee roster against the current month's attendance."""
        now = datetime.now()
        self._ensure_month_seeded(now.month, now.year)

        issues = []
        salary_employees = {r['id']: {'name': r['name'], 'type': (r['type'] or '').lower()}
                            for r in db.q("SELECT id, name, type FROM employees")}
        att_employees = {r['employee_id']: {'name': r['name'], 'designation': r['designation'],
                                            'type': r['type']}
                         for r in db.q("SELECT employee_id, name, designation, type FROM attendance "
                                       "WHERE year = %s AND month = %s", (now.year, now.month))}

        for emp_id, att_data in att_employees.items():
            if emp_id not in salary_employees:
                issues.append({'type': 'missing_salary', 'employeeId': emp_id, 'name': att_data['name'],
                               'attendanceType': att_data['type'],
                               'message': f"Employee {att_data['name']} ({emp_id}) in {att_data['type']} attendance but not in salary"})
        for emp_id, sal_data in salary_employees.items():
            if emp_id not in att_employees:
                issues.append({'type': 'missing_attendance', 'employeeId': emp_id, 'name': sal_data['name'],
                               'salaryType': sal_data['type'],
                               'message': f"Employee {sal_data['name']} ({emp_id}) in {sal_data['type']} salary but not in attendance"})
        for emp_id, att_data in att_employees.items():
            sal_data = salary_employees.get(emp_id)
            if sal_data and att_data['type'] != sal_data['type']:
                issues.append({'type': 'type_mismatch', 'employeeId': emp_id, 'name': att_data['name'],
                               'attendanceType': att_data['type'], 'salaryType': sal_data['type'],
                               'message': f"Employee {att_data['name']} ({emp_id}) is {att_data['type']} in attendance but {sal_data['type']} in salary"})
        return issues

    def get_next_employee_id(self, emp_type='labour'):
        row = db.q_one(
            r"""SELECT COALESCE(MAX(id::int), 0) AS max_id FROM employees
                WHERE id ~ '^\d+$' AND lower(type) = %s""",
            ('staff' if emp_type == 'staff' else 'labour',))
        return int(row['max_id']) + 1 if row['max_id'] else 1

    def create_employee(self, data):
        is_staff = data.get('type', '').lower() == 'staff'

        employee_id = data.get('employeeId')
        if not employee_id:
            employee_id = self.get_next_employee_id('staff' if is_staff else 'labour')
        employee_id = str(employee_id)

        rate_per_day = _parse_num(data.get('ratePerDay', 0))
        rate_per_hour = _parse_num(data.get('ratePerHour', 0))
        if not is_staff and not rate_per_hour and rate_per_day:
            rate_per_hour = round(rate_per_day / 8, 2)

        checkin_token = secrets.token_urlsafe(16)
        db.execute(
            """INSERT INTO employees
               (id, name, designation, rate_per_day, rate_per_hour, ot_hours, deductions, net_salary, type, checkin_token)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (employee_id, data.get('name', ''), data.get('designation', ''),
             rate_per_day,
             0 if is_staff else rate_per_hour,
             0 if is_staff else _parse_num(data.get('otHours', 0)),
             _parse_num(data.get('deductions', 0)) if is_staff else 0,
             _parse_num(data.get('netSalary', 0)),
             'Staff' if is_staff else 'Labour',
             checkin_token))

        return {
            'id': employee_id, 'employeeId': employee_id,
            'name': data.get('name', ''), 'designation': data.get('designation', ''),
            'ratePerDay': rate_per_day,
            'ratePerHour': _parse_num(data.get('ratePerHour', 0)) or rate_per_hour,
            'otHours': _parse_num(data.get('otHours', 0)),
            'netSalary': _parse_num(data.get('netSalary', 0)),
            'type': 'Staff' if is_staff else 'Labour',
            'sheetType': 'staff' if is_staff else 'labour',
            'checkinToken': checkin_token,
        }

    def update_employee(self, employee_id, data):
        employee_id = str(employee_id)
        row = db.q_one("SELECT * FROM employees WHERE id = %s", (employee_id,))
        if row is None:
            return None

        def v(key, col):
            return _parse_num(data[key]) if key in data else row[col]

        is_staff = (row['type'] or '').lower() == 'staff'
        db.execute(
            """UPDATE employees SET name=%s, designation=%s, rate_per_day=%s,
               rate_per_hour=%s, ot_hours=%s, deductions=%s, net_salary=%s
               WHERE id=%s""",
            (data.get('name', row['name']),
             data['designation'] if 'designation' in data else row['designation'],
             v('ratePerDay', 'rate_per_day'),
             row['rate_per_hour'] if is_staff else v('ratePerHour', 'rate_per_hour'),
             row['ot_hours'] if is_staff else v('otHours', 'ot_hours'),
             v('deductions', 'deductions') if is_staff else row['deductions'],
             v('netSalary', 'net_salary'),
             employee_id))
        return {**data, 'id': employee_id, 'type': row['type'] or ('Staff' if is_staff else 'Labour')}

    def delete_employee(self, employee_id):
        employee_id = str(employee_id)
        row = db.q_one("SELECT type FROM employees WHERE id = %s", (employee_id,))
        if row is None:
            return None
        db.execute("DELETE FROM employees WHERE id = %s", (employee_id,))
        # Attendance keeps its own per-month rows (seeded from employees), so they
        # must be removed too or the person keeps showing on the Attendance page.
        # Payroll snapshots are left intact as financial history.
        db.execute("DELETE FROM attendance WHERE employee_id = %s", (employee_id,))
        label = 'Staff' if (row['type'] or '').lower() == 'staff' else 'Labour'
        return f'Employee {employee_id} deleted from {label}'

    def move_employee(self, employee_id, target_type):
        """Switch an employee between labour and staff (also flips their
        current-month attendance rows; historical months keep the old type)."""
        employee_id = str(employee_id)
        source_sheet = 'Staff salary' if target_type == 'labour' else 'Labour salary'
        target_sheet = 'Labour salary' if target_type == 'labour' else 'Staff salary'

        row = db.q_one("SELECT * FROM employees WHERE id = %s AND lower(type) = %s",
                       (employee_id, 'staff' if target_type == 'labour' else 'labour'))
        if row is None:
            return None, f'Employee {employee_id} not found in {source_sheet}'

        rate_per_day = _parse_num(row['rate_per_day'])
        now = datetime.now()

        def fn(conn):
            with conn.cursor() as cur:
                if target_type == 'labour':
                    cur.execute(
                        """UPDATE employees SET type='Labour',
                           rate_per_hour=%s, ot_hours=0, deductions=0, net_salary=0
                           WHERE id=%s""",
                        (round(rate_per_day / 8, 2) if rate_per_day else 0, employee_id))
                else:
                    cur.execute(
                        """UPDATE employees SET type='Staff',
                           rate_per_hour=0, ot_hours=0, deductions=0, net_salary=0
                           WHERE id=%s""",
                        (employee_id,))
                cur.execute(
                    "UPDATE attendance SET type=%s WHERE employee_id=%s AND year=%s AND month=%s",
                    (target_type, employee_id, now.year, now.month))
        db.transaction(fn)
        return True, f'Employee {employee_id} moved to {target_sheet}'

    def fix_missing_salary(self, employee_id, target_type, rate_per_day=0, rate_per_hour=0, ot_hours=0):
        """Create a salary record for an employee that exists only in attendance."""
        employee_id = str(employee_id)
        att = db.q_one(
            """SELECT name, designation FROM attendance
               WHERE employee_id = %s AND type = %s
               ORDER BY year DESC, month DESC LIMIT 1""",
            (employee_id, target_type))
        if att is None:
            att_sheet = 'Labour attendence' if target_type == 'labour' else 'Staff attendence'
            return None, f'Employee {employee_id} not found in {att_sheet}'

        existing = db.q_one("SELECT id FROM employees WHERE id = %s", (employee_id,))
        if existing is not None:
            sal_sheet = 'Labour salary' if target_type == 'labour' else 'Staff salary'
            return None, f'Employee {employee_id} already exists in {sal_sheet}'

        db.execute(
            """INSERT INTO employees
               (id, name, designation, rate_per_day, rate_per_hour, ot_hours, deductions, net_salary, type)
               VALUES (%s,%s,%s,%s,%s,%s,0,0,%s)""",
            (employee_id, att['name'], att['designation'],
             _parse_num(rate_per_day), _parse_num(rate_per_hour),
             _parse_num(ot_hours) if target_type == 'labour' else 0,
             'Labour' if target_type == 'labour' else 'Staff'))
        return {'id': employee_id, 'name': att['name'], 'designation': att['designation']}, None

    # ---------- ATTENDANCE ----------

    def _ensure_month_seeded(self, db_month, year, emp_type=None):
        """Give every employee of the requested type a blank attendance row for
        the month (replaces the old copy-the-base-tab behavior).

        Seeds PER EMPLOYEE, leaning on the ON CONFLICT to skip anyone who
        already has a row. It used to skip the whole type when the month held
        any row of that type at all, which broke two ways:

          * someone added to Employees after a month was first opened never got
            a row in that month, and never would (Hany/Fahd were invisible on
            the Attendance page in every month of 2026);
          * once labour_log_service writes a row for the one labour an engineer
            marked, a brand-new month counts as "already seeded" and the other
            33 are skipped for good.

        Single statement, and writes nothing when everyone already has a row.
        """
        types = ['labour', 'staff'] if emp_type in (None, 'all') else [emp_type]
        db.execute(
            """INSERT INTO attendance (employee_id, year, month, type, name, designation)
               SELECT e.id, %s, %s, lower(e.type), e.name, e.designation
               FROM employees e
               WHERE lower(e.type) = ANY(%s)
               ON CONFLICT (employee_id, year, month) DO NOTHING""",
            (year, db_month, types))

    def _end_dates(self):
        """Earliest Cancelled/Resigned cell per employee, across EVERY month.

        The cut-off is derived from the grid rather than stored on `employees`,
        deliberately: five separate paths write attendance (single edit,
        mark-all-month, date-range, bulk day, and the engineers' crew sheet in
        labour_log_service), and an `end_date` column would have to be kept in
        step with all of them. Deriving it means putting the cell back to
        Present un-resigns the man, with nothing to undo by hand.

        Returns {employee_id: {'date': date, 'reason': 'Resigned'|'Cancelled'}}.
        Only the cells that actually hold a terminal status come back from the
        DB, so this stays a handful of rows however many months exist.
        """
        day_cols = ', '.join(f'a.day{d}' for d in range(1, 32))
        rows = db.q(
            f"""SELECT a.employee_id, a.year, a.month, d.idx AS day, d.val AS status
                FROM attendance a,
                     LATERAL unnest(ARRAY[{day_cols}]) WITH ORDINALITY AS d(val, idx)
                WHERE lower(trim(d.val)) = ANY(%s)""",
            (list(TERMINAL_STATUSES),))

        ends = {}
        for r in rows:
            try:
                when = datetime(r['year'], r['month'], r['day']).date()
            except ValueError:
                continue  # day31 of a 30-day month etc. — not a real date
            prev = ends.get(r['employee_id'])
            if prev is None or when < prev['date']:
                ends[r['employee_id']] = {
                    'date': when,
                    'reason': _normalize_status(r['status'].strip()),
                }
        return ends

    @staticmethod
    def _cutoff_day(end, year, db_month):
        """Translate an end date into a cut-off for ONE month.

        0    -> the man was already gone before this month: nothing counts.
        N    -> he ended on day N of this month: only days 1..N-1 count.
        None -> no end date, or it falls after this month: count everything.
        """
        if not end:
            return None
        when = end['date']
        if (when.year, when.month) < (year, db_month):
            return 0
        if (when.year, when.month) == (year, db_month):
            return when.day
        return None

    def get_attendance(self, emp_type='all', month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1  # 0-indexed
        year = year if year is not None else now.year
        db_month = int(month) + 1

        self._ensure_month_seeded(db_month, year, emp_type)

        # One round-trip for both types; partition in Python (labour first)
        all_rows = db.q(
            f"""SELECT * FROM attendance WHERE year=%s AND month=%s
                ORDER BY {_EMP_ID_ORDER}""",
            (year, db_month))
        ends = self._end_dates()
        attendance = []
        for sheet_type in ('labour', 'staff'):
            if emp_type not in (sheet_type, 'all'):
                continue
            rows = [r for r in all_rows if r['type'] == sheet_type]
            for i, row in enumerate(rows, start=2):
                end = ends.get(row['employee_id'])
                attendance.append({
                    'employeeId': row['employee_id'],
                    'name': row['name'],
                    'designation': row['designation'],
                    'rowIndex': i,
                    'type': sheet_type,
                    'days': {day: row[f'day{day}'] for day in range(1, 32)},
                    # Cancelled/Resigned cut-off. endDate/endReason are for
                    # display (they carry into later months); cutoffDay is the
                    # one number payrollCalculation.js needs for THIS month.
                    'endDate': end['date'].isoformat() if end else None,
                    'endReason': end['reason'] if end else None,
                    'cutoffDay': self._cutoff_day(end, year, db_month),
                })
        return attendance

    def update_attendance(self, employee_id, day, status, emp_type='labour', month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        day = int(day)
        if day < 1 or day > 31:
            return None, 'Invalid day'
        db_month = int(month) + 1

        self._ensure_month_seeded(db_month, year, emp_type)
        # day is validated 1-31, so the column name is safe to interpolate
        updated = db.execute(
            f"UPDATE attendance SET day{day} = %s WHERE employee_id=%s AND year=%s AND month=%s",
            (_normalize_status(status) or '', str(employee_id), year, db_month))
        if not updated:
            return None, 'Employee not found in attendance sheet'
        return True, None

    def bulk_update_all_days(self, employee_id, status, emp_type='labour', month=None, year=None):
        """Mark all non-Sunday days in the month for a single employee."""
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        db_month = int(month) + 1

        self._ensure_month_seeded(db_month, year, emp_type)

        days_in_month = calendar.monthrange(year, db_month)[1]
        normalized = _normalize_status(status) or ''
        target_days = [d for d in range(1, days_in_month + 1)
                       if datetime(year, db_month, d).weekday() != 6]  # skip Sundays
        if not target_days:
            return 0, None

        set_clause = ', '.join(f'day{d} = %s' for d in target_days)
        params = [normalized] * len(target_days) + [str(employee_id), year, db_month]
        updated = db.execute(
            f"UPDATE attendance SET {set_clause} WHERE employee_id=%s AND year=%s AND month=%s",
            params)
        if not updated:
            return 0, 'Employee not found in attendance sheet'
        return len(target_days), None

    def bulk_update_all_days_all_employees(self, status, employee_ids=None, emp_type='all', month=None, year=None):
        """Mark all non-Sunday days in the month for many employees in ONE query.

        If employee_ids is provided, only those rows are updated (respects UI filters);
        otherwise every employee of emp_type ('all'/'labour'/'staff') is updated.
        """
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        db_month = int(month) + 1

        self._ensure_month_seeded(db_month, year, emp_type)

        days_in_month = calendar.monthrange(year, db_month)[1]
        normalized = _normalize_status(status) or ''
        target_days = [d for d in range(1, days_in_month + 1)
                       if datetime(year, db_month, d).weekday() != 6]  # skip Sundays
        if not target_days:
            return 0, None

        set_clause = ', '.join(f'day{d} = %s' for d in target_days)
        params = [normalized] * len(target_days) + [year, db_month]
        where = "year=%s AND month=%s"
        if employee_ids:
            where += " AND employee_id = ANY(%s)"
            params.append([str(e) for e in employee_ids])
        elif emp_type in ('labour', 'staff'):
            where += " AND type=%s"
            params.append(emp_type)

        updated = db.execute(
            f"UPDATE attendance SET {set_clause} WHERE {where}",
            params)
        return updated, None

    def bulk_update_range(self, from_day, to_day, status, employee_ids=None,
                          emp_type='all', month=None, year=None, include_sundays=True):
        """Set one status across a span of days — a two-week leave, a shutdown,
        a man who joined mid-month.

        Unlike bulk_update_all_days, Sundays are INCLUDED by default: the whole
        point of marking a leave period is that the Sundays inside it stop
        counting as paid 'Off' days (isPaidDay in payrollCalculation.js counts
        Off as paid, Leave/Absent as not).
        """
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        db_month = int(month) + 1

        from_day, to_day = int(from_day), int(to_day)
        if from_day > to_day:
            from_day, to_day = to_day, from_day
        days_in_month = calendar.monthrange(year, db_month)[1]
        if from_day < 1 or to_day > 31:
            return 0, 'Invalid day range'

        self._ensure_month_seeded(db_month, year, emp_type)

        normalized = _normalize_status(status) or ''
        target_days = [
            d for d in range(from_day, min(to_day, days_in_month) + 1)
            if include_sundays or datetime(year, db_month, d).weekday() != 6
        ]
        if not target_days:
            return 0, None

        # day numbers are validated ints, so the column names are safe to interpolate
        set_clause = ', '.join(f'day{d} = %s' for d in target_days)
        params = [normalized] * len(target_days) + [year, db_month]
        where = "year=%s AND month=%s"
        if employee_ids:
            where += " AND employee_id = ANY(%s)"
            params.append([str(e) for e in employee_ids])
        elif emp_type in ('labour', 'staff'):
            where += " AND type=%s"
            params.append(emp_type)

        updated = db.execute(
            f"UPDATE attendance SET {set_clause} WHERE {where}", params)
        return updated, None

    def bulk_update_attendance(self, day, updates, emp_type=None, month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        day = int(day)
        if day < 1 or day > 31:
            return 0, 'Invalid day'
        db_month = int(month) + 1

        self._ensure_month_seeded(db_month, year, emp_type)

        counter = {'n': 0}

        def fn(conn):
            counter['n'] = 0
            with conn.cursor() as cur:
                for upd in updates:
                    cur.execute(
                        f"UPDATE attendance SET day{day} = %s WHERE employee_id=%s AND year=%s AND month=%s",
                        (_normalize_status(upd['status']) or '', str(upd['employeeId']), year, db_month))
                    counter['n'] += cur.rowcount
        db.transaction(fn)
        return counter['n'], None

    # ---------- PAYROLL ----------

    def write_payroll(self, month, year, emp_type, data, partial=False):
        """partial=True upserts only the given rows (no DELETE) — used by the
        frontend's save-what-changed flow. Default replaces the whole
        month+type snapshot."""
        month_abbr = MONTH_ABBRS[int(month)]
        db_month = int(month) + 1
        year = int(year)
        emp_type = 'staff' if emp_type == 'staff' else 'labour'
        tab_name = f"{'Staff' if emp_type == 'staff' else 'Labour'} {month_abbr}"

        # Build all row tuples up front and insert them with ONE multi-row
        # statement (execute_values). The DB is remote (Frankfurt) — the old
        # one-INSERT-per-employee loop was 40+ sequential round-trips and made
        # every save take 10-30s.
        rows = []
        for r in data:
            payment_method = r.get('paymentMethod') or ('Cash' if r.get('isCash') else 'Bank Transfer')
            rows.append((
                year, db_month, emp_type, str(r.get('employeeId', '')),
                r.get('name', ''), r.get('designation', ''),
                _parse_num(r.get('deductionAmount', 0)), _parse_num(r.get('paidDays', 0)),
                _parse_num(r.get('ratePerHour', 0)),
                None if emp_type == 'staff' else _parse_num(r.get('salaryBeforeOT', 0)),
                _parse_num(r.get('otHours', 0)),
                _parse_num(r.get('otPay', 0)),
                _parse_num(r.get('netSalary', 0)), payment_method,
                _parse_num(r.get('bonus', 0)), r.get('bonusReason', '') or '',
                r.get('deductionReason', '') or '', r.get('deductionNote', '') or ''))

        def fn(conn):
            with conn.cursor() as cur:
                if not partial:
                    cur.execute("DELETE FROM payroll WHERE year=%s AND month=%s AND emp_type=%s",
                                (year, db_month, emp_type))
                if rows:
                    execute_values(cur,
                        """INSERT INTO payroll
                           (year, month, emp_type, employee_id, name, designation,
                            deduction_amount, paid_days, rate_per_hour,
                            salary_before_ot, ot_hours, ot_pay, net_salary, payment_method,
                            bonus, bonus_reason, deduction_reason, deduction_note)
                           VALUES %s
                           ON CONFLICT (year, month, emp_type, employee_id) DO UPDATE SET
                             name=EXCLUDED.name, designation=EXCLUDED.designation,
                             deduction_amount=EXCLUDED.deduction_amount, paid_days=EXCLUDED.paid_days,
                             rate_per_hour=EXCLUDED.rate_per_hour, salary_before_ot=EXCLUDED.salary_before_ot,
                             ot_hours=EXCLUDED.ot_hours, ot_pay=EXCLUDED.ot_pay,
                             net_salary=EXCLUDED.net_salary, payment_method=EXCLUDED.payment_method,
                             bonus=EXCLUDED.bonus, bonus_reason=EXCLUDED.bonus_reason,
                             deduction_reason=EXCLUDED.deduction_reason, deduction_note=EXCLUDED.deduction_note""",
                        rows, page_size=200)
        db.transaction(fn)

        cash_data = [r for r in data if r.get('isCash') or r.get('paymentMethod') == 'Cash']
        return len(data), len(cash_data), tab_name, month_abbr

    def get_payroll_history(self):
        """All saved payroll runs, newest first: one row per (year, month, type)
        with employee count and totals. month returned 0-indexed like the rest
        of the payroll API."""
        rows = db.q(
            """SELECT year, month, emp_type,
                      count(*)                                        AS employees,
                      coalesce(sum(net_salary), 0)                    AS total_net,
                      coalesce(sum(ot_pay), 0)                        AS total_ot,
                      coalesce(sum(bonus), 0)                         AS total_bonus,
                      coalesce(sum(deduction_amount), 0)              AS total_deductions,
                      count(*) FILTER (WHERE payment_method = 'Cash') AS cash_count
               FROM payroll
               GROUP BY year, month, emp_type
               ORDER BY year DESC, month DESC, emp_type""")
        return [{
            'year': r['year'],
            'month': r['month'] - 1,  # 0-indexed for the frontend
            'type': r['emp_type'],
            'employees': r['employees'],
            'totalNet': _parse_num(r['total_net']),
            'totalOT': _parse_num(r['total_ot']),
            'totalBonus': _parse_num(r['total_bonus']),
            'totalDeductions': _parse_num(r['total_deductions']),
            'cashCount': r['cash_count'],
        } for r in rows]

    def get_payroll(self, month, year, emp_type):
        db_month = int(month) + 1
        year = int(year)
        is_staff = emp_type == 'staff'
        emp_type = 'staff' if is_staff else 'labour'

        rows = db.q(
            f"""SELECT * FROM payroll WHERE year=%s AND month=%s AND emp_type=%s
                ORDER BY {_EMP_ID_ORDER}""",
            (year, db_month, emp_type))
        if not rows:
            return [], 'No saved data for this period'

        result = []
        for row in rows:
            rec = {
                'employeeId': row['employee_id'],
                'name': row['name'],
                'designation': row['designation'],
                'deductionAmount': _parse_num(row['deduction_amount']),
                'paidDays': _parse_num(row['paid_days']),
                'ratePerHour': _parse_num(row['rate_per_hour']),
                'netSalary': _parse_num(row['net_salary']),
                'paymentMethod': row['payment_method'] or 'Bank Transfer',
                'isCash': (row['payment_method'] or '') == 'Cash',
                'bonus': _parse_num(row.get('bonus', 0)),
                'bonusReason': row.get('bonus_reason') or '',
                'deductionReason': row.get('deduction_reason') or '',
                'deductionNote': row.get('deduction_note') or '',
                # OT applies to staff too (since 2026-07-23); old staff rows have NULLs → 0
                'otHours': _parse_num(row['ot_hours']),
                'otPay': _parse_num(row['ot_pay']),
            }
            if not is_staff:
                rec['salaryBeforeOT'] = _parse_num(row['salary_before_ot'])
            result.append(rec)
        return result, None

    # ---------- MONTH-END (per-month OT / bonus / deduction entries) ----------

    def get_month_end(self, month, year):
        """All employees (labour first, then staff) joined with their month_end
        row for the given month (0-indexed at the API boundary, like
        attendance/payroll). Employees without a row yet come back with
        zeros/blanks. Staff are included so bonuses/deductions can be entered
        for them too (their OT is ignored by the payroll calc)."""
        db_month = int(month) + 1
        year = int(year)
        rows = db.q(
            f"""SELECT e.id, e.name, e.designation, e.rate_per_day, e.type,
                       m.ot_hours, m.bonus, m.bonus_reason,
                       m.deduction_amount, m.deduction_reason, m.deduction_note,
                       m.ot_source
                FROM employees e
                LEFT JOIN month_end m
                  ON m.employee_id = e.id AND m.year = %s AND m.month = %s
                ORDER BY (CASE WHEN lower(e.type) = 'staff' THEN 1 ELSE 0 END),
                         (CASE WHEN e.id ~ '^\\d+$' THEN e.id::int ELSE 2147483647 END), e.id""",
            (year, db_month))
        return [{
            'id': r['id'], 'employeeId': r['id'], 'name': r['name'],
            'designation': r['designation'],
            'ratePerDay': _parse_num(r['rate_per_day']),
            'type': 'staff' if (r['type'] or '').lower() == 'staff' else 'labour',
            'otHours': _parse_num(r['ot_hours']),
            # 'log'  = auto-summed from the engineers' daily labour_day_log
            # 'manual' = typed here by the office (and then never overwritten)
            'otSource': r['ot_source'] or 'manual',
            'bonus': _parse_num(r['bonus']),
            'bonusReason': r['bonus_reason'] or '',
            'deductionAmount': _parse_num(r['deduction_amount']),
            'deductionReason': r['deduction_reason'] or '',
            'deductionNote': r['deduction_note'] or '',
        } for r in rows]

    def save_month_end(self, month, year, updates):
        """Upsert month_end rows for the given month in one transaction.
        Only keys present in each update dict are written."""
        db_month = int(month) + 1
        year = int(year)
        counter = {'n': 0}

        # (payload key, db column, coercer)
        FIELD_MAP = [
            ('otHours', 'ot_hours', _parse_num),
            ('bonus', 'bonus', _parse_num),
            ('bonusReason', 'bonus_reason', lambda v: v or ''),
            ('deductionAmount', 'deduction_amount', _parse_num),
            ('deductionReason', 'deduction_reason', lambda v: v or ''),
            ('deductionNote', 'deduction_note', lambda v: v or ''),
        ]

        # Group updates by which columns they carry so each group is ONE
        # multi-row upsert (execute_values) — the DB is remote, per-row
        # statements would be a round-trip each.
        groups = {}
        for upd in updates:
            cols = tuple(col for key, col, _ in FIELD_MAP if key in upd)
            if not cols:
                continue
            vals = [coerce(upd[key]) for key, col, coerce in FIELD_MAP if key in upd]
            # The frontend only sends fields the office actually edited, so an
            # otHours here means someone typed it: pin the row to 'manual' and
            # labour_log_service will stop overwriting it from the daily log.
            if 'otHours' in upd:
                cols += ('ot_source',)
                vals.append('manual')
            groups.setdefault(cols, []).append([str(upd['employeeId']), year, db_month] + vals)

        def fn(conn):
            counter['n'] = 0
            with conn.cursor() as cur:
                for cols, rows in groups.items():
                    col_list = ', '.join(cols)
                    set_list = ', '.join(f"{c}=EXCLUDED.{c}" for c in cols)
                    execute_values(cur,
                        f"""INSERT INTO month_end (employee_id, year, month, {col_list})
                            VALUES %s
                            ON CONFLICT (employee_id, year, month) DO UPDATE SET {set_list}""",
                        rows, page_size=200)
                    counter['n'] += cur.rowcount
        db.transaction(fn)
        return counter['n']

    # ---------- OT / DEDUCTIONS ----------

    def update_employee_ot(self, employee_id, ot_hours):
        updated = db.execute(
            "UPDATE employees SET ot_hours = %s WHERE id = %s AND lower(type) = 'labour'",
            (_parse_num(ot_hours), str(employee_id)))
        return bool(updated)

    def bulk_update_ot(self, updates):
        """Save Month-End entries. Each update always carries otHours and may also
        carry the manual bonus/deduction adjustment fields; only keys that are
        present are written, so partial payloads are safe."""
        counter = {'n': 0}

        # (payload key, db column, coercer)
        FIELD_MAP = [
            ('otHours', 'ot_hours', _parse_num),
            ('bonus', 'bonus', _parse_num),
            ('bonusReason', 'bonus_reason', lambda v: v or ''),
            ('deductionAmount', 'month_end_deduction', _parse_num),
            ('deductionReason', 'deduction_reason', lambda v: v or ''),
            ('deductionNote', 'deduction_note', lambda v: v or ''),
        ]

        def fn(conn):
            counter['n'] = 0
            with conn.cursor() as cur:
                for upd in updates:
                    sets, params = [], []
                    for key, col, coerce in FIELD_MAP:
                        if key in upd:
                            sets.append(f"{col} = %s")
                            params.append(coerce(upd[key]))
                    if not sets:
                        continue
                    params.append(str(upd['employeeId']))
                    cur.execute(
                        f"UPDATE employees SET {', '.join(sets)} "
                        "WHERE id = %s AND lower(type) = 'labour'",
                        params)
                    counter['n'] += cur.rowcount
        db.transaction(fn)
        return counter['n']
