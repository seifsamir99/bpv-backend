"""
Reporting system for Purchase & Accounting
Generates reports for outstanding LPOs, pending cheques, cash flow, supplier analysis
"""

import os
from datetime import datetime, timedelta
from sheets_operations import SheetsDB
from dotenv import load_dotenv
import json

load_dotenv()


class ReportGenerator:
    """Generate various business reports"""

    def __init__(self):
        self.db = SheetsDB()

    def outstanding_lpos_report(self):
        """Report on all LPOs that are not fully delivered"""
        all_lpos = self.db.get_all("LPOs")

        outstanding = []
        for lpo in all_lpos:
            if lpo['Status'] in ['Approved', 'Partially Delivered']:
                # Get line items to check delivery status
                line_items = self.db.filter("LPO Line Items", {"LPO ID": lpo['LPO ID']})

                total_ordered = 0
                total_delivered = 0

                for item in line_items:
                    qty_ordered = float(item.get('Quantity Ordered', 0))
                    qty_delivered = float(item.get('Quantity Delivered', 0))
                    total_ordered += qty_ordered
                    total_delivered += qty_delivered

                completion_pct = (total_delivered / total_ordered * 100) if total_ordered > 0 else 0

                # Calculate days overdue
                expected_date_str = lpo.get('Expected Delivery Date', '')
                days_overdue = 0
                if expected_date_str:
                    try:
                        expected_date = datetime.strptime(expected_date_str, '%Y-%m-%d').date()
                        today = datetime.now().date()
                        if expected_date < today:
                            days_overdue = (today - expected_date).days
                    except:
                        pass

                outstanding.append({
                    'lpo_id': lpo['LPO ID'],
                    'supplier_name': lpo['Supplier Name'],
                    'lpo_date': lpo['LPO Date'],
                    'expected_delivery': lpo.get('Expected Delivery Date', ''),
                    'status': lpo['Status'],
                    'total_amount': float(lpo.get('Total Amount (AED)', 0)),
                    'completion_percentage': round(completion_pct, 1),
                    'days_overdue': days_overdue,
                    'total_ordered': total_ordered,
                    'total_delivered': total_delivered
                })

        # Sort by days overdue (descending)
        outstanding.sort(key=lambda x: x['days_overdue'], reverse=True)

        return {
            'report_title': 'Outstanding LPOs Report',
            'generated_at': datetime.now().isoformat(),
            'total_outstanding': len(outstanding),
            'total_value': sum(item['total_amount'] for item in outstanding),
            'items': outstanding
        }

    def pending_cheques_report(self, days_ahead=30):
        """Report on cheques by status and date

        Args:
            days_ahead: How many days ahead to look for scheduled cheques
        """
        all_cheques = self.db.get_all("Cheques")

        today = datetime.now().date()
        future_date = today + timedelta(days=days_ahead)

        report = {
            'created': [],
            'approved': [],
            'released_upcoming': [],
            'released_past': []
        }

        for cheque in all_cheques:
            status = cheque.get('Status', '')

            if status == 'Cleared':
                continue  # Skip cleared cheques

            cheque_data = {
                'cheque_id': cheque['Cheque ID'],
                'cheque_number': cheque.get('Cheque Number', ''),
                'supplier_name': cheque['Supplier Name'],
                'amount': float(cheque.get('Amount (AED)', 0)),
                'cheque_date': cheque.get('Cheque Date', ''),
                'status': status,
                'created_date': cheque.get('Created Date', ''),
                'bank_name': cheque.get('Bank Name', '')
            }

            if status == 'Created':
                report['created'].append(cheque_data)
            elif status == 'Approved':
                report['approved'].append(cheque_data)
            elif status == 'Released':
                # Check cheque date
                cheque_date_str = cheque.get('Cheque Date', '')
                if cheque_date_str:
                    try:
                        cheque_date = datetime.strptime(cheque_date_str, '%Y-%m-%d').date()
                        if cheque_date <= today:
                            report['released_past'].append(cheque_data)
                        elif cheque_date <= future_date:
                            report['released_upcoming'].append(cheque_data)
                    except:
                        report['released_upcoming'].append(cheque_data)

        # Sort each category by date
        for category in report.values():
            if category:
                category.sort(key=lambda x: x.get('cheque_date', ''))

        # Calculate totals
        total_created = sum(c['amount'] for c in report['created'])
        total_approved = sum(c['amount'] for c in report['approved'])
        total_released_upcoming = sum(c['amount'] for c in report['released_upcoming'])
        total_released_past = sum(c['amount'] for c in report['released_past'])

        return {
            'report_title': f'Pending Cheques Report ({days_ahead} days)',
            'generated_at': datetime.now().isoformat(),
            'summary': {
                'created': {'count': len(report['created']), 'total': total_created},
                'approved': {'count': len(report['approved']), 'total': total_approved},
                'released_upcoming': {'count': len(report['released_upcoming']), 'total': total_released_upcoming},
                'released_past': {'count': len(report['released_past']), 'total': total_released_past}
            },
            'details': report
        }

    def cash_flow_projection(self, days_ahead=90):
        """Project cash outflows based on cheque dates and invoice due dates

        Args:
            days_ahead: How many days ahead to project
        """
        today = datetime.now().date()
        future_date = today + timedelta(days=days_ahead)

        # Get all non-cleared cheques
        all_cheques = self.db.get_all("Cheques")

        # Get all unpaid invoices
        all_invoices = self.db.get_all("Tax Invoices")

        # Build projection timeline
        projections = []

        # Add cheques
        for cheque in all_cheques:
            if cheque.get('Status') != 'Cleared':
                cheque_date_str = cheque.get('Cheque Date', '')
                if cheque_date_str:
                    try:
                        cheque_date = datetime.strptime(cheque_date_str, '%Y-%m-%d').date()
                        if today <= cheque_date <= future_date:
                            projections.append({
                                'date': cheque_date_str,
                                'type': 'Cheque',
                                'reference': cheque['Cheque ID'],
                                'supplier': cheque['Supplier Name'],
                                'amount': float(cheque.get('Amount (AED)', 0)),
                                'status': cheque.get('Status', '')
                            })
                    except:
                        pass

        # Add unpaid invoices
        for invoice in all_invoices:
            if invoice.get('Status') in ['Pending', 'Approved']:
                due_date_str = invoice.get('Due Date', '')
                if due_date_str:
                    try:
                        due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
                        if today <= due_date <= future_date:
                            projections.append({
                                'date': due_date_str,
                                'type': 'Invoice Due',
                                'reference': invoice['Invoice ID'],
                                'supplier': invoice['Supplier Name'],
                                'amount': float(invoice.get('Total Amount (AED)', 0)),
                                'status': invoice.get('Status', '')
                            })
                    except:
                        pass

        # Sort by date
        projections.sort(key=lambda x: x['date'])

        # Calculate running total and weekly summaries
        running_total = 0
        weekly_summary = {}

        for item in projections:
            running_total += item['amount']
            item['running_total'] = running_total

            # Add to weekly summary
            item_date = datetime.strptime(item['date'], '%Y-%m-%d').date()
            week_start = item_date - timedelta(days=item_date.weekday())
            week_key = week_start.strftime('%Y-%m-%d')

            if week_key not in weekly_summary:
                weekly_summary[week_key] = {'total': 0, 'count': 0}

            weekly_summary[week_key]['total'] += item['amount']
            weekly_summary[week_key]['count'] += 1

        return {
            'report_title': f'Cash Flow Projection ({days_ahead} days)',
            'generated_at': datetime.now().isoformat(),
            'period_start': today.strftime('%Y-%m-%d'),
            'period_end': future_date.strftime('%Y-%m-%d'),
            'total_projected_outflow': running_total,
            'transaction_count': len(projections),
            'weekly_summary': [
                {
                    'week_starting': week,
                    'total_amount': data['total'],
                    'transaction_count': data['count']
                }
                for week, data in sorted(weekly_summary.items())
            ],
            'transactions': projections
        }

    def supplier_spending_analysis(self, start_date=None, end_date=None):
        """Analyze spending by supplier

        Args:
            start_date: Start date (YYYY-MM-DD), defaults to 1 year ago
            end_date: End date (YYYY-MM-DD), defaults to today
        """
        if not end_date:
            end_date = datetime.now().date()
        else:
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()

        if not start_date:
            start_date = end_date - timedelta(days=365)
        else:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()

        # Get all LPOs in date range
        all_lpos = self.db.get_all("LPOs")

        supplier_totals = {}

        for lpo in all_lpos:
            lpo_date_str = lpo.get('LPO Date', '')
            if not lpo_date_str:
                continue

            try:
                lpo_date = datetime.strptime(lpo_date_str, '%Y-%m-%d').date()
            except:
                continue

            if start_date <= lpo_date <= end_date:
                supplier_id = lpo.get('Supplier ID', '')
                supplier_name = lpo.get('Supplier Name', 'Unknown')
                amount = float(lpo.get('Total Amount (AED)', 0))

                if supplier_id not in supplier_totals:
                    supplier_totals[supplier_id] = {
                        'supplier_id': supplier_id,
                        'supplier_name': supplier_name,
                        'total_spent': 0,
                        'lpo_count': 0,
                        'average_lpo_value': 0
                    }

                supplier_totals[supplier_id]['total_spent'] += amount
                supplier_totals[supplier_id]['lpo_count'] += 1

        # Calculate averages and sort
        supplier_list = list(supplier_totals.values())
        for supplier in supplier_list:
            supplier['average_lpo_value'] = supplier['total_spent'] / supplier['lpo_count']

        supplier_list.sort(key=lambda x: x['total_spent'], reverse=True)

        total_spend = sum(v['total_spent'] for v in supplier_list)

        # Add percentage
        for supplier in supplier_list:
            supplier['percentage_of_total'] = (supplier['total_spent'] / total_spend * 100) if total_spend > 0 else 0

        return {
            'report_title': 'Vendor Spending Analysis',
            'generated_at': datetime.now().isoformat(),
            'period_start': start_date.strftime('%Y-%m-%d'),
            'period_end': end_date.strftime('%Y-%m-%d'),
            'total_suppliers': len(supplier_list),
            'total_spend': total_spend,
            'suppliers': supplier_list
        }

    def export_report_to_json(self, report, filename):
        """Export report to JSON file"""
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2)
        return filename


# CLI for testing
if __name__ == "__main__":
    import sys

    generator = ReportGenerator()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python reporting.py outstanding_lpos")
        print("  python reporting.py pending_cheques [days_ahead]")
        print("  python reporting.py cash_flow [days_ahead]")
        print("  python reporting.py supplier_spending [start_date] [end_date]")
        sys.exit(1)

    report_type = sys.argv[1]

    if report_type == "outstanding_lpos":
        report = generator.outstanding_lpos_report()
        print(json.dumps(report, indent=2))

    elif report_type == "pending_cheques":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        report = generator.pending_cheques_report(days)
        print(json.dumps(report, indent=2))

    elif report_type == "cash_flow":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 90
        report = generator.cash_flow_projection(days)
        print(json.dumps(report, indent=2))

    elif report_type == "supplier_spending":
        start = sys.argv[2] if len(sys.argv) > 2 else None
        end = sys.argv[3] if len(sys.argv) > 3 else None
        report = generator.supplier_spending_analysis(start, end)
        print(json.dumps(report, indent=2))

    else:
        print(f"Unknown report type: {report_type}")
