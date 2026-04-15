"""
Core Google Sheets operations for Purchase & Accounting System
Handles CRUD operations for all entities
"""

import os
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from googleapiclient.discovery import build
from dotenv import load_dotenv

load_dotenv()


class SheetsDB:
    """Google Sheets database operations"""

    def __init__(self, sheet_id=None):
        self.sheet_id = sheet_id or os.getenv('GOOGLE_SHEET_ID')
        if not self.sheet_id:
            raise ValueError("GOOGLE_SHEET_ID not set in environment")

        self.service = self._get_service()

    def _get_credentials(self):
        """Get Google API credentials"""
        scopes = ['https://www.googleapis.com/auth/spreadsheets']

        # 1. Railway split-key pattern
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

        # 2. Full JSON env var
        import json
        creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
        if creds_json:
            info = json.loads(creds_json)
            return service_account.Credentials.from_service_account_info(info, scopes=scopes)

        # 3. Local files (dev)
        script_dir = os.path.dirname(os.path.abspath(__file__))
        parent_dir = os.path.dirname(script_dir)

        for token_path in ['token.json', os.path.join(parent_dir, 'token.json')]:
            if os.path.exists(token_path):
                return Credentials.from_authorized_user_file(token_path, scopes)

        for creds_path in ['credentials.json', os.path.join(parent_dir, 'credentials.json')]:
            if os.path.exists(creds_path):
                return service_account.Credentials.from_service_account_file(creds_path, scopes=scopes)

        raise Exception("No valid credentials found")

    def _get_service(self):
        """Build Google Sheets service"""
        creds = self._get_credentials()
        return build('sheets', 'v4', credentials=creds)

    def _get_next_id(self, entity_type, prefix):
        """Generate next ID for entity type (e.g., QT-2026-01-001)"""
        now = datetime.now()
        year = now.strftime("%Y")
        month = now.strftime("%m")

        # Get current counter
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range="Counters!A2:D100"
        ).execute()

        rows = result.get('values', [])

        # Find or create counter row
        counter_row = None
        row_index = 2

        for i, row in enumerate(rows):
            if len(row) > 0 and row[0] == entity_type:
                counter_row = row
                row_index = i + 2
                break

        if counter_row:
            last_number = int(counter_row[1]) if len(counter_row) > 1 else 0
            last_year = counter_row[2] if len(counter_row) > 2 else ""
            last_month = counter_row[3] if len(counter_row) > 3 else ""

            # Reset counter if month/year changed
            if last_year != year or last_month != month:
                next_number = 1
            else:
                next_number = last_number + 1
        else:
            next_number = 1

        # Update counter
        new_row = [entity_type, str(next_number), year, month]
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=f"Counters!A{row_index}:D{row_index}",
            valueInputOption='RAW',
            body={'values': [new_row]}
        ).execute()

        # Generate ID
        entity_id = f"{prefix}-{year}-{month}-{next_number:03d}"
        return entity_id

    def _log_audit(self, user, action, entity_type, entity_id, changes):
        """Add entry to audit log"""
        timestamp = datetime.now().isoformat()
        log_id = self._get_next_id("Audit Log", "LOG")

        row = [
            log_id,
            timestamp,
            user,
            action,
            entity_type,
            entity_id,
            str(changes)
        ]

        self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range="Audit Log!A2",
            valueInputOption='RAW',
            body={'values': [row]}
        ).execute()

    def get_all(self, sheet_name):
        """Get all rows from a sheet"""
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1:ZZ10000"
        ).execute()

        rows = result.get('values', [])
        if not rows:
            return []

        headers = rows[0]
        data = []

        for row in rows[1:]:
            # Pad row to match headers length
            while len(row) < len(headers):
                row.append("")

            item = dict(zip(headers, row))
            data.append(item)

        return data

    def get_by_id(self, sheet_name, id_column, id_value):
        """Get single row by ID"""
        all_rows = self.get_all(sheet_name)

        for row in all_rows:
            if row.get(id_column) == id_value:
                return row

        return None

    def filter(self, sheet_name, filters):
        """Filter rows by multiple criteria
        Example: filters = {'Status': 'Pending', 'Vendor ID': 'V001'}
        """
        all_rows = self.get_all(sheet_name)
        results = []

        for row in all_rows:
            match = True
            for key, value in filters.items():
                if row.get(key) != value:
                    match = False
                    break
            if match:
                results.append(row)

        return results

    def create(self, sheet_name, data, user="System"):
        """Create new row"""
        # Get headers
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1:ZZ1"
        ).execute()

        headers = result.get('values', [[]])[0]

        # Build row from data dict
        row = []
        for header in headers:
            row.append(data.get(header, ""))

        # Append row - use USER_ENTERED so dates are interpreted correctly
        self.service.spreadsheets().values().append(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A2",
            valueInputOption='USER_ENTERED',
            body={'values': [row]}
        ).execute()

        # Log audit
        entity_id = data.get(headers[0], "")  # First column is usually ID
        self._log_audit(user, "Create", sheet_name, entity_id, data)

        return data

    def update(self, sheet_name, id_column, id_value, updates, user="System"):
        """Update existing row"""
        # Get all rows
        result = self.service.spreadsheets().values().get(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A1:ZZ10000"
        ).execute()

        rows = result.get('values', [])
        if not rows:
            raise ValueError("Sheet is empty")

        headers = rows[0]
        id_col_index = headers.index(id_column)

        # Find row to update
        row_index = None
        for i, row in enumerate(rows[1:], start=2):
            if len(row) > id_col_index and row[id_col_index] == id_value:
                row_index = i
                break

        if row_index is None:
            raise ValueError(f"Row with {id_column}={id_value} not found")

        # Build updated row
        existing_row = rows[row_index - 1]
        updated_row = existing_row.copy()

        # Pad to match headers length
        while len(updated_row) < len(headers):
            updated_row.append("")

        for key, value in updates.items():
            if key in headers:
                col_index = headers.index(key)
                updated_row[col_index] = value

        # Update row - use USER_ENTERED so dates are interpreted correctly
        self.service.spreadsheets().values().update(
            spreadsheetId=self.sheet_id,
            range=f"{sheet_name}!A{row_index}:ZZ{row_index}",
            valueInputOption='USER_ENTERED',
            body={'values': [updated_row]}
        ).execute()

        # Log audit
        self._log_audit(user, "Update", sheet_name, id_value, updates)

        return dict(zip(headers, updated_row))

    def delete(self, sheet_name, id_column, id_value, user="System"):
        """Delete row (soft delete by setting status or hard delete)"""
        # For now, we'll do soft delete by updating status
        # Hard delete can be implemented later if needed
        self.update(sheet_name, id_column, id_value,
                   {"Status": "Deleted"}, user)

        self._log_audit(user, "Delete", sheet_name, id_value, {})

    # Specialized methods for business logic

    def create_lpo(self, supplier_id, lpo_date, line_items, expected_delivery_date,
                   user, **kwargs):
        """Create LPO directly with supplier and line items"""
        # Get supplier
        supplier = self.get_by_id("Suppliers", "Supplier ID", supplier_id)
        if not supplier:
            raise ValueError(f"Supplier {supplier_id} not found")

        # Generate LPO ID
        lpo_id = self._get_next_id("LPO", "LPO")

        # Calculate amounts
        subtotal = sum(item['quantity'] * item['unit_price'] for item in line_items)
        vat_percent = 5  # UAE VAT is 5%
        vat_amount = subtotal * (vat_percent / 100)
        total_amount = subtotal + vat_amount

        # Create LPO
        lpo_data = {
            "LPO ID": lpo_id,
            "Supplier ID": supplier_id,
            "Supplier Name": supplier.get("Supplier Name", ""),
            "LPO Date": lpo_date,
            "Status": "Pending Approval",
            "Subtotal (AED)": subtotal,
            "VAT %": vat_percent,
            "VAT Amount (AED)": vat_amount,
            "Total Amount (AED)": total_amount,
            "Payment Terms": supplier.get("Payment Terms (Days)", ""),
            "Expected Delivery Date": expected_delivery_date,
            "Created By": user,
            "Created Date": datetime.now().isoformat(),
            **kwargs
        }

        self.create("LPOs", lpo_data, user)

        # Create LPO line items
        for item in line_items:
            line_id = self._get_next_id("Line Item", "LI")
            lpo_line_data = {
                "Line Item ID": line_id,
                "LPO ID": lpo_id,
                "Item Description": item['description'],
                "Quantity Ordered": item['quantity'],
                "Quantity Delivered": "0",
                "Unit": item['unit'],
                "Unit Price (AED)": item['unit_price'],
                "Total Price (AED)": item['quantity'] * item['unit_price'],
                "Notes": item.get('notes', '')
            }
            self.create("LPO Line Items", lpo_line_data, user)

        return lpo_id

    def create_supplier(self, supplier_name, contact_person, email, phone,
                       address, payment_terms, trn, user, **kwargs):
        """Create new supplier"""
        supplier_id = self._get_next_id("Supplier", "S")

        supplier_data = {
            "Supplier ID": supplier_id,
            "Supplier Name": supplier_name,
            "Contact Person": contact_person,
            "Email": email,
            "Phone": phone,
            "Address": address,
            "Payment Terms (Days)": payment_terms,
            "Tax Registration Number (TRN)": trn,
            "Status": "Active",
            "Created Date": datetime.now().isoformat(),
            **kwargs
        }

        self.create("Suppliers", supplier_data, user)
        return supplier_id

    def create_cheque(self, invoice_id, cheque_number, bank_name,
                     cheque_date, user, **kwargs):
        """Create cheque for invoice"""
        # Get invoice
        invoice = self.get_by_id("Tax Invoices", "Invoice ID", invoice_id)
        if not invoice:
            raise ValueError(f"Invoice {invoice_id} not found")

        cheque_id = self._get_next_id("Cheque", "CHQ")

        cheque_data = {
            "Cheque ID": cheque_id,
            "Invoice ID": invoice_id,
            "Supplier ID": invoice["Supplier ID"],
            "Supplier Name": invoice["Supplier Name"],
            "Cheque Number": cheque_number,
            "Bank Name": bank_name,
            "Cheque Date": cheque_date,
            "Amount (AED)": invoice["Total Amount (AED)"],
            "Status": "Created",
            "Created Date": datetime.now().isoformat(),
            **kwargs
        }

        self.create("Cheques", cheque_data, user)
        return cheque_id


if __name__ == "__main__":
    # Test operations
    db = SheetsDB()

    # Example: Get all suppliers
    suppliers = db.get_all("Suppliers")
    print(f"Found {len(suppliers)} suppliers")

    # Example: Filter pending LPOs
    pending_lpos = db.filter("LPOs", {"Status": "Pending Approval"})
    print(f"Found {len(pending_lpos)} pending LPOs")
