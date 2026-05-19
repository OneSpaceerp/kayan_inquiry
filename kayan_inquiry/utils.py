"""
kayan_inquiry.utils — Shared Utilities & Document Event Handlers
================================================================
Hooks registered in hooks.py ``doc_events``:

    Quotation.on_submit   →  on_quotation_submit
    Sales Order.on_submit →  on_sales_order_submit

Also provides:
    log_activity()  — shared activity logger for the Inquiry Activity Log
                      child table on Email Inquiry Ticket.
"""

import frappe
from frappe import _
from frappe.utils import now_datetime


# ---------------------------------------------------------------------------
# Document Event: Quotation on_submit
# ---------------------------------------------------------------------------

def on_quotation_submit(doc, method):
    """
    Called when any Quotation is submitted.
    If the Quotation has a ``custom_inquiry_ticket`` field pointing to
    an Email Inquiry Ticket, update the ticket's linked_quotation and
    log the activity.

    The ``custom_inquiry_ticket`` field is a Custom Field added to the
    standard Quotation DocType via Customize Form (see DocType JSON links).
    """
    ticket_name = _get_custom_inquiry_ticket(doc)
    if not ticket_name:
        return

    # Verify the ticket actually exists
    if not frappe.db.exists("Email Inquiry Ticket", ticket_name):
        frappe.log_error(
            title="Quotation Submit — Ticket Not Found",
            message=f"Quotation {doc.name} references ticket {ticket_name} which does not exist.",
        )
        return

    # Update the ticket's linked_quotation
    # Use set_value to avoid triggering the full doc-save cycle
    current_quotation = frappe.db.get_value(
        "Email Inquiry Ticket", ticket_name, "linked_quotation"
    )
    if not current_quotation:
        frappe.db.set_value(
            "Email Inquiry Ticket", ticket_name,
            "linked_quotation", doc.name,
        )

    # Log activity on the ticket
    log_activity(
        ticket_name=ticket_name,
        action=f"Quotation submitted: {doc.name}",
        new_value=doc.name,
        notes=f"Quotation {doc.name} submitted for {doc.party_name or 'N/A'}. "
              f"Grand Total: {doc.grand_total or 0}",
    )


# ---------------------------------------------------------------------------
# Document Event: Sales Order on_submit
# ---------------------------------------------------------------------------

def on_sales_order_submit(doc, method):
    """
    Called when any Sales Order is submitted.
    Walks back through SO items to find a source Quotation, then checks
    if that Quotation is linked to an Email Inquiry Ticket.

    If found:
    - Sets linked_sales_order on the ticket
    - Transitions workflow_state to 'Won: Sales Order Generated'
    - Logs the activity
    """
    # ------------------------------------------------------------------
    # Step 1: Find the source Quotation from SO items
    # ------------------------------------------------------------------
    quotation_name = _find_source_quotation(doc)
    if not quotation_name:
        return

    # ------------------------------------------------------------------
    # Step 2: Look up the Quotation for custom_inquiry_ticket
    # ------------------------------------------------------------------
    ticket_name = _get_ticket_from_quotation(quotation_name)
    if not ticket_name:
        return

    # Verify the ticket exists
    if not frappe.db.exists("Email Inquiry Ticket", ticket_name):
        frappe.log_error(
            title="Sales Order Submit — Ticket Not Found",
            message=f"Sales Order {doc.name} traces back to ticket {ticket_name} "
                    f"via Quotation {quotation_name}, but the ticket does not exist.",
        )
        return

    # ------------------------------------------------------------------
    # Step 3: Update the ticket
    # ------------------------------------------------------------------
    frappe.db.set_value(
        "Email Inquiry Ticket", ticket_name,
        {
            "linked_sales_order": doc.name,
            "workflow_state": "Won: Sales Order Generated",
        },
    )

    # ------------------------------------------------------------------
    # Step 4: Log activity
    # ------------------------------------------------------------------
    log_activity(
        ticket_name=ticket_name,
        action="Sales Order submitted — Ticket Won",
        old_value=frappe.db.get_value("Email Inquiry Ticket", ticket_name, "workflow_state") or "",
        new_value="Won: Sales Order Generated",
        notes=f"Sales Order {doc.name} created from Quotation {quotation_name}. "
              f"Grand Total: {doc.grand_total or 0}",
    )


# ---------------------------------------------------------------------------
# Shared Activity Logger
# ---------------------------------------------------------------------------

def log_activity(ticket_name, action, old_value="", new_value="", notes=""):
    """
    Append a row to the ``activity_log`` child table of an
    Email Inquiry Ticket. Uses ``ignore_permissions=True`` for
    system-level operations (SLA scheduler, doc events).

    Args:
        ticket_name (str): Name of the Email Inquiry Ticket.
        action (str): Short description of the action (e.g. "Quotation submitted").
        old_value (str): Previous value (if applicable).
        new_value (str): New value (if applicable).
        notes (str): Additional context / details.
    """
    if not ticket_name:
        return

    try:
        ticket = frappe.get_doc("Email Inquiry Ticket", ticket_name)
        ticket.append("activity_log", {
            "timestamp": now_datetime(),
            "action": action,
            "performed_by": frappe.session.user,
            "old_value": old_value or "",
            "new_value": new_value or "",
            "notes": notes or "",
        })
        ticket.save(ignore_permissions=True)
    except Exception:
        # Activity logging should never block the parent operation
        frappe.log_error(
            title=f"Activity Log Failed — {ticket_name}",
            message=frappe.get_traceback(),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_custom_inquiry_ticket(doc):
    """
    Safely retrieve the custom_inquiry_ticket field from a Quotation
    or any document. Returns None if the field doesn't exist or is empty.
    """
    # Try attribute access (works for custom fields added via Customize Form)
    ticket = getattr(doc, "custom_inquiry_ticket", None)
    if ticket:
        return ticket

    # Fallback: try doc.get() for meta-driven fields
    ticket = doc.get("custom_inquiry_ticket")
    if ticket:
        return ticket

    return None


def _find_source_quotation(sales_order_doc):
    """
    Walk the Sales Order items to find a linked Quotation.
    ERPNext stores the source Quotation reference in the SO item's
    ``prevdoc_docname`` field when the SO is created via
    "Make Sales Order" from a Quotation.
    """
    for item in sales_order_doc.get("items", []):
        # Standard ERPNext v16 field for quotation reference in SO items
        prevdoc_doctype = getattr(item, "prevdoc_doctype", None) or item.get("prevdoc_doctype")
        prevdoc_docname = getattr(item, "prevdoc_docname", None) or item.get("prevdoc_docname")

        if prevdoc_doctype == "Quotation" and prevdoc_docname:
            return prevdoc_docname

    return None


def _get_ticket_from_quotation(quotation_name):
    """
    Look up the custom_inquiry_ticket field on a Quotation.
    Returns the ticket name or None.
    """
    if not quotation_name:
        return None

    try:
        ticket_name = frappe.db.get_value(
            "Quotation", quotation_name, "custom_inquiry_ticket"
        )
        return ticket_name or None
    except Exception:
        # Field may not exist yet if custom field hasn't been created
        frappe.log_error(
            title="Quotation Ticket Lookup Failed",
            message=f"Could not read custom_inquiry_ticket from Quotation {quotation_name}. "
                    f"Ensure the Custom Field exists.\n{frappe.get_traceback()}",
        )
        return None
