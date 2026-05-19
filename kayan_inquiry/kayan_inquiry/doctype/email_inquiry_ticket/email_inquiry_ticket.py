"""
Email Inquiry Ticket — Server-side controller
Handles: auto-assignment, customer resolution, activity logging,
         quotation creation, SLA initialization, and permissions.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime, add_to_date, get_datetime
import json


# ---------------------------------------------------------------------------
# Lifecycle hooks (called from hooks.py doc_events)
# ---------------------------------------------------------------------------

def before_insert(doc, method=None):
    """Run before the ticket is saved for the first time."""
    _check_duplicate_message_id(doc)
    _resolve_customer_or_lead(doc)
    _auto_assign_sales_engineer(doc)
    _set_sla_due(doc)


def after_insert(doc, method=None):
    """Run after the ticket is first saved."""
    _append_activity(doc, "Inquiry created", details=f"Source: {doc.source}, Confidence: {doc.ai_confidence_score}")
    _append_assignment_log(doc, doc.assigned_sales_engineer, "Sales Engineer", "System")
    _send_new_inquiry_notification(doc)


def on_update(doc, method=None):
    """Run on every save after insert."""
    _track_workflow_state_change(doc)
    _sync_quotation_link(doc)


# ---------------------------------------------------------------------------
# Permission hook
# ---------------------------------------------------------------------------

def has_permission(doc, ptype, user):
    """
    Sales Engineers can only read/write their own tickets.
    Application Engineers can access tickets where they are assigned.
    Managers and Admins have unrestricted access.
    """
    if user == "Administrator":
        return True

    roles = frappe.get_roles(user)

    if "Inquiry Admin" in roles or "Inquiry Sales Manager" in roles:
        return True

    if "Inquiry Sales Engineer" in roles:
        return doc.assigned_sales_engineer == user

    if "Inquiry Application Engineer" in roles:
        return doc.application_engineer == user

    return False


# ---------------------------------------------------------------------------
# Auto-assignment: map 'To' address to Sales Engineer
# ---------------------------------------------------------------------------

def _auto_assign_sales_engineer(doc):
    """
    Look up Sales Engineer Mapping to find the SE for doc.email_to.
    Falls back to domain match, then round-robin, then first active manager.
    """
    if doc.assigned_sales_engineer:
        return  # Already set by caller (API)

    # 1. Exact email match
    mapping = frappe.db.get_value(
        "Sales Engineer Mapping",
        {"email_address": doc.email_to, "is_active": 1},
        ["sales_engineer", "fallback_rule", "fallback_user"],
        as_dict=True,
    )

    if mapping:
        doc.assigned_sales_engineer = mapping.sales_engineer
        return

    # 2. Domain-level match (e.g., @kayan.com)
    if doc.email_to and "@" in doc.email_to:
        domain = "@" + doc.email_to.split("@")[1]
        domain_mapping = frappe.db.get_value(
            "Sales Engineer Mapping",
            {"email_address": ("like", f"%{domain}%"), "is_active": 1},
            "sales_engineer",
        )
        if domain_mapping:
            doc.assigned_sales_engineer = domain_mapping
            return

    # 3. Round-robin fallback: pick the SE with the fewest active tickets
    doc.assigned_sales_engineer = _get_round_robin_se()


def _get_round_robin_se():
    """Return the Sales Engineer with the fewest open tickets."""
    ses = frappe.db.sql(
        """
        SELECT u.name, COUNT(t.name) as ticket_count
        FROM `tabUser` u
        INNER JOIN `tabHas Role` hr ON hr.parent = u.name AND hr.role = 'Inquiry Sales Engineer'
        LEFT JOIN `tabEmail Inquiry Ticket` t
            ON t.assigned_sales_engineer = u.name
            AND t.workflow_state NOT IN ('Won: Sales Order Generated', 'Lost / Closed')
        WHERE u.enabled = 1
        GROUP BY u.name
        ORDER BY ticket_count ASC
        LIMIT 1
        """,
        as_dict=True,
    )
    if ses:
        return ses[0].name

    # Final fallback: assign to Administrator
    return "Administrator"


# ---------------------------------------------------------------------------
# Customer / Lead resolution
# ---------------------------------------------------------------------------

def _resolve_customer_or_lead(doc):
    """
    Try to find an existing Customer or Lead from the sender's email.
    If none found, create a new Lead.
    """
    if doc.customer or doc.lead:
        return  # Already resolved by API caller

    sender_email = doc.email_from

    # Step 1: Check Contact
    contact = frappe.db.get_value("Contact", {"email_id": sender_email}, "name")
    if contact:
        doc.contact = contact
        # Check if contact has a linked Customer
        customer = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": "Customer", "parenttype": "Contact", "parent": contact},
            "link_name",
        )
        if customer:
            doc.customer = customer
            doc.customer_type = "Existing Customer"
            return

        # Check if contact has a linked Lead
        lead = frappe.db.get_value(
            "Dynamic Link",
            {"link_doctype": "Lead", "parenttype": "Contact", "parent": contact},
            "link_name",
        )
        if lead:
            doc.lead = lead
            doc.customer_type = "New Lead"
            return

    # Step 2: Check Lead directly by email
    lead = frappe.db.get_value("Lead", {"email_id": sender_email}, "name")
    if lead:
        doc.lead = lead
        doc.customer_type = "New Lead"
        return

    # Step 3: Create a new Lead
    new_lead = frappe.new_doc("Lead")
    new_lead.lead_name = doc.sender_name or sender_email.split("@")[0]
    new_lead.email_id = sender_email
    new_lead.company_name = doc.company_name or ""
    new_lead.phone = doc.contact_phone or ""
    new_lead.source = "Email Inquiry"
    new_lead.status = "Open"
    new_lead.insert(ignore_permissions=True)
    frappe.db.commit()

    doc.lead = new_lead.name
    doc.customer_type = "New Lead"


# ---------------------------------------------------------------------------
# SLA initialization
# ---------------------------------------------------------------------------

SLA_HOURS_BY_PRIORITY = {
    "Critical": 1,
    "High": 2,
    "Medium": 4,
    "Low": 8,
}


def _set_sla_due(doc):
    """Set the SLA due datetime based on priority at creation time."""
    hours = SLA_HOURS_BY_PRIORITY.get(doc.priority, 4)
    doc.sla_due_datetime = add_to_date(now_datetime(), hours=hours)
    doc.sla_status = "On Track"


# ---------------------------------------------------------------------------
# Activity & audit trail
# ---------------------------------------------------------------------------

def _append_activity(doc, action, details=None, old_value=None, new_value=None):
    """Add a row to the immutable activity log."""
    doc.append("activity_log", {
        "timestamp": now_datetime(),
        "action": action,
        "performed_by": frappe.session.user,
        "old_value": old_value or "",
        "new_value": new_value or "",
        "notes": details or "",
    })


def _append_assignment_log(doc, assigned_to, role, assigned_by_name=None):
    """Record an assignment event."""
    doc.append("assignment_log", {
        "assigned_to": assigned_to,
        "role": role,
        "assigned_by": assigned_by_name or frappe.session.user,
        "assigned_at": now_datetime(),
    })


def _track_workflow_state_change(doc):
    """Detect workflow state changes and log them."""
    if doc.is_new():
        return
    old_state = frappe.db.get_value("Email Inquiry Ticket", doc.name, "workflow_state")
    if old_state and old_state != doc.workflow_state:
        _append_activity(
            doc,
            action=f"Status changed: {old_state} → {doc.workflow_state}",
            old_value=old_state,
            new_value=doc.workflow_state,
        )
        # When AE is added for first time, log assignment
        old_ae = frappe.db.get_value("Email Inquiry Ticket", doc.name, "application_engineer")
        if doc.application_engineer and not old_ae:
            _append_assignment_log(doc, doc.application_engineer, "Application Engineer")


# ---------------------------------------------------------------------------
# Quotation auto-link sync
# ---------------------------------------------------------------------------

def _sync_quotation_link(doc):
    """If a Quotation linked back to this ticket was submitted, update SO link."""
    pass  # Handled by utils.on_quotation_submit and utils.on_sales_order_submit


# ---------------------------------------------------------------------------
# Deduplication check
# ---------------------------------------------------------------------------

def _check_duplicate_message_id(doc):
    if not doc.message_id:
        return
    existing = frappe.db.exists("Email Inquiry Ticket", {"message_id": doc.message_id})
    if existing:
        frappe.throw(
            _("An inquiry with Message ID {0} already exists: {1}").format(
                doc.message_id, existing
            ),
            frappe.DuplicateEntryError,
        )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _send_new_inquiry_notification(doc):
    """Email + in-app alert to the assigned Sales Engineer."""
    if not doc.assigned_sales_engineer:
        return

    se_email = frappe.db.get_value("User", doc.assigned_sales_engineer, "email")
    if not se_email:
        return

    subject = f"[New Inquiry] {doc.email_subject} — {doc.name}"
    message = f"""
<p>Dear {frappe.db.get_value('User', doc.assigned_sales_engineer, 'full_name')},</p>
<p>A new pricing inquiry has been assigned to you:</p>
<ul>
    <li><b>Ticket:</b> {doc.name}</li>
    <li><b>From:</b> {doc.sender_name} &lt;{doc.email_from}&gt;</li>
    <li><b>Company:</b> {doc.company_name or 'Unknown'}</li>
    <li><b>Subject:</b> {doc.email_subject}</li>
    <li><b>AI Confidence:</b> {round((doc.ai_confidence_score or 0) * 100)}%</li>
    <li><b>SLA Due:</b> {doc.sla_due_datetime}</li>
</ul>
<p>AI Draft Reply suggestion is available in the ticket for your review.</p>
<p><a href="{frappe.utils.get_url()}/app/email-inquiry-ticket/{doc.name}">Open Ticket →</a></p>
"""

    frappe.sendmail(
        recipients=[se_email],
        subject=subject,
        message=message,
        reference_doctype="Email Inquiry Ticket",
        reference_name=doc.name,
        now=True,
    )

    # In-app notification
    frappe.publish_realtime(
        "eval:frappe.msgprint",
        {"message": f"New inquiry assigned: {doc.name}", "title": "New Inquiry"},
        user=doc.assigned_sales_engineer,
    )


# ---------------------------------------------------------------------------
# Whitelisted server-side actions (called from client JS / desk buttons)
# ---------------------------------------------------------------------------

@frappe.whitelist()
def create_quotation_from_ticket(ticket_name):
    """
    Convert an Email Inquiry Ticket into an ERPNext Quotation.
    Pre-fills customer/lead data and product lines from the ticket.
    """
    ticket = frappe.get_doc("Email Inquiry Ticket", ticket_name)

    # Permission check
    if not has_permission(ticket, "write", frappe.session.user):
        frappe.throw(_("You do not have permission to convert this ticket."))

    if ticket.linked_quotation:
        frappe.throw(_("A quotation ({0}) already exists for this ticket.").format(ticket.linked_quotation))

    quotation = frappe.new_doc("Quotation")

    # Set party
    if ticket.customer:
        quotation.quotation_to = "Customer"
        quotation.party_name = ticket.customer
    elif ticket.lead:
        quotation.quotation_to = "Lead"
        quotation.party_name = ticket.lead
    else:
        frappe.throw(_("Please link a Customer or Lead to the ticket before creating a quotation."))

    # Custom link back to inquiry ticket
    if hasattr(quotation, "custom_inquiry_ticket"):
        quotation.custom_inquiry_ticket = ticket_name

    # Pre-fill items
    for line in ticket.product_lines:
        item_row = {
            "item_name": line.item_description,
            "description": line.item_description,
            "qty": line.quantity or 1,
            "uom": line.uom or "Nos",
        }
        if line.item_code:
            item_row["item_code"] = line.item_code
        quotation.append("items", item_row)

    quotation.flags.ignore_permissions = True
    quotation.insert()
    frappe.db.commit()

    # Link quotation back to ticket
    frappe.db.set_value("Email Inquiry Ticket", ticket_name, {
        "linked_quotation": quotation.name,
        "workflow_state": "Quotation Preparation",
    })

    # Log activity
    ticket.reload()
    _append_activity(
        ticket,
        action=f"Quotation created: {quotation.name}",
        new_value=quotation.name,
    )
    ticket.save(ignore_permissions=True)

    frappe.msgprint(
        _("Quotation {0} created successfully.").format(quotation.name),
        alert=True,
    )
    return quotation.name


@frappe.whitelist()
def request_ae_review(ticket_name, ae_user):
    """
    Assign an Application Engineer and move the ticket to AE Review.
    """
    ticket = frappe.get_doc("Email Inquiry Ticket", ticket_name)

    if not has_permission(ticket, "write", frappe.session.user):
        frappe.throw(_("You do not have permission to update this ticket."))

    ticket.application_engineer = ae_user
    ticket.workflow_state = "Application Engineer Review"

    _append_activity(
        ticket,
        action=f"AE Review requested — assigned to {ae_user}",
        new_value=ae_user,
    )
    _append_assignment_log(ticket, ae_user, "Application Engineer")

    ticket.save(ignore_permissions=True)

    # Notify AE
    ae_email = frappe.db.get_value("User", ae_user, "email")
    ae_name = frappe.db.get_value("User", ae_user, "full_name")
    if ae_email:
        frappe.sendmail(
            recipients=[ae_email],
            subject=f"[Technical Review Required] {ticket.name} — {ticket.email_subject}",
            message=f"""
<p>Dear {ae_name},</p>
<p>Technical review has been requested for inquiry <b>{ticket.name}</b>.</p>
<ul>
    <li><b>Customer:</b> {ticket.company_name or ticket.sender_name}</li>
    <li><b>Subject:</b> {ticket.email_subject}</li>
    <li><b>Requested by:</b> {frappe.db.get_value('User', frappe.session.user, 'full_name')}</li>
</ul>
<p><a href="{frappe.utils.get_url()}/app/email-inquiry-ticket/{ticket.name}">Open Ticket →</a></p>
""",
            reference_doctype="Email Inquiry Ticket",
            reference_name=ticket.name,
            now=True,
        )

    return {"status": "ok", "ae": ae_user}


@frappe.whitelist()
def mark_as_lost(ticket_name, lost_reason, lost_notes=""):
    """Mark ticket as Lost / Closed."""
    ticket = frappe.get_doc("Email Inquiry Ticket", ticket_name)

    if not has_permission(ticket, "write", frappe.session.user):
        frappe.throw(_("You do not have permission to update this ticket."))

    ticket.lost_reason = lost_reason
    ticket.lost_reason_notes = lost_notes
    ticket.workflow_state = "Lost / Closed"

    _append_activity(
        ticket,
        action=f"Ticket marked as Lost — Reason: {lost_reason}",
        new_value="Lost / Closed",
    )
    ticket.save(ignore_permissions=True)
    return {"status": "ok"}


@frappe.whitelist()
def mark_as_won(ticket_name):
    """Mark ticket as Won when the Sales Order is created."""
    ticket = frappe.get_doc("Email Inquiry Ticket", ticket_name)
    ticket.workflow_state = "Won: Sales Order Generated"
    _append_activity(ticket, action="Ticket marked as Won — Sales Order Generated")
    ticket.save(ignore_permissions=True)
    return {"status": "ok"}
