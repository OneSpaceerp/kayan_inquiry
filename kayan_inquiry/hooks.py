from . import __version__ as app_version

app_name = "kayan_inquiry"
app_title = "Kayan Inquiry"
app_publisher = "Nest Software Development"
app_description = "AI Email Inquiry Automation & Sales Workflow for ERPNext v16"
app_email = "kelshiekh@gmail.com"
app_license = "MIT"
app_version = app_version

# ----------------------------------------------------------
# Module registration
# ----------------------------------------------------------
app_include_css = []
app_include_js = []

# ----------------------------------------------------------
# DocType JS (client-side scripts)
# ----------------------------------------------------------
doctype_js = {
    "Email Inquiry Ticket": "kayan_inquiry/doctype/email_inquiry_ticket/email_inquiry_ticket.js",
}

# ----------------------------------------------------------
# Scheduled Jobs (SLA checker runs every 30 minutes)
# ----------------------------------------------------------
scheduler_events = {
    "cron": {
        # Every 30 minutes: check SLA breaches
        "*/30 * * * *": [
            "kayan_inquiry.sla.check_sla_breaches"
        ],
        # Every day at 08:00: send overdue follow-up reminders
        "0 8 * * *": [
            "kayan_inquiry.sla.send_followup_reminders"
        ],
    }
}

# ----------------------------------------------------------
# Document Events (hooks into DocType lifecycle)
# ----------------------------------------------------------
doc_events = {
    "Email Inquiry Ticket": {
        "before_insert": "kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.before_insert",
        "after_insert": "kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.after_insert",
        "on_update": "kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.on_update",
    },
    "Quotation": {
        "on_submit": "kayan_inquiry.utils.on_quotation_submit",
    },
    "Sales Order": {
        "on_submit": "kayan_inquiry.utils.on_sales_order_submit",
    },
}

# ----------------------------------------------------------
# Fixtures — export these with bench export-fixtures
# ----------------------------------------------------------
fixtures = [
    {
        "doctype": "Role",
        "filters": [
            ["name", "in", [
                "Inquiry Sales Engineer",
                "Inquiry Application Engineer",
                "Inquiry Sales Manager",
                "Inquiry Admin",
            ]]
        ]
    },
    {
        "doctype": "Workflow",
        "filters": [["name", "=", "Inquiry Ticket Workflow"]]
    },
    {
        "doctype": "Notification",
        "filters": [
            ["name", "in", [
                "New Inquiry Assigned to Sales Engineer",
                "AE Technical Review Requested",
                "SLA Breach Warning",
                "Quotation Overdue Alert",
                "AE Checklist Complete",
                "Inquiry Follow-Up Due",
                "Ticket Won Notification",
                "Ticket Lost Notification",
                "Quotation Sent Confirmation",
                "Inquiry Integration Error",
            ]]
        ]
    },
    {
        "doctype": "Workspace",
        "filters": [
            ["name", "in", [
                "Sales Engineer Inquiry Workspace",
                "Application Engineer Workspace",
                "Sales Manager Inquiry Workspace",
            ]]
        ]
    },
]

# ----------------------------------------------------------
# Website (unused in v1)
# ----------------------------------------------------------
website_route_rules = []

# ----------------------------------------------------------
# Permissions
# ----------------------------------------------------------
has_permission = {
    "Email Inquiry Ticket": "kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.has_permission",
}
