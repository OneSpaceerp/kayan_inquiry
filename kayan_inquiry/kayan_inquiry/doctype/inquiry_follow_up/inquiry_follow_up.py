import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class InquiryFollowUp(Document):
    def before_insert(self):
        if not self.created_by:
            self.created_by = frappe.session.user

    def on_update(self):
        if self.completed and not self.completed_on:
            self.completed_on = now_datetime()
            self.db_set('completed_on', self.completed_on)
