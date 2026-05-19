"""
kayan_inquiry.sla — SLA Monitoring & Follow-Up Reminders
=========================================================
Background scheduler tasks that run via hooks.py ``scheduler_events``:

    */30 * * * *  →  check_sla_breaches()
    0 8 * * *     →  send_followup_reminders()

SLA thresholds (overall, from PRD):
    Critical  — 1 hour
    High      — 2 hours
    Medium    — 4 hours
    Low       — 8 hours

Stage-level SLAs:
    New → Assigned             — 30 min   (handled at insert, not checked here)
    Assigned → Qualification   — 2 hours
    Pending Qualification idle — 4 hours   (auto-escalate to Critical)
    AE Review                  — 24 hours  (alert AE + manager)
    Quotation Preparation      — 48 hours  (alert manager)
    Quotation Sent             — 72 hours  (reminder to SE)
"""

import frappe
from frappe import _
from frappe.utils import (
    now_datetime,
    get_datetime,
    time_diff_in_hours,
    today,
    formatdate,
    get_url,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLA_HOURS_BY_PRIORITY = {
    "Critical": 1,
    "High": 2,
    "Medium": 4,
    "Low": 8,
}

# Stage-level thresholds (hours)
STAGE_SLA = {
    "Pending Qualification": 4,
    "Application Engineer Review": 24,
    "Quotation Preparation": 48,
    "Quotation Sent": 72,
}

# The "at risk" threshold is 80% of the SLA window
AT_RISK_RATIO = 0.80

# Terminal states — skip these tickets entirely
CLOSED_STATES = ("Won: Sales Order Generated", "Lost / Closed")


# ---------------------------------------------------------------------------
# Scheduler: check_sla_breaches  (runs every 30 minutes)
# ---------------------------------------------------------------------------

def check_sla_breaches():
    """
    Evaluate SLA status for all active Email Inquiry Tickets.
    Updates sla_status, auto-escalates stale tickets, and sends
    notifications when status transitions occur.
    """
    now = now_datetime()

    # Fetch all active tickets in one query
    tickets = frappe.db.sql(
        """
        SELECT
            name,
            priority,
            sla_status,
            sla_due_datetime,
            workflow_state,
            received_date,
            modified,
            assigned_sales_engineer,
            application_engineer,
            email_subject
        FROM `tabEmail Inquiry Ticket`
        WHERE workflow_state NOT IN %(closed)s
        """,
        {"closed": CLOSED_STATES},
        as_dict=True,
    )

    for ticket in tickets:
        try:
            _evaluate_ticket_sla(ticket, now)
        except Exception:
            frappe.log_error(
                title=f"SLA Check Failed — {ticket.name}",
                message=frappe.get_traceback(),
            )

    frappe.db.commit()


def _evaluate_ticket_sla(ticket, now):
    """
    For a single ticket:
    1. Compute overall SLA status (On Track / At Risk / Breached)
    2. Run stage-level checks (escalation, alerts)
    3. Persist changes via db.set_value (avoids triggering doc events)
    4. Send notifications on status transitions
    """
    received = get_datetime(ticket.received_date) if ticket.received_date else now
    elapsed_hours = time_diff_in_hours(now, received)

    threshold = SLA_HOURS_BY_PRIORITY.get(ticket.priority, 4)
    old_status = ticket.sla_status or "On Track"

    # ------------------------------------------------------------------
    # Overall SLA status
    # ------------------------------------------------------------------
    if elapsed_hours >= threshold:
        new_status = "Breached"
    elif elapsed_hours >= (threshold * AT_RISK_RATIO):
        new_status = "At Risk"
    else:
        new_status = "On Track"

    # Persist if changed
    if new_status != old_status:
        frappe.db.set_value(
            "Email Inquiry Ticket", ticket.name,
            "sla_status", new_status,
            update_modified=False,
        )
        _notify_sla_transition(ticket, old_status, new_status)

    # ------------------------------------------------------------------
    # Stage-level SLA checks
    # ------------------------------------------------------------------
    state = ticket.workflow_state
    # Use the ticket's last modified timestamp to gauge time-in-stage
    stage_start = get_datetime(ticket.modified) if ticket.modified else received
    stage_hours = time_diff_in_hours(now, stage_start)

    # --- Pending Qualification idle for >4 hours → auto-escalate to Critical ---
    if state == "Pending Qualification" and stage_hours > STAGE_SLA["Pending Qualification"]:
        if ticket.priority != "Critical":
            frappe.db.set_value(
                "Email Inquiry Ticket", ticket.name,
                {"priority": "Critical", "sla_status": "Breached"},
                update_modified=False,
            )
            _send_escalation_email(
                ticket,
                reason="Pending Qualification idle for {0:.1f} hours (threshold: {1}h). "
                       "Priority auto-escalated to Critical.".format(
                           stage_hours, STAGE_SLA["Pending Qualification"]
                       ),
            )

    # --- AE Review stale for >24 hours → alert AE and manager ---
    elif state == "Application Engineer Review" and stage_hours > STAGE_SLA["Application Engineer Review"]:
        _send_stage_alert(
            ticket,
            stage="Application Engineer Review",
            hours_elapsed=stage_hours,
            threshold=STAGE_SLA["Application Engineer Review"],
            recipients=_get_ae_and_manager_emails(ticket),
        )

    # --- Quotation Preparation stale for >48 hours → alert manager ---
    elif state == "Quotation Preparation" and stage_hours > STAGE_SLA["Quotation Preparation"]:
        _send_stage_alert(
            ticket,
            stage="Quotation Preparation",
            hours_elapsed=stage_hours,
            threshold=STAGE_SLA["Quotation Preparation"],
            recipients=_get_manager_emails(),
        )

    # --- Quotation Sent stale for >72 hours → remind SE ---
    elif state == "Quotation Sent" and stage_hours > STAGE_SLA["Quotation Sent"]:
        se_email = frappe.db.get_value("User", ticket.assigned_sales_engineer, "email")
        if se_email:
            _send_stage_alert(
                ticket,
                stage="Quotation Sent",
                hours_elapsed=stage_hours,
                threshold=STAGE_SLA["Quotation Sent"],
                recipients=[se_email],
            )


# ---------------------------------------------------------------------------
# Scheduler: send_followup_reminders  (runs daily at 08:00)
# ---------------------------------------------------------------------------

def send_followup_reminders():
    """
    1. Remind Sales Engineers about overdue follow-ups.
    2. Send a daily SLA breach/at-risk summary to all Sales Managers.
    """
    _send_overdue_followup_reminders()
    _send_daily_breach_summary()
    frappe.db.commit()


def _send_overdue_followup_reminders():
    """Email each SE about their overdue Inquiry Follow-Ups."""
    overdue = frappe.db.sql(
        """
        SELECT
            fu.name          AS followup_name,
            fu.ticket        AS ticket_name,
            fu.follow_up_type,
            fu.due_date,
            fu.summary,
            t.assigned_sales_engineer,
            t.email_subject
        FROM `tabInquiry Follow-Up` fu
        INNER JOIN `tabEmail Inquiry Ticket` t ON t.name = fu.ticket
        WHERE fu.due_date <= %(today)s
          AND fu.completed = 0
          AND t.workflow_state NOT IN %(closed)s
        ORDER BY fu.due_date ASC
        """,
        {"today": today(), "closed": CLOSED_STATES},
        as_dict=True,
    )

    if not overdue:
        return

    # Group by SE for a consolidated email
    se_followups = {}
    for row in overdue:
        se = row.assigned_sales_engineer
        if not se:
            continue
        se_followups.setdefault(se, []).append(row)

    for se_user, items in se_followups.items():
        se_email = frappe.db.get_value("User", se_user, "email")
        se_name = frappe.db.get_value("User", se_user, "full_name") or se_user
        if not se_email:
            continue

        rows_html = ""
        for item in items:
            ticket_url = f"{get_url()}/app/email-inquiry-ticket/{item.ticket_name}"
            rows_html += f"""
            <tr>
                <td><a href="{ticket_url}">{item.ticket_name}</a></td>
                <td>{item.email_subject or ''}</td>
                <td>{item.follow_up_type}</td>
                <td>{formatdate(item.due_date)}</td>
                <td>{item.summary or ''}</td>
            </tr>"""

        message = f"""
        <p>Dear {se_name},</p>
        <p>You have <b>{len(items)}</b> overdue follow-up(s) that require your attention:</p>
        <table border="1" cellpadding="6" cellspacing="0"
               style="border-collapse:collapse; width:100%; font-size:13px;">
            <thead>
                <tr style="background:#f5f5f5;">
                    <th>Ticket</th>
                    <th>Subject</th>
                    <th>Type</th>
                    <th>Due Date</th>
                    <th>Notes</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        <p style="margin-top:12px;">
            Please complete or reschedule these follow-ups as soon as possible.
        </p>
        """

        frappe.sendmail(
            recipients=[se_email],
            subject=_("[Overdue Follow-Ups] {0} pending action(s)").format(len(items)),
            message=message,
            reference_doctype="Email Inquiry Ticket",
            now=True,
        )


def _send_daily_breach_summary():
    """
    Compile all tickets with sla_status = 'Breached' or 'At Risk'
    and send a summary email to all users with the Inquiry Sales Manager role.
    """
    manager_emails = _get_manager_emails()
    if not manager_emails:
        return

    at_risk_tickets = frappe.db.sql(
        """
        SELECT
            name,
            email_subject,
            priority,
            sla_status,
            sla_due_datetime,
            workflow_state,
            assigned_sales_engineer,
            received_date
        FROM `tabEmail Inquiry Ticket`
        WHERE sla_status IN ('Breached', 'At Risk')
          AND workflow_state NOT IN %(closed)s
        ORDER BY
            FIELD(sla_status, 'Breached', 'At Risk'),
            sla_due_datetime ASC
        """,
        {"closed": CLOSED_STATES},
        as_dict=True,
    )

    if not at_risk_tickets:
        return  # Nothing to report

    breached = [t for t in at_risk_tickets if t.sla_status == "Breached"]
    at_risk = [t for t in at_risk_tickets if t.sla_status == "At Risk"]

    rows_html = ""
    for t in at_risk_tickets:
        ticket_url = f"{get_url()}/app/email-inquiry-ticket/{t.name}"
        se_name = frappe.db.get_value("User", t.assigned_sales_engineer, "full_name") or t.assigned_sales_engineer or "-"
        status_color = "#d32f2f" if t.sla_status == "Breached" else "#f57c00"
        rows_html += f"""
        <tr>
            <td><a href="{ticket_url}">{t.name}</a></td>
            <td>{t.email_subject or ''}</td>
            <td>{t.priority}</td>
            <td style="color:{status_color}; font-weight:bold;">{t.sla_status}</td>
            <td>{t.sla_due_datetime or '-'}</td>
            <td>{t.workflow_state}</td>
            <td>{se_name}</td>
        </tr>"""

    message = f"""
    <h3>Daily SLA Summary — {formatdate(today())}</h3>
    <p>
        <b style="color:#d32f2f;">{len(breached)} Breached</b> &nbsp;|&nbsp;
        <b style="color:#f57c00;">{len(at_risk)} At Risk</b>
    </p>
    <table border="1" cellpadding="6" cellspacing="0"
           style="border-collapse:collapse; width:100%; font-size:13px;">
        <thead>
            <tr style="background:#f5f5f5;">
                <th>Ticket</th>
                <th>Subject</th>
                <th>Priority</th>
                <th>SLA Status</th>
                <th>SLA Due</th>
                <th>Stage</th>
                <th>Assigned SE</th>
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    <p style="margin-top:12px;">
        <a href="{get_url()}/app/email-inquiry-ticket?sla_status=Breached">
            View all breached tickets →
        </a>
    </p>
    """

    frappe.sendmail(
        recipients=manager_emails,
        subject=_("[SLA Report] {0} Breached, {1} At Risk — {2}").format(
            len(breached), len(at_risk), formatdate(today())
        ),
        message=message,
        reference_doctype="Email Inquiry Ticket",
        now=True,
    )


# ---------------------------------------------------------------------------
# Notification helpers
# ---------------------------------------------------------------------------

def _notify_sla_transition(ticket, old_status, new_status):
    """
    Send email + realtime alert when SLA status transitions:
        On Track → At Risk
        At Risk  → Breached
    """
    se_user = ticket.assigned_sales_engineer
    if not se_user:
        return

    se_email = frappe.db.get_value("User", se_user, "email")
    se_name = frappe.db.get_value("User", se_user, "full_name") or se_user

    ticket_url = f"{get_url()}/app/email-inquiry-ticket/{ticket.name}"

    if new_status == "Breached":
        subject = _("[SLA BREACHED] {0} — {1}").format(ticket.name, ticket.email_subject)
        alert_type = "red"
    else:
        subject = _("[SLA At Risk] {0} — {1}").format(ticket.name, ticket.email_subject)
        alert_type = "orange"

    message = f"""
    <p>Dear {se_name},</p>
    <p>The SLA status for inquiry <b>{ticket.name}</b> has changed
       from <b>{old_status}</b> to <b style="color:{alert_type};">{new_status}</b>.</p>
    <ul>
        <li><b>Subject:</b> {ticket.email_subject}</li>
        <li><b>Priority:</b> {ticket.priority}</li>
        <li><b>Current Stage:</b> {ticket.workflow_state}</li>
        <li><b>SLA Due:</b> {ticket.sla_due_datetime or 'N/A'}</li>
    </ul>
    <p><a href="{ticket_url}">Open Ticket →</a></p>
    """

    # Email notification
    recipients = [se_email] if se_email else []
    # Also notify managers on breach
    if new_status == "Breached":
        recipients.extend(_get_manager_emails())
        # De-duplicate
        recipients = list(set(recipients))

    if recipients:
        frappe.sendmail(
            recipients=recipients,
            subject=subject,
            message=message,
            reference_doctype="Email Inquiry Ticket",
            reference_name=ticket.name,
            now=True,
        )

    # In-app realtime notification to SE
    frappe.publish_realtime(
        "eval:frappe.show_alert",
        {
            "message": _("SLA {0}: {1}").format(new_status, ticket.name),
            "indicator": alert_type,
        },
        user=se_user,
    )


def _send_escalation_email(ticket, reason):
    """Email SE + managers about an automatic priority escalation."""
    recipients = _get_manager_emails()
    se_email = frappe.db.get_value("User", ticket.assigned_sales_engineer, "email")
    if se_email:
        recipients.append(se_email)

    if not recipients:
        return

    ticket_url = f"{get_url()}/app/email-inquiry-ticket/{ticket.name}"
    message = f"""
    <p><b>Auto-Escalation Notice</b></p>
    <p>Ticket <a href="{ticket_url}"><b>{ticket.name}</b></a> has been
       automatically escalated.</p>
    <p><b>Reason:</b> {reason}</p>
    <ul>
        <li><b>Subject:</b> {ticket.email_subject}</li>
        <li><b>Assigned SE:</b> {ticket.assigned_sales_engineer}</li>
    </ul>
    """

    frappe.sendmail(
        recipients=list(set(recipients)),
        subject=_("[ESCALATION] {0} — Priority raised to Critical").format(ticket.name),
        message=message,
        reference_doctype="Email Inquiry Ticket",
        reference_name=ticket.name,
        now=True,
    )

    frappe.publish_realtime(
        "eval:frappe.show_alert",
        {
            "message": _("Escalated to Critical: {0}").format(ticket.name),
            "indicator": "red",
        },
        user=ticket.assigned_sales_engineer,
    )


def _send_stage_alert(ticket, stage, hours_elapsed, threshold, recipients):
    """Generic stage-level SLA alert email."""
    if not recipients:
        return

    ticket_url = f"{get_url()}/app/email-inquiry-ticket/{ticket.name}"
    message = f"""
    <p><b>Stage SLA Alert</b></p>
    <p>Ticket <a href="{ticket_url}"><b>{ticket.name}</b></a> has been in
       <b>{stage}</b> for <b>{hours_elapsed:.1f} hours</b>
       (threshold: {threshold}h).</p>
    <ul>
        <li><b>Subject:</b> {ticket.email_subject}</li>
        <li><b>Priority:</b> {ticket.priority}</li>
        <li><b>Assigned SE:</b> {ticket.assigned_sales_engineer or '-'}</li>
        <li><b>Application Engineer:</b> {ticket.application_engineer or '-'}</li>
    </ul>
    <p>Please take action to progress this inquiry.</p>
    """

    frappe.sendmail(
        recipients=list(set(recipients)),
        subject=_("[Stage Alert] {0} — {1} overdue ({2:.0f}h)").format(
            ticket.name, stage, hours_elapsed
        ),
        message=message,
        reference_doctype="Email Inquiry Ticket",
        reference_name=ticket.name,
        now=True,
    )


# ---------------------------------------------------------------------------
# Utility: recipient lookups
# ---------------------------------------------------------------------------

def _get_manager_emails():
    """Return email addresses of all active users with 'Inquiry Sales Manager' role."""
    managers = frappe.db.sql(
        """
        SELECT DISTINCT u.email
        FROM `tabUser` u
        INNER JOIN `tabHas Role` hr ON hr.parent = u.name
        WHERE hr.role = 'Inquiry Sales Manager'
          AND u.enabled = 1
          AND u.email IS NOT NULL
          AND u.email != ''
        """,
        as_dict=True,
    )
    return [m.email for m in managers if m.email]


def _get_ae_and_manager_emails(ticket):
    """Return a combined list of AE email + manager emails for a ticket."""
    emails = _get_manager_emails()
    ae_user = ticket.get("application_engineer") if isinstance(ticket, dict) else getattr(ticket, "application_engineer", None)
    if ae_user:
        ae_email = frappe.db.get_value("User", ae_user, "email")
        if ae_email:
            emails.append(ae_email)
    return emails
