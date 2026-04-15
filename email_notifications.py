"""
Email notification system for Purchase & Accounting System
Sends email alerts for approvals, overdue items, and important events
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sheets_operations import SheetsDB

load_dotenv()


class EmailNotifier:
    """Send email notifications"""

    def __init__(self):
        self.smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = int(os.getenv('SMTP_PORT', 587))
        self.smtp_user = os.getenv('SMTP_USER')
        self.smtp_password = os.getenv('SMTP_PASSWORD')
        self.from_email = os.getenv('NOTIFICATION_EMAIL', self.smtp_user)
        self.company_name = os.getenv('COMPANY_NAME', 'Company')
        self.enabled = bool(self.smtp_user and self.smtp_password)

        if not self.enabled:
            print("WARNING: Email notifications disabled (SMTP credentials not configured)")

    def send_email(self, to_email, subject, html_body, plain_body=None):
        """Send HTML email"""
        if not self.enabled:
            print(f"Email skipped (not configured): {subject} to {to_email}")
            return False

        msg = MIMEMultipart('alternative')
        msg['From'] = self.from_email
        msg['To'] = to_email
        msg['Subject'] = subject

        # Plain text fallback
        if plain_body:
            part1 = MIMEText(plain_body, 'plain')
            msg.attach(part1)

        # HTML content
        part2 = MIMEText(html_body, 'html')
        msg.attach(part2)

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                server.sendmail(self.from_email, to_email, msg.as_string())
            return True
        except Exception as e:
            print(f"Failed to send email: {str(e)}")
            return False

    def notify_lpo_pending_approval(self, lpo_data, approver_email):
        """Notify approver about pending LPO"""
        subject = f"LPO Approval Required: {lpo_data['LPO ID']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #2563eb; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #2563eb; }}
                .button {{ display: inline-block; background-color: #2563eb; color: white; padding: 12px 24px; text-decoration: none; border-radius: 4px; margin-top: 15px; }}
                .footer {{ text-align: center; color: #6b7280; font-size: 12px; margin-top: 20px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>LPO Approval Required</h2>
                </div>
                <div class="content">
                    <p>A new Local Purchase Order requires your approval:</p>
                    <div class="details">
                        <p><strong>LPO Number:</strong> {lpo_data['LPO ID']}</p>
                        <p><strong>Vendor:</strong> {lpo_data['Vendor Name']}</p>
                        <p><strong>Total Amount:</strong> {lpo_data['Total Amount (AED)']} AED</p>
                        <p><strong>Created By:</strong> {lpo_data['Created By']}</p>
                        <p><strong>Expected Delivery:</strong> {lpo_data['Expected Delivery Date']}</p>
                    </div>
                    <p>Please review and approve this purchase order at your earliest convenience.</p>
                    <a href="#" class="button">Review LPO</a>
                </div>
                <div class="footer">
                    <p>This is an automated notification from {self.company_name} Purchase System</p>
                </div>
            </div>
        </body>
        </html>
        """

        plain_body = f"""
        LPO Approval Required

        A new Local Purchase Order requires your approval:

        LPO Number: {lpo_data['LPO ID']}
        Vendor: {lpo_data['Vendor Name']}
        Total Amount: {lpo_data['Total Amount (AED)']} AED
        Created By: {lpo_data['Created By']}
        Expected Delivery: {lpo_data['Expected Delivery Date']}

        Please review and approve this purchase order.
        """

        return self.send_email(approver_email, subject, html_body, plain_body)

    def notify_lpo_approved(self, lpo_data, creator_email):
        """Notify creator that LPO was approved"""
        subject = f"LPO Approved: {lpo_data['LPO ID']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #10b981; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #10b981; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>✓ LPO Approved</h2>
                </div>
                <div class="content">
                    <p>Your Local Purchase Order has been approved:</p>
                    <div class="details">
                        <p><strong>LPO Number:</strong> {lpo_data['LPO ID']}</p>
                        <p><strong>Vendor:</strong> {lpo_data['Vendor Name']}</p>
                        <p><strong>Total Amount:</strong> {lpo_data['Total Amount (AED)']} AED</p>
                        <p><strong>Approved By:</strong> {lpo_data.get('Approved By', '')}</p>
                        <p><strong>Approval Date:</strong> {lpo_data.get('Approved Date', '')}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(creator_email, subject, html_body)

    def notify_delivery_overdue(self, lpo_data, team_email):
        """Notify team about overdue delivery"""
        subject = f"Overdue Delivery: {lpo_data['LPO ID']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #ef4444; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #ef4444; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>⚠️ Delivery Overdue</h2>
                </div>
                <div class="content">
                    <p>The following purchase order has an overdue delivery:</p>
                    <div class="details">
                        <p><strong>LPO Number:</strong> {lpo_data['LPO ID']}</p>
                        <p><strong>Vendor:</strong> {lpo_data['Vendor Name']}</p>
                        <p><strong>Expected Delivery:</strong> {lpo_data['Expected Delivery Date']}</p>
                        <p><strong>Status:</strong> {lpo_data['Status']}</p>
                    </div>
                    <p>Please follow up with the vendor to check on delivery status.</p>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(team_email, subject, html_body)

    def notify_invoice_due_soon(self, invoice_data, finance_email):
        """Notify finance team about upcoming invoice due date"""
        subject = f"Invoice Due Soon: {invoice_data['Invoice ID']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #f59e0b; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #f59e0b; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Invoice Due Soon</h2>
                </div>
                <div class="content">
                    <p>The following invoice is due within 3 days:</p>
                    <div class="details">
                        <p><strong>Invoice Number:</strong> {invoice_data['Invoice ID']}</p>
                        <p><strong>Vendor:</strong> {invoice_data['Vendor Name']}</p>
                        <p><strong>Amount:</strong> {invoice_data['Total Amount (AED)']} AED</p>
                        <p><strong>Due Date:</strong> {invoice_data['Due Date']}</p>
                    </div>
                    <p>Please arrange payment to avoid late fees.</p>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(finance_email, subject, html_body)

    def notify_cheque_needs_approval(self, cheque_data, approver_email):
        """Notify approver about cheque awaiting approval"""
        subject = f"Cheque Approval Required: {cheque_data['Cheque ID']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #2563eb; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #2563eb; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Cheque Approval Required</h2>
                </div>
                <div class="content">
                    <p>A cheque payment requires your approval:</p>
                    <div class="details">
                        <p><strong>Cheque Number:</strong> {cheque_data['Cheque Number']}</p>
                        <p><strong>Vendor:</strong> {cheque_data['Vendor Name']}</p>
                        <p><strong>Amount:</strong> {cheque_data['Amount (AED)']} AED</p>
                        <p><strong>Bank:</strong> {cheque_data['Bank Name']}</p>
                        <p><strong>Cheque Date:</strong> {cheque_data['Cheque Date']}</p>
                        <p><strong>Invoice Reference:</strong> {cheque_data['Invoice ID']}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(approver_email, subject, html_body)

    def notify_cheque_released(self, cheque_data, finance_email):
        """Notify finance team that cheque was released"""
        subject = f"Cheque Released: {cheque_data['Cheque Number']}"

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: #10b981; color: white; padding: 20px; text-align: center; }}
                .content {{ background-color: #f9fafb; padding: 20px; }}
                .details {{ background-color: white; padding: 15px; margin: 10px 0; border-left: 4px solid #10b981; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>Cheque Released</h2>
                </div>
                <div class="content">
                    <p>A cheque has been released to the vendor:</p>
                    <div class="details">
                        <p><strong>Cheque Number:</strong> {cheque_data['Cheque Number']}</p>
                        <p><strong>Vendor:</strong> {cheque_data['Vendor Name']}</p>
                        <p><strong>Amount:</strong> {cheque_data['Amount (AED)']} AED</p>
                        <p><strong>Released By:</strong> {cheque_data.get('Released By', '')}</p>
                        <p><strong>Release Date:</strong> {cheque_data.get('Released Date', '')}</p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """

        return self.send_email(finance_email, subject, html_body)


def check_and_send_notifications():
    """Check database and send notifications for various conditions"""
    db = SheetsDB()
    notifier = EmailNotifier()

    finance_email = os.getenv('FINANCE_EMAIL', os.getenv('NOTIFICATION_EMAIL'))
    approver_email = os.getenv('APPROVER_EMAIL', os.getenv('NOTIFICATION_EMAIL'))

    # Check for pending LPO approvals
    pending_lpos = db.filter("LPOs", {"Status": "Pending Approval"})
    for lpo in pending_lpos:
        # Check if notification already sent today (you'd track this in audit log)
        notifier.notify_lpo_pending_approval(lpo, approver_email)

    # Check for overdue deliveries
    today = datetime.now().date()
    all_lpos = db.get_all("LPOs")
    for lpo in all_lpos:
        if lpo['Status'] in ['Approved', 'Partially Delivered']:
            expected_date_str = lpo.get('Expected Delivery Date', '')
            if expected_date_str:
                try:
                    expected_date = datetime.strptime(expected_date_str, '%Y-%m-%d').date()
                    if expected_date < today:
                        notifier.notify_delivery_overdue(lpo, finance_email)
                except:
                    pass

    # Check for invoices due soon (within 3 days)
    three_days_out = today + timedelta(days=3)
    all_invoices = db.get_all("Tax Invoices")
    for invoice in all_invoices:
        if invoice['Status'] == 'Approved':
            due_date_str = invoice.get('Due Date', '')
            if due_date_str:
                try:
                    due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                    if today <= due_date <= three_days_out:
                        notifier.notify_invoice_due_soon(invoice, finance_email)
                except:
                    pass

    # Check for pending cheque approvals
    pending_cheques = db.filter("Cheques", {"Status": "Created"})
    for cheque in pending_cheques:
        notifier.notify_cheque_needs_approval(cheque, approver_email)

    print(f"Notification check complete at {datetime.now()}")


if __name__ == "__main__":
    check_and_send_notifications()
