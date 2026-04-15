"""
Approval workflow management for LPOs and Cheques
Handles approval requests, status updates, and notifications
"""

import os
from datetime import datetime
from sheets_operations import SheetsDB
from email_notifications import EmailNotifier
from dotenv import load_dotenv

load_dotenv()


class ApprovalWorkflow:
    """Manage approval workflows"""

    def __init__(self):
        self.db = SheetsDB()
        self.notifier = EmailNotifier()
        self.approver_email = os.getenv('APPROVER_EMAIL',
                                       os.getenv('NOTIFICATION_EMAIL'))
        self.finance_email = os.getenv('FINANCE_EMAIL',
                                      os.getenv('NOTIFICATION_EMAIL'))

    def request_lpo_approval(self, lpo_id, requester):
        """Request approval for an LPO"""
        # Get LPO data
        lpo = self.db.get_by_id("LPOs", "LPO ID", lpo_id)
        if not lpo:
            raise ValueError(f"LPO {lpo_id} not found")

        # Check current status
        if lpo['Status'] != 'Draft' and lpo['Status'] != 'Pending Approval':
            raise ValueError(f"LPO is in {lpo['Status']} status, cannot request approval")

        # Update status to Pending Approval
        self.db.update("LPOs", "LPO ID", lpo_id,
                      {"Status": "Pending Approval"},
                      requester)

        # Send notification
        lpo_updated = self.db.get_by_id("LPOs", "LPO ID", lpo_id)
        self.notifier.notify_lpo_pending_approval(lpo_updated, self.approver_email)

        return {
            "success": True,
            "message": f"Approval requested for {lpo_id}",
            "notification_sent": True
        }

    def approve_lpo(self, lpo_id, approver, comments=""):
        """Approve an LPO"""
        # Get LPO data
        lpo = self.db.get_by_id("LPOs", "LPO ID", lpo_id)
        if not lpo:
            raise ValueError(f"LPO {lpo_id} not found")

        # Check current status
        if lpo['Status'] != 'Pending Approval':
            raise ValueError(f"LPO is not pending approval (current status: {lpo['Status']})")

        # Update to approved
        self.db.update("LPOs", "LPO ID", lpo_id,
                      {
                          "Status": "Approved",
                          "Approved By": approver,
                          "Approved Date": datetime.now().strftime("%Y-%m-%d"),
                          "Notes": lpo.get('Notes', '') + f"\n[Approval] {comments}" if comments else lpo.get('Notes', '')
                      },
                      approver)

        # Notify creator
        creator_email = self._get_user_email(lpo['Created By'])
        if creator_email:
            lpo_updated = self.db.get_by_id("LPOs", "LPO ID", lpo_id)
            self.notifier.notify_lpo_approved(lpo_updated, creator_email)

        return {
            "success": True,
            "message": f"LPO {lpo_id} approved",
            "approved_by": approver,
            "approved_date": datetime.now().strftime("%Y-%m-%d")
        }

    def reject_lpo(self, lpo_id, approver, reason):
        """Reject an LPO"""
        # Get LPO data
        lpo = self.db.get_by_id("LPOs", "LPO ID", lpo_id)
        if not lpo:
            raise ValueError(f"LPO {lpo_id} not found")

        # Check current status
        if lpo['Status'] != 'Pending Approval':
            raise ValueError(f"LPO is not pending approval")

        # Update to rejected
        self.db.update("LPOs", "LPO ID", lpo_id,
                      {
                          "Status": "Rejected",
                          "Approved By": approver,
                          "Approved Date": datetime.now().strftime("%Y-%m-%d"),
                          "Notes": lpo.get('Notes', '') + f"\n[Rejection] {reason}"
                      },
                      approver)

        # Notify creator (could create a reject notification method)
        return {
            "success": True,
            "message": f"LPO {lpo_id} rejected",
            "rejected_by": approver,
            "reason": reason
        }

    def request_cheque_approval(self, cheque_id, requester):
        """Request approval for a cheque"""
        # Get cheque data
        cheque = self.db.get_by_id("Cheques", "Cheque ID", cheque_id)
        if not cheque:
            raise ValueError(f"Cheque {cheque_id} not found")

        # Status should be Created
        if cheque['Status'] != 'Created':
            raise ValueError(f"Cheque cannot be approved (current status: {cheque['Status']})")

        # Send notification
        self.notifier.notify_cheque_needs_approval(cheque, self.approver_email)

        return {
            "success": True,
            "message": f"Approval requested for cheque {cheque_id}",
            "notification_sent": True
        }

    def approve_cheque(self, cheque_id, approver):
        """Approve a cheque"""
        # Get cheque data
        cheque = self.db.get_by_id("Cheques", "Cheque ID", cheque_id)
        if not cheque:
            raise ValueError(f"Cheque {cheque_id} not found")

        if cheque['Status'] != 'Created':
            raise ValueError(f"Cheque cannot be approved from {cheque['Status']} status")

        # Update to approved
        self.db.update("Cheques", "Cheque ID", cheque_id,
                      {
                          "Status": "Approved",
                          "Approved By": approver,
                          "Approved Date": datetime.now().strftime("%Y-%m-%d")
                      },
                      approver)

        return {
            "success": True,
            "message": f"Cheque {cheque_id} approved",
            "approved_by": approver
        }

    def release_cheque(self, cheque_id, releaser):
        """Mark cheque as released to supplier"""
        # Get cheque data
        cheque = self.db.get_by_id("Cheques", "Cheque ID", cheque_id)
        if not cheque:
            raise ValueError(f"Cheque {cheque_id} not found")

        if cheque['Status'] != 'Approved':
            raise ValueError(f"Cheque must be approved before release (current: {cheque['Status']})")

        # Update to released
        self.db.update("Cheques", "Cheque ID", cheque_id,
                      {
                          "Status": "Released",
                          "Released By": releaser,
                          "Released Date": datetime.now().strftime("%Y-%m-%d")
                      },
                      releaser)

        # Update invoice status to paid
        if cheque.get('Invoice ID'):
            self.db.update("Tax Invoices", "Invoice ID", cheque['Invoice ID'],
                          {"Status": "Paid"},
                          releaser)

        # Notify finance team
        cheque_updated = self.db.get_by_id("Cheques", "Cheque ID", cheque_id)
        self.notifier.notify_cheque_released(cheque_updated, self.finance_email)

        return {
            "success": True,
            "message": f"Cheque {cheque_id} released",
            "released_by": releaser,
            "released_date": datetime.now().strftime("%Y-%m-%d")
        }

    def mark_cheque_cleared(self, cheque_id, user):
        """Mark cheque as cleared by bank"""
        # Get cheque data
        cheque = self.db.get_by_id("Cheques", "Cheque ID", cheque_id)
        if not cheque:
            raise ValueError(f"Cheque {cheque_id} not found")

        if cheque['Status'] != 'Released':
            raise ValueError(f"Cheque must be released before marking cleared")

        # Update to cleared
        self.db.update("Cheques", "Cheque ID", cheque_id,
                      {
                          "Status": "Cleared",
                          "Cleared Date": datetime.now().strftime("%Y-%m-%d")
                      },
                      user)

        return {
            "success": True,
            "message": f"Cheque {cheque_id} marked as cleared"
        }

    def get_pending_approvals(self, approval_type="all"):
        """Get all items pending approval

        Args:
            approval_type: "lpo", "cheque", or "all"
        """
        results = {
            "lpos": [],
            "cheques": []
        }

        if approval_type in ["lpo", "all"]:
            results["lpos"] = self.db.filter("LPOs", {"Status": "Pending Approval"})

        if approval_type in ["cheque", "all"]:
            results["cheques"] = self.db.filter("Cheques", {"Status": "Created"})

        return results

    def _get_user_email(self, username):
        """Get email for a user (placeholder - implement based on user management)"""
        # For now, return a default email
        # In production, query a Users table or directory
        return os.getenv('NOTIFICATION_EMAIL')


# CLI for testing
if __name__ == "__main__":
    import sys

    workflow = ApprovalWorkflow()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python approval_workflow.py pending [lpo|cheque|all]")
        print("  python approval_workflow.py approve_lpo <lpo_id> <approver>")
        print("  python approval_workflow.py approve_cheque <cheque_id> <approver>")
        print("  python approval_workflow.py release_cheque <cheque_id> <releaser>")
        sys.exit(1)

    command = sys.argv[1]

    if command == "pending":
        approval_type = sys.argv[2] if len(sys.argv) > 2 else "all"
        results = workflow.get_pending_approvals(approval_type)
        print(f"\nPending Approvals:")
        print(f"  LPOs: {len(results['lpos'])}")
        print(f"  Cheques: {len(results['cheques'])}")

        if results['lpos']:
            print("\nLPOs pending approval:")
            for lpo in results['lpos']:
                print(f"  - {lpo['LPO ID']}: {lpo['Supplier Name']} - {lpo['Total Amount (AED)']} AED")

        if results['cheques']:
            print("\nCheques pending approval:")
            for cheque in results['cheques']:
                print(f"  - {cheque['Cheque ID']}: {cheque['Supplier Name']} - {cheque['Amount (AED)']} AED")

    elif command == "approve_lpo":
        lpo_id = sys.argv[2]
        approver = sys.argv[3]
        result = workflow.approve_lpo(lpo_id, approver)
        print(result)

    elif command == "approve_cheque":
        cheque_id = sys.argv[2]
        approver = sys.argv[3]
        result = workflow.approve_cheque(cheque_id, approver)
        print(result)

    elif command == "release_cheque":
        cheque_id = sys.argv[2]
        releaser = sys.argv[3]
        result = workflow.release_cheque(cheque_id, releaser)
        print(result)

    else:
        print(f"Unknown command: {command}")
