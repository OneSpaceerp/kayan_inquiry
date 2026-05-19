"""
kayan_inquiry.api — REST API for n8n Email Inquiry Integration
==============================================================
Exposes whitelisted endpoints that n8n calls after the AI pipeline
classifies and extracts data from incoming pricing-inquiry emails.

Endpoints:
    - create_inquiry(**kwargs)          — Creates an Email Inquiry Ticket
    - check_duplicate_message_id(id)    — Pre-check for duplicate emails
"""

import json
import base64
import frappe
from frappe import _
from frappe.utils import now_datetime, get_datetime, cstr


# ---------------------------------------------------------------------------
# Urgency → Priority mapping
# ---------------------------------------------------------------------------
URGENCY_TO_PRIORITY = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "critical": "Critical",
}

# Confidence band for manual review flag
MANUAL_REVIEW_LOW = 0.40
MANUAL_REVIEW_HIGH = 0.74

# Fields that must be present in the incoming payload
REQUIRED_FIELDS = [
    "message_id",
    "received_date",
    "email_from",
    "email_to",
    "email_subject",
    "email_body",
    "ai_confidence_score",
]


# ---------------------------------------------------------------------------
# Primary endpoint: create an inquiry ticket from n8n
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=False)
def create_inquiry(**kwargs):
    """
    Create an Email Inquiry Ticket from an AI-processed email payload.

    Called by n8n after the classification + extraction pipeline.
    The DocType's ``before_insert`` hook handles:
        - Duplicate message_id check
        - Customer / Lead resolution
        - Sales Engineer auto-assignment
        - SLA due-datetime initialization

    Returns:
        dict with status, inquiry_name, assigned_to, customer_type,
        lead_created, and sla_due.
    """
    # Accept either raw kwargs or a nested 'data' dict (for flexibility)
    data = kwargs
    if isinstance(data.get("data"), dict):
        data = data["data"]

    # ------------------------------------------------------------------
    # 1. Validate required fields
    # ------------------------------------------------------------------
    missing = [f for f in REQUIRED_FIELDS if not data.get(f)]
    if missing:
        frappe.local.response["http_status_code"] = 400
        return {
            "status": "error",
            "message": _("Missing required fields: {0}").format(", ".join(missing)),
        }

    # ------------------------------------------------------------------
    # 2. Check for duplicate message_id before doing any work
    # ------------------------------------------------------------------
    message_id = cstr(data["message_id"]).strip()
    existing = frappe.db.get_value(
        "Email Inquiry Ticket", {"message_id": message_id}, "name"
    )
    if existing:
        frappe.local.response["http_status_code"] = 409
        return {
            "status": "error",
            "message": _("Duplicate: an inquiry with this Message ID already exists."),
            "existing_ticket": existing,
        }

    # ------------------------------------------------------------------
    # 3. Resolve priority from urgency_level
    # ------------------------------------------------------------------
    urgency = cstr(data.get("urgency_level", "")).strip().lower()
    priority = URGENCY_TO_PRIORITY.get(urgency, "Medium")

    # ------------------------------------------------------------------
    # 4. Determine manual review flag
    # ------------------------------------------------------------------
    try:
        confidence = float(data["ai_confidence_score"])
    except (ValueError, TypeError):
        confidence = 0.0

    manual_review = 1 if MANUAL_REVIEW_LOW <= confidence <= MANUAL_REVIEW_HIGH else 0

    # ------------------------------------------------------------------
    # 5. Parse received_date
    # ------------------------------------------------------------------
    try:
        received_date = get_datetime(data["received_date"])
    except Exception:
        received_date = now_datetime()

    # ------------------------------------------------------------------
    # 6. Build the Email Inquiry Ticket document
    # ------------------------------------------------------------------
    ticket = frappe.new_doc("Email Inquiry Ticket")
    ticket.update({
        "message_id": message_id,
        "source": "Email",
        "received_date": received_date,
        "email_from": cstr(data["email_from"]).strip(),
        "sender_name": cstr(data.get("sender_name", "")).strip(),
        "email_to": cstr(data["email_to"]).strip(),
        "email_cc": cstr(data.get("email_cc", "")).strip(),
        "email_subject": cstr(data["email_subject"]).strip(),
        "email_body": cstr(data["email_body"]),
        "priority": priority,
        "ai_confidence_score": confidence,
        "ai_classification_reason": cstr(data.get("ai_classification_reason", "")),
        "ai_draft_reply": cstr(data.get("ai_draft_reply", "")),
        "manual_review_flag": manual_review,
        # Customer-supplied info (may be empty)
        "company_name": cstr(data.get("company_name", "")).strip(),
        "contact_phone": cstr(data.get("contact_phone", "")).strip(),
        "delivery_location": cstr(data.get("delivery_location", "")).strip(),
        "requested_delivery_date": data.get("requested_delivery_date") or None,
        "inquiry_summary": cstr(data.get("inquiry_summary", "")),
        # workflow_state defaults to 'New Inquiry Received' via DocType default / before_insert
    })

    # ------------------------------------------------------------------
    # 7. Add product lines (child table rows)
    # ------------------------------------------------------------------
    product_lines = data.get("product_lines") or []
    if isinstance(product_lines, str):
        try:
            product_lines = json.loads(product_lines)
        except (json.JSONDecodeError, TypeError):
            product_lines = []

    for line in product_lines:
        if not isinstance(line, dict):
            continue
        ticket.append("product_lines", {
            "item_description": cstr(line.get("item_description", "")),
            "quantity": float(line.get("quantity", 0) or 0),
            "uom": cstr(line.get("uom", "")),
            "ai_suggested": 1 if line.get("ai_suggested") else 0,
        })

    # ------------------------------------------------------------------
    # 8. Insert the ticket
    #    before_insert hook handles: dedup, customer/lead, SE routing, SLA
    # ------------------------------------------------------------------
    try:
        ticket.insert(ignore_permissions=True)
        frappe.db.commit()
    except frappe.DuplicateEntryError:
        frappe.db.rollback()
        frappe.local.response["http_status_code"] = 409
        return {
            "status": "error",
            "message": _("Duplicate: an inquiry with this Message ID already exists."),
        }
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(
            title="Inquiry API — Ticket Creation Failed",
            message=frappe.get_traceback(),
        )
        frappe.local.response["http_status_code"] = 500
        return {
            "status": "error",
            "message": _("Failed to create inquiry ticket: {0}").format(str(e)),
        }

    # ------------------------------------------------------------------
    # 9. Create Inquiry AI Log (standalone DocType)
    # ------------------------------------------------------------------
    ai_log_data = data.get("ai_log")
    if isinstance(ai_log_data, str):
        try:
            ai_log_data = json.loads(ai_log_data)
        except (json.JSONDecodeError, TypeError):
            ai_log_data = None

    if ai_log_data and isinstance(ai_log_data, dict):
        try:
            ai_log = frappe.new_doc("Inquiry AI Log")
            ai_log.update({
                "ticket": ticket.name,
                "stage": "Classification",
                "model_used": cstr(ai_log_data.get("model_used", "")),
                "timestamp": now_datetime(),
                "confidence_score": confidence,
                "prompt_tokens": int(ai_log_data.get("prompt_tokens", 0) or 0),
                "completion_tokens": int(ai_log_data.get("completion_tokens", 0) or 0),
                "processing_time_ms": int(ai_log_data.get("processing_time_ms", 0) or 0),
                "raw_response": json.dumps(ai_log_data, indent=2, default=str),
            })
            ai_log.insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            # AI log failure should not block ticket creation
            frappe.log_error(
                title="Inquiry API — AI Log Creation Failed",
                message=frappe.get_traceback(),
            )

    # ------------------------------------------------------------------
    # 10. Handle file attachments (base64-encoded)
    # ------------------------------------------------------------------
    attachments = data.get("attachments") or []
    if isinstance(attachments, str):
        try:
            attachments = json.loads(attachments)
        except (json.JSONDecodeError, TypeError):
            attachments = []

    for att in attachments:
        if not isinstance(att, dict):
            continue
        filename = cstr(att.get("filename", "")).strip()
        content_b64 = att.get("content_base64", "")
        if not filename or not content_b64:
            continue

        try:
            decoded_bytes = base64.b64decode(content_b64)
            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": filename,
                "content": decoded_bytes,
                "attached_to_doctype": "Email Inquiry Ticket",
                "attached_to_name": ticket.name,
                "is_private": 1,
            })
            file_doc.insert(ignore_permissions=True)
        except Exception:
            # Individual attachment failure should not block the response
            frappe.log_error(
                title=f"Inquiry API — Attachment Failed: {filename}",
                message=frappe.get_traceback(),
            )

    frappe.db.commit()

    # ------------------------------------------------------------------
    # 11. Return success response
    # ------------------------------------------------------------------
    return {
        "status": "success",
        "inquiry_name": ticket.name,
        "assigned_to": ticket.assigned_sales_engineer,
        "customer_type": ticket.customer_type,
        "lead_created": ticket.lead or None,
        "sla_due": str(ticket.sla_due_datetime) if ticket.sla_due_datetime else None,
    }


# ---------------------------------------------------------------------------
# Duplicate pre-check endpoint (for n8n idempotency guard)
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=False)
def check_duplicate_message_id(message_id):
    """
    Check whether an Email Inquiry Ticket with the given Message-ID
    already exists. Called by n8n before pushing the full payload to
    avoid unnecessary AI processing costs.

    Args:
        message_id (str): The email Message-ID header value.

    Returns:
        dict: {exists: bool, ticket_name: str|None}
    """
    if not message_id:
        return {"exists": False, "ticket_name": None}

    message_id = cstr(message_id).strip()
    existing = frappe.db.get_value(
        "Email Inquiry Ticket", {"message_id": message_id}, "name"
    )

    return {
        "exists": bool(existing),
        "ticket_name": existing or None,
    }
