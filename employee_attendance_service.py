"""
Employee, Attendance, and Payroll service using Google Sheets.
Ported from the original Node.js backend (index.js).

Sheet IDs:
  SALARY_SHEET_ID     = '1WpLT7IK1x5Pm5Dzj-q98oMCYrzHnFbHMT0DDJN4m7k4'
  ATTENDANCE_SHEET_ID = '1NjZxG_LctqXZP2nk1HvXbFG4rVVzeK6H4WZ-4iRtODE'
  PAYROLL_SHEET_ID    = '1_q5QsmF9gZ2jeJqDpcSHU2iCeJyjIAQntK92G4iFsOg'

Column layout:
  Labour salary:  A=ID B=Name C=Designation D=RatePerDay E=RatePerHour F=OTHours G=NetSalary H=Type
  Staff salary:   A=ID B=Name C=RatePerDay  D=Designation E=Deductions  F=NetSalary G=Type
  Attendance:     A=ID B=Name C=Designation D=Day1 ... AH=Day31
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from googleapiclient.discovery import build
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
import json

load_dotenv()

SALARY_SHEET_ID     = os.getenv('SALARY_SHEET_ID', '1WpLT7IK1x5Pm5Dzj-q98oMCYrzHnFbHMT0DDJN4m7k4')
ATTENDANCE_SHEET_ID = os.getenv('ATTENDANCE_SHEET_ID', '1NjZxG_LctqXZP2nk1HvXbFG4rVVzeK6H4WZ-4iRtODE')
PAYROLL_SHEET_ID    = os.getenv('PAYROLL_SHEET_ID', '1_q5QsmF9gZ2jeJqDpcSHU2iCeJyjIAQntK92G4iFsOg')

LABOUR_SALARY_SHEET     = 'Labour salary'
STAFF_SALARY_SHEET      = 'Staff salary'
LABOUR_ATTENDANCE_SHEET = 'Labour attendence'
STAFF_ATTENDANCE_SHEET  = 'Staff attendence'
MONTH_ABBRS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

CANONICAL_STATUSES = {
    'present': 'Present', 'absent': 'Absent', 'leave': 'Leave',
    'off': 'Off', 'sick': 'Sick', 'joined': 'Joined', 'holiday': 'Holiday'
}

LABOUR_PAYROLL_HEADERS = ['Employee ID', 'Name of Employee', 'Designation', 'Deductions', 'Paid Days',
                          'RATE PER hour', 'total bf OT', 'OT Hours', 'OT Pay', 'Net salary', 'Payment Method']
STAFF_PAYROLL_HEADERS  = ['Employee ID', 'Name of Employee', 'Designation', 'Deductions', 'Paid Days',
                          'RATE PER hour', 'Net salary', 'Payment Method']

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def _get_credentials():
    """Return Google credentials using Railway split-key or file-based pattern."""
    # 1. Railway split-key env vars (preferred in production)
    email = os.getenv('GOOGLE_SERVICE_ACCOUNT_EMAIL')
    private_key = os.getenv('GOOGLE_PRIVATE_KEY')
    if email and private_key:
        info = {
            "type": "service_account",
            "client_email": email,
            "private_key": private_key.replace('\\n', '\n'),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    # 2. credentials.json service account file
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for creds_path in [
        os.path.join(base_dir, 'credentials.json'),
        os.path.join(os.path.dirname(base_dir), 'credentials.json'),
    ]:
        if os.path.exists(creds_path):
            with open(creds_path) as f:
                info = json.load(f)
            if info.get('type') == 'service_account':
                return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    # 3. token.json OAuth user credentials
    for token_path in [
        os.path.join(base_dir, 'token.json'),
        os.path.join(os.path.dirname(base_dir), 'token.json'),
    ]:
        if os.path.exists(token_path):
            return Credentials.from_authorized_user_file(token_path, SCOPES)

    raise ValueError("No valid Google credentials found")


def _get_service():
    creds = _get_credentials()
    return build('sheets', 'v4', credentials=creds)


def _parse_num(val):
    """Parse a possibly comma-formatted number string to float."""
    if val is None or val == '':
        return 0
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0


def _get_column_letter(col_num):
    """Convert 1-based column number to letter(s): 1=A, 26=Z, 27=AA, 34=AH."""
    letter = ''
    while col_num > 0:
        mod = (col_num - 1) % 26
        letter = chr(65 + mod) + letter
        col_num = (col_num - 1) // 26
    return letter


def _normalize_status(status):
    if not status:
        return status
    return CANONICAL_STATUSES.get(status.lower(), status)


def _get_or_create_monthly_sheet(service, base_sheet_name, month, year):
    """
    Return the name of 'base_sheet_name Mon YYYY' sheet.
    Creates it by copying the base sheet if it doesn't exist.
    """
    month_name = MONTH_ABBRS[month]
    target_name = f'{base_sheet_name} {month_name} {year}'

    spreadsheet = service.spreadsheets().get(
        spreadsheetId=ATTENDANCE_SHEET_ID,
        fields='sheets.properties'
    ).execute()

    existing = {s['properties']['title']: s['properties']['sheetId']
                for s in spreadsheet.get('sheets', [])}

    if target_name in existing:
        return target_name

    # Find base sheet
    if base_sheet_name not in existing:
        raise ValueError(f'Base sheet "{base_sheet_name}" not found in attendance spreadsheet')

    base_sheet_id = existing[base_sheet_name]

    # Copy base sheet
    copy_response = service.spreadsheets().sheets().copyTo(
        spreadsheetId=ATTENDANCE_SHEET_ID,
        sheetId=base_sheet_id,
        body={'destinationSpreadsheetId': ATTENDANCE_SHEET_ID}
    ).execute()

    new_sheet_id = copy_response['sheetId']

    # Rename it
    service.spreadsheets().batchUpdate(
        spreadsheetId=ATTENDANCE_SHEET_ID,
        body={
            'requests': [{
                'updateSheetProperties': {
                    'properties': {'sheetId': new_sheet_id, 'title': target_name},
                    'fields': 'title'
                }
            }]
        }
    ).execute()

    # Clear day columns (D2:AH) — keep header + employee info
    service.spreadsheets().values().clear(
        spreadsheetId=ATTENDANCE_SHEET_ID,
        range=f"'{target_name}'!D2:AH"
    ).execute()

    return target_name


# ============================================================================
# EMPLOYEES
# ============================================================================

class EmployeeAttendanceService:
    def __init__(self):
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = _get_service()
        return self._service

    # -- helpers --

    def _read(self, sheet_id, range_):
        resp = self.service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=range_
        ).execute()
        return resp.get('values', [])

    def _write(self, sheet_id, range_, values):
        self.service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=range_,
            valueInputOption='USER_ENTERED',
            body={'values': values}
        ).execute()

    def _write_raw(self, sheet_id, range_, values):
        self.service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=range_,
            valueInputOption='RAW',
            body={'values': values}
        ).execute()

    def _append(self, sheet_id, range_, values):
        self.service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=range_,
            valueInputOption='USER_ENTERED',
            insertDataOption='INSERT_ROWS',
            body={'values': values}
        ).execute()

    def _batch_update(self, sheet_id, data, value_input='RAW'):
        self.service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={'data': data, 'valueInputOption': value_input}
        ).execute()

    # ---------- GET ALL EMPLOYEES ----------

    def get_employees(self, emp_type=None):
        employees = []

        if not emp_type or emp_type == 'labour':
            rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:H")
            for i, row in enumerate(rows[1:], start=2):
                if len(row) >= 2 and row[0] and row[1]:
                    employees.append({
                        'id': row[0],
                        'employeeId': row[0],
                        'name': row[1],
                        'designation': row[2] if len(row) > 2 else '',
                        'ratePerDay': _parse_num(row[3] if len(row) > 3 else 0),
                        'ratePerHour': _parse_num(row[4] if len(row) > 4 else 0),
                        'otHours': _parse_num(row[5] if len(row) > 5 else 0),
                        'netSalary': _parse_num(row[6] if len(row) > 6 else 0),
                        'type': row[7] if len(row) > 7 else 'Labour',
                        'rowIndex': i,
                        'sheetType': 'labour',
                    })

        if not emp_type or emp_type == 'staff':
            rows = self._read(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A:G")
            for i, row in enumerate(rows[1:], start=2):
                if len(row) >= 2 and row[0] and row[1]:
                    employees.append({
                        'id': row[0],
                        'employeeId': row[0],
                        'name': row[1],
                        'ratePerDay': _parse_num(row[2] if len(row) > 2 else 0),
                        'designation': row[3] if len(row) > 3 else '',
                        'deductions': _parse_num(row[4] if len(row) > 4 else 0),
                        'netSalary': _parse_num(row[5] if len(row) > 5 else 0),
                        'type': row[6] if len(row) > 6 else 'Staff',
                        'rowIndex': i,
                        'sheetType': 'staff',
                    })

        return employees

    # ---------- VALIDATE ----------

    def validate_employees(self):
        issues = []

        # Salary employees
        salary_employees = {}
        for row in self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:C")[1:]:
            if row and row[0]:
                salary_employees[row[0]] = {'name': row[1] if len(row) > 1 else '', 'type': 'labour'}
        for row in self._read(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A:C")[1:]:
            if row and row[0]:
                salary_employees[row[0]] = {'name': row[1] if len(row) > 1 else '', 'type': 'staff'}

        # Attendance employees
        att_employees = {}
        for row in self._read(ATTENDANCE_SHEET_ID, f"'{LABOUR_ATTENDANCE_SHEET}'!A:C")[1:]:
            if row and row[0]:
                att_employees[row[0]] = {'name': row[1] if len(row) > 1 else '', 'designation': row[2] if len(row) > 2 else '', 'type': 'labour'}
        for row in self._read(ATTENDANCE_SHEET_ID, f"'{STAFF_ATTENDANCE_SHEET}'!A:C")[1:]:
            if row and row[0]:
                att_employees[row[0]] = {'name': row[1] if len(row) > 1 else '', 'designation': row[2] if len(row) > 2 else '', 'type': 'staff'}

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

    # ---------- GET ONE EMPLOYEE ----------

    def get_employee(self, employee_id):
        rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:H")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(employee_id):
                return {
                    'id': row[0], 'employeeId': row[0], 'name': row[1],
                    'designation': row[2] if len(row) > 2 else '',
                    'ratePerDay': _parse_num(row[3] if len(row) > 3 else 0),
                    'ratePerHour': _parse_num(row[4] if len(row) > 4 else 0),
                    'otHours': _parse_num(row[5] if len(row) > 5 else 0),
                    'netSalary': _parse_num(row[6] if len(row) > 6 else 0),
                    'type': row[7] if len(row) > 7 else 'Labour',
                    'rowIndex': i, 'sheetType': 'labour',
                }

        rows = self._read(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A:G")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(employee_id):
                return {
                    'id': row[0], 'employeeId': row[0], 'name': row[1],
                    'ratePerDay': _parse_num(row[2] if len(row) > 2 else 0),
                    'designation': row[3] if len(row) > 3 else '',
                    'deductions': _parse_num(row[4] if len(row) > 4 else 0),
                    'netSalary': _parse_num(row[5] if len(row) > 5 else 0),
                    'type': row[6] if len(row) > 6 else 'Staff',
                    'rowIndex': i, 'sheetType': 'staff',
                }

        return None

    # ---------- GET NEXT ID ----------

    def get_next_employee_id(self, emp_type='labour'):
        sheet_name = STAFF_SALARY_SHEET if emp_type == 'staff' else LABOUR_SALARY_SHEET
        rows = self._read(SALARY_SHEET_ID, f"'{sheet_name}'!A:A")
        ids = [int(r[0]) for r in rows[1:] if r and r[0].isdigit()]
        return max(ids) + 1 if ids else 1

    # ---------- CREATE EMPLOYEE ----------

    def create_employee(self, data):
        is_staff = data.get('type', '').lower() == 'staff'
        sheet_name = STAFF_SALARY_SHEET if is_staff else LABOUR_SALARY_SHEET

        employee_id = data.get('employeeId')
        if not employee_id:
            employee_id = self.get_next_employee_id('staff' if is_staff else 'labour')

        rate_per_day = data.get('ratePerDay', '') or ''
        if is_staff:
            row = [str(employee_id), data.get('name', ''), rate_per_day,
                   data.get('designation', ''), data.get('deductions', ''),
                   data.get('netSalary', ''), 'Staff']
        else:
            rate_per_hour = data.get('ratePerHour', '')
            if not rate_per_hour and rate_per_day:
                try:
                    rate_per_hour = round(float(rate_per_day) / 8, 2)
                except (ValueError, TypeError):
                    rate_per_hour = ''
            row = [str(employee_id), data.get('name', ''), data.get('designation', ''),
                   rate_per_day, rate_per_hour, data.get('otHours', ''),
                   data.get('netSalary', ''), 'Labour']

        self._append(SALARY_SHEET_ID, f"'{sheet_name}'!A:H", [row])

        return {
            'id': str(employee_id), 'employeeId': str(employee_id),
            'name': data.get('name', ''), 'designation': data.get('designation', ''),
            'ratePerDay': _parse_num(data.get('ratePerDay', 0)),
            'ratePerHour': _parse_num(data.get('ratePerHour', 0)),
            'otHours': _parse_num(data.get('otHours', 0)),
            'netSalary': _parse_num(data.get('netSalary', 0)),
            'type': 'Staff' if is_staff else 'Labour',
            'sheetType': 'staff' if is_staff else 'labour',
        }

    # ---------- UPDATE EMPLOYEE ----------

    def update_employee(self, employee_id, data):
        employee_id = str(employee_id)

        # Labour sheet
        rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:H")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == employee_id:
                def v(key, idx):
                    return data[key] if key in data else (row[idx] if idx < len(row) else '')
                updated = [
                    employee_id,
                    data.get('name', row[1] if len(row) > 1 else ''),
                    v('designation', 2),
                    v('ratePerDay', 3),
                    v('ratePerHour', 4),
                    v('otHours', 5),
                    v('netSalary', 6),
                    row[7] if len(row) > 7 else 'Labour',
                ]
                self._write(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A{i}:H{i}", [updated])
                return {**data, 'id': employee_id, 'type': 'Labour'}

        # Staff sheet
        rows = self._read(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A:G")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == employee_id:
                def v(key, idx):
                    return data[key] if key in data else (row[idx] if idx < len(row) else '')
                updated = [
                    employee_id,
                    data.get('name', row[1] if len(row) > 1 else ''),
                    v('ratePerDay', 2),
                    v('designation', 3),
                    v('deductions', 4),
                    v('netSalary', 5),
                    row[6] if len(row) > 6 else 'Staff',
                ]
                self._write(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A{i}:G{i}", [updated])
                return {**data, 'id': employee_id, 'type': 'Staff'}

        return None

    # ---------- DELETE EMPLOYEE ----------

    def delete_employee(self, employee_id):
        employee_id = str(employee_id)

        rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:A")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == employee_id:
                self._write_raw(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A{i}:H{i}",
                                [['', '', '', '', '', '', '', '']])
                return f'Employee {employee_id} deleted from Labour'

        rows = self._read(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A:A")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == employee_id:
                self._write_raw(SALARY_SHEET_ID, f"'{STAFF_SALARY_SHEET}'!A{i}:G{i}",
                                [['', '', '', '', '', '', '']])
                return f'Employee {employee_id} deleted from Staff'

        return None

    # ---------- MOVE EMPLOYEE ----------

    def move_employee(self, employee_id, target_type):
        employee_id = str(employee_id)
        source_sheet = STAFF_SALARY_SHEET if target_type == 'labour' else LABOUR_SALARY_SHEET
        target_sheet = LABOUR_SALARY_SHEET if target_type == 'labour' else STAFF_SALARY_SHEET
        source_att   = STAFF_ATTENDANCE_SHEET if target_type == 'labour' else LABOUR_ATTENDANCE_SHEET
        target_att   = LABOUR_ATTENDANCE_SHEET if target_type == 'labour' else STAFF_ATTENDANCE_SHEET

        # Read source salary data
        src_rows = self._read(SALARY_SHEET_ID, f"'{source_sheet}'!A:H")
        emp_data = None
        src_row_idx = -1
        for i, row in enumerate(src_rows[1:], start=2):
            if row and row[0] == employee_id:
                emp_data = row
                src_row_idx = i
                break

        if emp_data is None:
            return None, f'Employee {employee_id} not found in {source_sheet}'

        # Convert data format between sheets
        if target_type == 'labour':
            # Staff -> Labour
            rate_per_day = _parse_num(emp_data[2] if len(emp_data) > 2 else 0)
            target_row = [
                emp_data[0], emp_data[1],
                emp_data[3] if len(emp_data) > 3 else '',
                emp_data[2] if len(emp_data) > 2 else '',
                round(rate_per_day / 8, 2) if rate_per_day else 0,
                '0', '', 'Labour'
            ]
        else:
            # Labour -> Staff
            target_row = [
                emp_data[0], emp_data[1],
                emp_data[3] if len(emp_data) > 3 else '',
                emp_data[2] if len(emp_data) > 2 else '',
                '0', '', 'Staff'
            ]

        # Append to target salary sheet
        tgt_rows = self._read(SALARY_SHEET_ID, f"'{target_sheet}'!A:A")
        next_row = len(tgt_rows) + 1
        self._write_raw(SALARY_SHEET_ID, f"'{target_sheet}'!A{next_row}", [target_row])

        # Clear from source salary sheet
        clear_vals = [[''] * (7 if target_type == 'labour' else 8)]
        self._write_raw(SALARY_SHEET_ID, f"'{source_sheet}'!A{src_row_idx}:{'G' if target_type == 'labour' else 'H'}{src_row_idx}", clear_vals)

        # Move attendance data
        att_rows = self._read(ATTENDANCE_SHEET_ID, f"'{source_att}'!A:AH")
        for i, row in enumerate(att_rows[1:], start=2):
            if row and row[0] == employee_id:
                tgt_att_rows = self._read(ATTENDANCE_SHEET_ID, f"'{target_att}'!A:A")
                next_att_row = len(tgt_att_rows) + 1
                self._write_raw(ATTENDANCE_SHEET_ID, f"'{target_att}'!A{next_att_row}", [row])
                clear_att = [[''] * len(row)]
                self._write_raw(ATTENDANCE_SHEET_ID, f"'{source_att}'!A{i}:AH{i}", clear_att)
                break

        return True, f'Employee {employee_id} moved to {target_sheet}'

    # ---------- FIX MISSING ----------

    def fix_missing_salary(self, employee_id, target_type, rate_per_day=0, rate_per_hour=0, ot_hours=0):
        employee_id = str(employee_id)
        att_sheet = LABOUR_ATTENDANCE_SHEET if target_type == 'labour' else STAFF_ATTENDANCE_SHEET
        sal_sheet = LABOUR_SALARY_SHEET if target_type == 'labour' else STAFF_SALARY_SHEET

        # Get from attendance
        att_rows = self._read(ATTENDANCE_SHEET_ID, f"'{att_sheet}'!A:C")
        emp_data = None
        for row in att_rows[1:]:
            if row and row[0] == employee_id:
                emp_data = {'id': row[0], 'name': row[1] if len(row) > 1 else '', 'designation': row[2] if len(row) > 2 else ''}
                break
        if emp_data is None:
            return None, f'Employee {employee_id} not found in {att_sheet}'

        # Check not already in salary
        sal_rows = self._read(SALARY_SHEET_ID, f"'{sal_sheet}'!A:A")
        for row in sal_rows[1:]:
            if row and row[0] == employee_id:
                return None, f'Employee {employee_id} already exists in {sal_sheet}'

        next_row = len(sal_rows) + 1
        if target_type == 'labour':
            row_data = [emp_data['id'], emp_data['name'], emp_data['designation'],
                        rate_per_day or 0, rate_per_hour or 0, ot_hours or 0, 0, 0]
        else:
            row_data = [emp_data['id'], emp_data['name'], emp_data['designation'],
                        rate_per_day or 0, rate_per_hour or 0, 0, 0]

        self._write_raw(SALARY_SHEET_ID, f"'{sal_sheet}'!A{next_row}", [row_data])
        return emp_data, None

    # ============================================================================
    # ATTENDANCE
    # ============================================================================

    def get_attendance(self, emp_type='all', month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1  # 0-indexed
        year = year if year is not None else now.year

        attendance = []

        def parse_rows(rows, sheet_type):
            for i, row in enumerate(rows[1:], start=2):
                if not row or not row[0]:
                    continue
                record = {
                    'employeeId': row[0],
                    'name': row[1] if len(row) > 1 else '',
                    'designation': row[2] if len(row) > 2 else '',
                    'rowIndex': i,
                    'type': sheet_type,
                    'days': {}
                }
                for day in range(1, 32):
                    col_idx = day + 2
                    record['days'][day] = row[col_idx] if col_idx < len(row) else ''
                attendance.append(record)

        if emp_type in ('labour', 'all'):
            sheet_name = _get_or_create_monthly_sheet(self.service, LABOUR_ATTENDANCE_SHEET, month, year)
            rows = self._read(ATTENDANCE_SHEET_ID, f"'{sheet_name}'!A:AH")
            parse_rows(rows, 'labour')

        if emp_type in ('staff', 'all'):
            try:
                sheet_name = _get_or_create_monthly_sheet(self.service, STAFF_ATTENDANCE_SHEET, month, year)
                rows = self._read(ATTENDANCE_SHEET_ID, f"'{sheet_name}'!A:AH")
                parse_rows(rows, 'staff')
            except Exception as e:
                print(f'Warning: staff attendance sheet error: {e}')

        return attendance

    def update_attendance(self, employee_id, day, status, emp_type='labour', month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        day = int(day)

        if day < 1 or day > 31:
            return None, 'Invalid day'

        base_name = STAFF_ATTENDANCE_SHEET if emp_type == 'staff' else LABOUR_ATTENDANCE_SHEET
        sheet_name = _get_or_create_monthly_sheet(self.service, base_name, month, year)

        rows = self._read(ATTENDANCE_SHEET_ID, f"'{sheet_name}'!A:A")
        target_row = None
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == str(employee_id):
                target_row = i
                break

        if target_row is None:
            return None, 'Employee not found in attendance sheet'

        col_letter = _get_column_letter(day + 3)
        self._write_raw(ATTENDANCE_SHEET_ID, f"'{sheet_name}'!{col_letter}{target_row}",
                        [[_normalize_status(status)]])
        return True, None

    def bulk_update_attendance(self, day, updates, emp_type=None, month=None, year=None):
        now = datetime.now()
        month = month if month is not None else now.month - 1
        year = year if year is not None else now.year
        day = int(day)

        if day < 1 or day > 31:
            return 0, 'Invalid day'

        col_letter = _get_column_letter(day + 3)

        # Group by sheet type
        grouped = {}
        if emp_type and emp_type != 'all':
            grouped[emp_type] = updates
        else:
            for upd in updates:
                t = upd.get('type', 'labour')
                grouped.setdefault(t, []).append(upd)

        total_updated = 0
        for sheet_type, sheet_updates in grouped.items():
            base_name = STAFF_ATTENDANCE_SHEET if sheet_type == 'staff' else LABOUR_ATTENDANCE_SHEET
            sheet_name = _get_or_create_monthly_sheet(self.service, base_name, month, year)

            rows = self._read(ATTENDANCE_SHEET_ID, f"'{sheet_name}'!A:A")
            row_map = {}
            for i, row in enumerate(rows[1:], start=2):
                if row and row[0]:
                    row_map[row[0]] = i

            batch_data = []
            for upd in sheet_updates:
                row_num = row_map.get(str(upd['employeeId']))
                if row_num:
                    batch_data.append({
                        'range': f"'{sheet_name}'!{col_letter}{row_num}",
                        'values': [[_normalize_status(upd['status'])]]
                    })

            if batch_data:
                self._batch_update(ATTENDANCE_SHEET_ID, batch_data, 'RAW')
                total_updated += len(batch_data)

        return total_updated, None

    # ============================================================================
    # PAYROLL
    # ============================================================================

    def _payroll_row_to_array(self, row, emp_type):
        payment_method = row.get('paymentMethod') or ('Cash' if row.get('isCash') else 'Bank Transfer')
        if emp_type == 'staff':
            return [row.get('employeeId'), row.get('name'), row.get('designation'),
                    row.get('deductionAmount'), row.get('paidDays'), row.get('ratePerHour'),
                    row.get('netSalary'), payment_method]
        return [row.get('employeeId'), row.get('name'), row.get('designation'),
                row.get('deductionAmount'), row.get('paidDays'), row.get('ratePerHour'),
                row.get('salaryBeforeOT'), row.get('otHours'), row.get('otPay'),
                row.get('netSalary'), payment_method]

    def write_payroll(self, month, year, emp_type, data):
        month_abbr = MONTH_ABBRS[int(month)]
        tab_name = f"{'Staff' if emp_type == 'staff' else 'Labour'} {month_abbr}"
        headers = STAFF_PAYROLL_HEADERS if emp_type == 'staff' else LABOUR_PAYROLL_HEADERS

        # Check/create tab
        spreadsheet = self.service.spreadsheets().get(spreadsheetId=PAYROLL_SHEET_ID).execute()
        existing_tabs = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]

        if tab_name not in existing_tabs:
            self.service.spreadsheets().batchUpdate(
                spreadsheetId=PAYROLL_SHEET_ID,
                body={'requests': [{'addSheet': {'properties': {'title': tab_name}}}]}
            ).execute()

        # Clear tab
        self.service.spreadsheets().values().clear(
            spreadsheetId=PAYROLL_SHEET_ID, range=f"'{tab_name}'!A:Z"
        ).execute()

        # Write header + data
        rows = [headers] + [self._payroll_row_to_array(r, emp_type) for r in data]
        self._write_raw(PAYROLL_SHEET_ID, f"'{tab_name}'!A1", rows)

        # Cash tab
        cash_data = [r for r in data if r.get('isCash') or r.get('paymentMethod') == 'Cash']
        if cash_data:
            cash_tab = f'Cash {month_abbr}'
            spreadsheet2 = self.service.spreadsheets().get(spreadsheetId=PAYROLL_SHEET_ID).execute()
            existing2 = [s['properties']['title'] for s in spreadsheet2.get('sheets', [])]
            if cash_tab not in existing2:
                self.service.spreadsheets().batchUpdate(
                    spreadsheetId=PAYROLL_SHEET_ID,
                    body={'requests': [{'addSheet': {'properties': {'title': cash_tab}}}]}
                ).execute()
            self.service.spreadsheets().values().clear(
                spreadsheetId=PAYROLL_SHEET_ID, range=f"'{cash_tab}'!A:Z"
            ).execute()
            cash_rows = [headers] + [self._payroll_row_to_array(r, emp_type) for r in cash_data]
            self._write_raw(PAYROLL_SHEET_ID, f"'{cash_tab}'!A1", cash_rows)

        return len(data), len(cash_data), tab_name, month_abbr

    def get_payroll(self, month, year, emp_type):
        month_abbr = MONTH_ABBRS[int(month)]
        tab_name = f"{'Staff' if emp_type == 'staff' else 'Labour'} {month_abbr}"
        is_staff = emp_type == 'staff'

        spreadsheet = self.service.spreadsheets().get(spreadsheetId=PAYROLL_SHEET_ID).execute()
        existing = [s['properties']['title'] for s in spreadsheet.get('sheets', [])]

        if tab_name not in existing:
            return [], 'No saved data for this period'

        rows = self._read(PAYROLL_SHEET_ID, f"'{tab_name}'!A:Z")
        if len(rows) <= 1:
            return [], 'No saved data for this period'

        result = []
        for row in rows[1:]:
            if is_staff:
                result.append({
                    'employeeId': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'designation': row[2] if len(row) > 2 else '',
                    'deductionAmount': _parse_num(row[3] if len(row) > 3 else 0),
                    'paidDays': _parse_num(row[4] if len(row) > 4 else 0),
                    'ratePerHour': _parse_num(row[5] if len(row) > 5 else 0),
                    'netSalary': _parse_num(row[6] if len(row) > 6 else 0),
                    'paymentMethod': row[7] if len(row) > 7 else 'Bank Transfer',
                    'isCash': (row[7] if len(row) > 7 else '') == 'Cash',
                })
            else:
                result.append({
                    'employeeId': row[0] if len(row) > 0 else '',
                    'name': row[1] if len(row) > 1 else '',
                    'designation': row[2] if len(row) > 2 else '',
                    'deductionAmount': _parse_num(row[3] if len(row) > 3 else 0),
                    'paidDays': _parse_num(row[4] if len(row) > 4 else 0),
                    'ratePerHour': _parse_num(row[5] if len(row) > 5 else 0),
                    'salaryBeforeOT': _parse_num(row[6] if len(row) > 6 else 0),
                    'otHours': _parse_num(row[7] if len(row) > 7 else 0),
                    'otPay': _parse_num(row[8] if len(row) > 8 else 0),
                    'netSalary': _parse_num(row[9] if len(row) > 9 else 0),
                    'paymentMethod': row[10] if len(row) > 10 else 'Bank Transfer',
                    'isCash': (row[10] if len(row) > 10 else '') == 'Cash',
                })

        return result, None

    # ============================================================================
    # OT / DEDUCTIONS
    # ============================================================================

    def update_employee_ot(self, employee_id, ot_hours):
        employee_id = str(employee_id)
        rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:A")
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0] == employee_id:
                self._write(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!F{i}", [[ot_hours]])
                return True
        return False

    def bulk_update_ot(self, updates):
        rows = self._read(SALARY_SHEET_ID, f"'{LABOUR_SALARY_SHEET}'!A:A")
        row_map = {}
        for i, row in enumerate(rows[1:], start=2):
            if row and row[0]:
                row_map[row[0]] = i

        batch_data = []
        for upd in updates:
            row_num = row_map.get(str(upd['employeeId']))
            if row_num:
                batch_data.append({
                    'range': f"'{LABOUR_SALARY_SHEET}'!F{row_num}",
                    'values': [[upd['otHours']]]
                })

        if batch_data:
            self._batch_update(SALARY_SHEET_ID, batch_data, 'USER_ENTERED')

        return len(batch_data)
