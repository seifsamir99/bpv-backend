"""
NewBPVService — unified BPV/PDC/CDC service using the new flat-format Google Sheet.

Sheet ID: NEW_BPV_SHEET_ID in .env
Tabs:
  BPV         — one row per line item, grouped by BPV_No on read
  PDC_Status  — cheque-level status tracking for PDCs
  CDC_Status  — cheque-level status tracking for CDCs
  Manual_PDC  — manually added PDC entries not derived from BPV
"""

import os
import json
import tempfile
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()


class NewBPVService:
    SHEET_ID = os.getenv('GOOGLE_SHEET_ID') or os.getenv('NEW_BPV_SHEET_ID', '1vmO1ydGwGupkeGIY3IyLGKWVmHk7YBXSPUf6833NqJU')

    TAB_BPV = 'BPV'
    TAB_PDC_STATUS = 'PDC_Status'
    TAB_CDC_STATUS = 'CDC_Status'
    TAB_MANUAL_PDC = 'Manual_PDC'

    # Column indices (0-based) for BPV tab
    BPV_COLS = ['BPV_No', 'Date', 'Type', 'SR_No', 'Description', 'Company',
                'Cheque_No', 'Cheque_Date', 'Debit', 'Credit']

    def __init__(self):
        self.service = self._get_service()

    # -------------------------------------------------------------------------
    # Auth
    # -------------------------------------------------------------------------

    def _get_credentials(self):
        scopes = ['https://www.googleapis.com/auth/spreadsheets']

        # 1. Railway split-key pattern (GOOGLE_SERVICE_ACCOUNT_EMAIL + GOOGLE_PRIVATE_KEY)
        email = os.getenv('GOOGLE_SERVICE_ACCOUNT_EMAIL')
        private_key = os.getenv('GOOGLE_PRIVATE_KEY')
        if email and private_key:
            info = {
                "type": "service_account",
                "client_email": email,
                "private_key": private_key.replace('\\n', '\n'),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)

        # 2. Full JSON env var (GOOGLE_CREDENTIALS_JSON)
        creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
        if creds_json:
            info = json.loads(creds_json)
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)

        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)

        # 3. OAuth token file (local dev)
        for token_path in ['token.json', os.path.join(parent_dir, 'token.json')]:
            if os.path.exists(token_path):
                return Credentials.from_authorized_user_file(token_path, scopes)

        # 4. Service account file (local dev)
        for creds_path in ['credentials.json', os.path.join(parent_dir, 'credentials.json')]:
            if os.path.exists(creds_path):
                return service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)

        raise Exception("No credentials found. Set GOOGLE_CREDENTIALS_JSON env var or provide credentials.json")

    def _get_service(self):
        return build('sheets', 'v4', credentials=self._get_credentials())

    # -------------------------------------------------------------------------
    # Low-level helpers
    # -------------------------------------------------------------------------

    def _read_tab(self, tab_name):
        """Read all rows from a tab. Returns list of dicts using first row as headers."""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.SHEET_ID,
            range=f"'{tab_name}'"
        ).execute()
        rows = result.get('values', [])
        if len(rows) < 1:
            return []
        headers = rows[0]
        data = []
        for i, row in enumerate(rows[1:], start=2):  # start=2 = actual sheet row
            padded = row + [''] * (len(headers) - len(row))
            data.append({'_row': i, **dict(zip(headers, padded))})
        return data

    def _append_rows(self, tab_name, rows):
        """Append a list of value-lists to a tab."""
        if not rows:
            return
        self.service.spreadsheets().values().append(
            spreadsheetId=self.SHEET_ID,
            range=f"'{tab_name}'",
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': rows}
        ).execute()

    def _delete_rows(self, tab_name, row_indices):
        """Delete specific rows by 1-based sheet row index (sorted descending to avoid shifting)."""
        if not row_indices:
            return
        sheet_id = self._get_sheet_id(tab_name)
        requests = []
        for row_index in sorted(row_indices, reverse=True):
            requests.append({
                'deleteDimension': {
                    'range': {
                        'sheetId': sheet_id,
                        'dimension': 'ROWS',
                        'startIndex': row_index - 1,  # 0-based
                        'endIndex': row_index          # exclusive
                    }
                }
            })
        self.service.spreadsheets().batchUpdate(
            spreadsheetId=self.SHEET_ID,
            body={'requests': requests}
        ).execute()

    def _get_sheet_id(self, tab_name):
        """Get numeric sheetId for a tab by name."""
        spreadsheet = self.service.spreadsheets().get(spreadsheetId=self.SHEET_ID).execute()
        for sheet in spreadsheet.get('sheets', []):
            if sheet['properties']['title'] == tab_name:
                return sheet['properties']['sheetId']
        raise Exception(f"Tab '{tab_name}' not found in sheet")

    def _update_cell_range(self, range_str, values):
        self.service.spreadsheets().values().update(
            spreadsheetId=self.SHEET_ID,
            range=range_str,
            valueInputOption='RAW',
            body={'values': values}
        ).execute()

    # -------------------------------------------------------------------------
    # BPV operations
    # -------------------------------------------------------------------------

    def get_all_vouchers(self):
        """Return all vouchers as a list of dicts, grouped by BPV_No."""
        rows = self._read_tab(self.TAB_BPV)
        return self._group_to_vouchers(rows)

    def get_voucher(self, bpv_no):
        """Return a single voucher by BPV_No, or None."""
        rows = self._read_tab(self.TAB_BPV)
        bpv_rows = [r for r in rows if str(r.get('BPV_No', '')).strip() == str(bpv_no).strip()]
        if not bpv_rows:
            return None
        vouchers = self._group_to_vouchers(bpv_rows)
        return vouchers[0] if vouchers else None

    def get_next_bpv_number(self):
        """Return the next available BPV number (int)."""
        rows = self._read_tab(self.TAB_BPV)
        max_num = 0
        for row in rows:
            raw = str(row.get('BPV_No', '')).strip()
            try:
                num = int(raw)
                if num > max_num:
                    max_num = num
            except ValueError:
                pass
        return max_num + 1

    def create_voucher(self, data):
        """Create a new BPV voucher by appending one row per line item."""
        bpv_no = str(data.get('bpvNo', '')).strip()
        date = data.get('date', '')
        pdc_type = data.get('pdcType', 'PDC')
        line_items = data.get('lineItems', [])

        if not line_items:
            # Create a single blank-ish row so the voucher exists
            line_items = [{'srNo': 1, 'description': '', 'companyName': '',
                           'chequeNo': '', 'chequeDate': '', 'debit': 0, 'credit': 0}]

        rows = []
        for item in line_items:
            rows.append([
                bpv_no,
                date,
                pdc_type,
                str(item.get('srNo', '')),
                item.get('description', ''),
                item.get('companyName', ''),
                item.get('chequeNo', ''),
                item.get('chequeDate', ''),
                str(item.get('debit', 0)),
                str(item.get('credit', 0)),
            ])

        self._append_rows(self.TAB_BPV, rows)
        # Return constructed voucher dict without re-reading from sheet
        return {
            'bpvNo': bpv_no,
            'date': date,
            'pdcType': pdc_type,
            'lineItems': line_items,
            'totalAmount': self.calculate_total(line_items),
            'hasData': True,
        }

    def update_voucher(self, bpv_no, data):
        """Update an existing BPV voucher: delete its rows then re-insert."""
        existing = self._read_tab(self.TAB_BPV)
        row_indices = [r['_row'] for r in existing
                       if str(r.get('BPV_No', '')).strip() == str(bpv_no).strip()]
        self._delete_rows(self.TAB_BPV, row_indices)

        # Merge bpvNo from URL param into data
        data['bpvNo'] = bpv_no
        return self.create_voucher(data)

    def delete_voucher(self, bpv_no):
        """Delete all rows for a given BPV_No."""
        existing = self._read_tab(self.TAB_BPV)
        row_indices = [r['_row'] for r in existing
                       if str(r.get('BPV_No', '')).strip() == str(bpv_no).strip()]
        if not row_indices:
            return {'success': False, 'error': f'Voucher {bpv_no} not found'}
        self._delete_rows(self.TAB_BPV, row_indices)
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

    def _group_to_vouchers(self, rows):
        """Group flat BPV rows into voucher dicts keyed by BPV_No."""
        vouchers_map = {}
        order = []
        for row in rows:
            bpv_no = str(row.get('BPV_No', '')).strip()
            if not bpv_no:
                continue
            if bpv_no not in vouchers_map:
                order.append(bpv_no)
                vouchers_map[bpv_no] = {
                    'bpvNo': bpv_no,
                    'date': row.get('Date', ''),
                    'pdcType': row.get('Type', 'PDC'),
                    'lineItems': [],
                    'totalAmount': 0,
                    'hasData': True,
                }
            item = {
                'srNo': row.get('SR_No', ''),
                'description': row.get('Description', ''),
                'companyName': row.get('Company', ''),
                'chequeNo': row.get('Cheque_No', ''),
                'chequeDate': row.get('Cheque_Date', ''),
                'debit': _to_float(row.get('Debit', 0)),
                'credit': _to_float(row.get('Credit', 0)),
            }
            vouchers_map[bpv_no]['lineItems'].append(item)
            vouchers_map[bpv_no]['totalAmount'] += item['debit']

        return [vouchers_map[k] for k in order]

    # -------------------------------------------------------------------------
    # PDC status operations
    # -------------------------------------------------------------------------

    def get_pdc_statuses(self):
        """Return dict {cheque_no: status} for all PDC statuses."""
        rows = self._read_tab(self.TAB_PDC_STATUS)
        return {r['Cheque_No']: r['Status'] for r in rows if r.get('Cheque_No')}

    def update_pdc_status(self, cheque_no, status):
        """Upsert status for a cheque in PDC_Status tab."""
        rows = self._read_tab(self.TAB_PDC_STATUS)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for row in rows:
            if str(row.get('Cheque_No', '')).strip() == str(cheque_no).strip():
                # Update existing row in-place
                sheet_row = row['_row']
                self._update_cell_range(
                    f"'{self.TAB_PDC_STATUS}'!A{sheet_row}:C{sheet_row}",
                    [[cheque_no, status, now]]
                )
                return {'success': True, 'action': 'updated'}

        # Not found → append
        self._append_rows(self.TAB_PDC_STATUS, [[cheque_no, status, now]])
        return {'success': True, 'action': 'created'}

    def bulk_update_pdc_status(self, updates):
        """updates: list of {chequeNo, status} dicts."""
        results = []
        for u in updates:
            r = self.update_pdc_status(u['chequeNo'], u['status'])
            results.append(r)
        return {'success': True, 'updated': len(results)}

    # -------------------------------------------------------------------------
    # Manual PDC operations
    # -------------------------------------------------------------------------

    def get_manual_pdcs(self):
        """Return all manual PDC entries."""
        rows = self._read_tab(self.TAB_MANUAL_PDC)
        return [_strip_meta(r) for r in rows]

    def add_manual_pdc(self, data):
        """Append a new manual PDC entry."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        existing = self._read_tab(self.TAB_MANUAL_PDC)
        new_id = len(existing) + 1

        row = [
            str(new_id),
            data.get('company', ''),
            data.get('chequeNo', ''),
            data.get('chequeDate', ''),
            str(data.get('amount', 0)),
            data.get('description', ''),
            data.get('status', 'Not Released'),
            now,
        ]
        self._append_rows(self.TAB_MANUAL_PDC, [row])
        return {'success': True, 'id': new_id}

    def delete_manual_pdc(self, row_index):
        """Delete a manual PDC by its 1-based data row index (not sheet row).
        row_index=1 deletes the first data row (sheet row 2)."""
        sheet_row = int(row_index) + 1  # +1 for header row
        self._delete_rows(self.TAB_MANUAL_PDC, [sheet_row])
        return {'success': True}

    # -------------------------------------------------------------------------
    # CDC status operations
    # -------------------------------------------------------------------------

    def get_cdc_statuses(self):
        """Return dict {cheque_no: status} for all CDC statuses."""
        rows = self._read_tab(self.TAB_CDC_STATUS)
        return {r['Cheque_No']: r['Status'] for r in rows if r.get('Cheque_No')}

    def update_cdc_status(self, cheque_no, status):
        """Upsert status for a cheque in CDC_Status tab."""
        rows = self._read_tab(self.TAB_CDC_STATUS)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        for row in rows:
            if str(row.get('Cheque_No', '')).strip() == str(cheque_no).strip():
                sheet_row = row['_row']
                self._update_cell_range(
                    f"'{self.TAB_CDC_STATUS}'!A{sheet_row}:C{sheet_row}",
                    [[cheque_no, status, now]]
                )
                return {'success': True, 'action': 'updated'}

        self._append_rows(self.TAB_CDC_STATUS, [[cheque_no, status, now]])
        return {'success': True, 'action': 'created'}

    def bulk_update_cdc_status(self, updates):
        """updates: list of {chequeNo, status} dicts."""
        results = []
        for u in updates:
            r = self.update_cdc_status(u['chequeNo'], u['status'])
            results.append(r)
        return {'success': True, 'updated': len(results)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(val):
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _strip_meta(row):
    """Remove internal _row key before returning to client."""
    return {k: v for k, v in row.items() if k != '_row'}
