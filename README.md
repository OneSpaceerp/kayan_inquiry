<p align="center">
  <img src="https://img.shields.io/badge/Frappe-v16-blue?style=for-the-badge" alt="Frappe v16" />
  <img src="https://img.shields.io/badge/ERPNext-v16-green?style=for-the-badge" alt="ERPNext v16" />
  <img src="https://img.shields.io/badge/Python-3.11+-yellow?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+" />
  <img src="https://img.shields.io/badge/License-MIT-orange?style=for-the-badge" alt="MIT License" />
</p>

# Kayan Inquiry

**AI-powered email inquiry automation & sales workflow management for ERPNext v16.**

Kayan Inquiry transforms incoming pricing inquiry emails into structured, trackable sales pipeline tickets — powered by Claude AI classification and extraction, orchestrated by n8n, and managed entirely within ERPNext.

---

## 🎯 Overview

Kayan Inquiry solves a common challenge for commercial and industrial equipment companies: **high-volume pricing inquiry emails** that require manual triage, classification, and routing to sales engineers.

### What It Does

1. **n8n** monitors an email inbox and forwards new emails to **Claude AI**
2. **Claude Haiku** classifies whether the email is a genuine pricing inquiry (Stage 1)
3. **Claude Sonnet** extracts structured data — sender, company, products, urgency (Stage 2)
4. n8n calls the **Kayan Inquiry REST API** to create a ticket in ERPNext
5. The ticket is **auto-assigned** to the right Sales Engineer based on configurable routing rules
6. Sales Engineers manage the full lifecycle via **workflow buttons** in ERPNext Desk
7. **SLA monitoring** runs in the background, escalating breached tickets automatically

### Key Features

- 🤖 **AI-Powered Classification** — Binary intent detection with confidence scoring
- 📧 **Structured Data Extraction** — Company, products, urgency, and draft reply generation
- 🔀 **Smart Auto-Assignment** — Email mapping → domain matching → round-robin fallback
- 👤 **Customer/Lead Resolution** — Auto-links to existing ERPNext Customers or creates new Leads
- ⏱️ **SLA Engine** — Priority-based thresholds with stage-level escalation
- 📊 **Role-Based Workspaces** — Dashboards for Sales Engineers, Application Engineers, and Managers
- 🔔 **10 Notification Templates** — Email + in-app alerts for every workflow transition
- 📋 **AE Requirements Checklist** — Interactive technical review checklist with role-based editing
- 🔗 **ERPNext Integration** — Quotation creation, Sales Order tracking, Lead management

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SECTION ONE: n8n                            │
│                                                                 │
│  Email Inbox ──► n8n Workflow ──► Claude Haiku (Classification) │
│                                  Claude Sonnet (Extraction)     │
│                        │                                        │
└────────────────────────┼────────────────────────────────────────┘
                         │ POST /api/method/kayan_inquiry.api.create_inquiry
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│               SECTION TWO: kayan_inquiry App                    │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │
│  │  api.py   │  │  sla.py   │  │ utils.py │  │  hooks.py     │  │
│  │ REST API  │  │ Scheduler │  │ Events   │  │ Configuration │  │
│  └────┬─────┘  └─────┬────┘  └─────┬────┘  └───────────────┘  │
│       │              │             │                            │
│       ▼              ▼             ▼                            │
│  ┌──────────────────────────────────────────────────────┐      │
│  │            Email Inquiry Ticket (DocType)             │      │
│  │  ┌─────────────┐ ┌──────────────┐ ┌──────────────┐  │      │
│  │  │Product Lines │ │Activity Log  │ │Assignment Log│  │      │
│  │  │(Child Table) │ │(Child Table) │ │(Child Table) │  │      │
│  │  └─────────────┘ └──────────────┘ └──────────────┘  │      │
│  └──────────────────────────────────────────────────────┘      │
│       │              │              │                           │
│       ▼              ▼              ▼                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐             │
│  │Quotation │  │Sales Order│  │Lead / Customer   │             │
│  │(ERPNext) │  │(ERPNext)  │  │(ERPNext)         │             │
│  └──────────┘  └──────────┘  └──────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📦 Installation

### Prerequisites

- Frappe Bench v16+
- ERPNext v16+
- Python 3.11+

### Install

```bash
# Get the app
bench get-app https://github.com/OneSpaceerp/kayan_inquiry.git

# Install on your site
bench --site your-site.localhost install-app kayan_inquiry

# Run migrations
bench --site your-site.localhost migrate

# Restart
bench restart
```

---

## ⚙️ Configuration

### 1. Custom Field on Quotation

Add a custom field to the standard **Quotation** DocType to enable the backlink from Quotation → Inquiry Ticket:

```
Customize Form → Quotation → Add Field:
  - Fieldname: custom_inquiry_ticket
  - Label: Inquiry Ticket
  - Fieldtype: Link
  - Options: Email Inquiry Ticket
```

### 2. Roles

The app creates 4 custom roles (via fixtures):

| Role | Access Level |
|---|---|
| **Inquiry Admin** | Full CRUD, manage all tickets, configure mappings |
| **Inquiry Sales Manager** | Read/write all tickets, view dashboards, manage lost reasons |
| **Inquiry Sales Engineer** | Read/write own tickets, create quotations, log follow-ups |
| **Inquiry Application Engineer** | Read/write assigned tickets, complete AE checklists |

Assign these roles to your users via **User → Roles**.

### 3. Sales Engineer Mapping

Configure email-to-SE routing via **Sales Engineer Mapping**:

| Field | Description |
|---|---|
| `email_address` | The "To" address to match (e.g., `sales@kayan.com` or `@kayan.com` for domain match) |
| `sales_engineer` | The User to assign tickets to |
| `fallback_rule` | None / Round Robin / Assign to Manager |
| `is_active` | Toggle mapping on/off |

### 4. Lost Reasons

Pre-populate **Inquiry Lost Reason** master data:

- Price, Competitor, Timing, No Response, Technical, Budget Cancelled, Other

### 5. n8n Integration

Configure your n8n workflow to call the API endpoint with an API key or token-based auth.

---

## 🔌 API Reference

### Create Inquiry

```
POST /api/method/kayan_inquiry.api.create_inquiry
Content-Type: application/json
Authorization: token <api_key>:<api_secret>
```

**Request Body:**

```json
{
  "message_id": "<unique-email-message-id>",
  "received_date": "2026-05-19 14:30:00",
  "email_from": "ahmed@customer.com",
  "sender_name": "Ahmed Al-Hassan",
  "email_to": "sales@kayan.com",
  "email_cc": "manager@customer.com",
  "email_subject": "RFQ - Industrial Pumps XP-500",
  "email_body": "Dear Kayan team, please provide pricing for...",
  "ai_confidence_score": 0.92,
  "ai_classification_reason": "Explicit RFQ language with product specifications",
  "ai_draft_reply": "Dear Ahmed, thank you for your inquiry...",
  "company_name": "Hassan Industrial LLC",
  "contact_phone": "+966 50 123 4567",
  "delivery_location": "Riyadh, Saudi Arabia",
  "requested_delivery_date": "2026-07-15",
  "inquiry_summary": "Customer requesting pricing for 10 industrial pumps...",
  "urgency_level": "high",
  "product_lines": [
    {
      "item_description": "Industrial Pump XP-500",
      "quantity": 10,
      "uom": "Nos",
      "ai_suggested": true
    }
  ],
  "ai_log": {
    "model_used": "claude-3-haiku-20240307",
    "prompt_tokens": 1200,
    "completion_tokens": 350,
    "processing_time_ms": 2400
  },
  "attachments": [
    {
      "filename": "specs.pdf",
      "content_base64": "JVBERi0xLjQ...",
      "content_type": "application/pdf"
    }
  ]
}
```

**Success Response (200):**

```json
{
  "status": "success",
  "inquiry_name": "KYN-INQ-2026-00001",
  "assigned_to": "se@kayan.com",
  "customer_type": "Existing Customer",
  "lead_created": null,
  "sla_due": "2026-05-19 16:30:00"
}
```

**Error Responses:**

| Code | Reason |
|---|---|
| `400` | Missing required fields |
| `409` | Duplicate `message_id` |
| `500` | Internal error (logged to Error Log) |

### Check Duplicate

```
POST /api/method/kayan_inquiry.api.check_duplicate_message_id
```

```json
{ "message_id": "<email-message-id>" }
```

Returns: `{ "exists": true/false, "ticket_name": "KYN-INQ-..." }`

---

## 🔄 Workflow

```
                    ┌─────────────────────┐
                    │ New Inquiry Received │
                    └─────────┬───────────┘
                              │ (auto on insert)
                    ┌─────────▼───────────┐
                    │ Assigned to Sales    │
                    │ Engineer             │
                    └─────────┬───────────┘
                              │ [Start Qualification]
                    ┌─────────▼───────────┐
              ┌─────│ Pending             │─────┐
              │     │ Qualification       │     │
              │     └─────────────────────┘     │
   [Request   │                                 │  [Create
    AE Review]│                                 │   Quotation]
              │                                 │
    ┌─────────▼───────────┐           ┌─────────▼───────────┐
    │ Application Engineer│           │ Quotation            │
    │ Review              │──────────►│ Preparation          │◄──┐
    └─────────────────────┘           └─────────┬───────────┘   │
       [AE Review Complete]                     │               │
                               [Mark Sent]      │   [Re-Quote]  │
                              ┌─────────▼───────────┐           │
                              │ Quotation Sent       │           │
                              └─────────┬───────────┘           │
                                        │ [Follow Up]           │
                              ┌─────────▼───────────┐           │
                              │ Negotiation /        │───────────┘
                              │ Follow-Up            │
                              └────┬────────────┬────┘
                     [Mark as Won] │            │ [Mark as Lost]
                    ┌──────────────▼┐    ┌──────▼──────────────┐
                    │ Won: Sales     │    │ Lost / Closed       │
                    │ Order Generated│    │ (Re-Open by Manager)│
                    └────────────────┘    └─────────────────────┘
```

---

## ⏱️ SLA Thresholds

### Overall SLA (by Priority)

| Priority | Threshold | At Risk (80%) | Breached |
|---|---|---|---|
| Critical | 1 hour | 48 min | 1 hour |
| High | 2 hours | 96 min | 2 hours |
| Medium | 4 hours | 3h 12m | 4 hours |
| Low | 8 hours | 6h 24m | 8 hours |

### Stage-Level SLA

| Stage | Max Duration | Action on Breach |
|---|---|---|
| Pending Qualification | 4 hours | Auto-escalate to Critical |
| Application Engineer Review | 24 hours | Alert AE + Manager |
| Quotation Preparation | 48 hours | Alert Manager |
| Quotation Sent | 72 hours | Reminder to SE |

### Background Scheduler

| Schedule | Function | Purpose |
|---|---|---|
| Every 30 min | `check_sla_breaches()` | Update SLA statuses, escalate, notify |
| Daily 08:00 | `send_followup_reminders()` | Overdue follow-ups + daily breach summary |

---

## 📊 DocTypes

| DocType | Type | Naming | Description |
|---|---|---|---|
| **Email Inquiry Ticket** | Main | `KYN-INQ-{YYYY}-{#####}` | Core ticket tracking inquiry lifecycle |
| **Inquiry Product Line** | Child Table | — | Requested items/services per ticket |
| **Inquiry Activity Log** | Child Table | — | Immutable audit trail of all actions |
| **Inquiry Assignment Log** | Child Table | — | SE/AE assignment history |
| **Inquiry Follow-Up** | Standalone | `KYN-FU-{YYYY}-{#####}` | Scheduled follow-up actions |
| **Inquiry AI Log** | Standalone | `KYN-AIL-{YYYY}-{#####}` | AI model usage tracking per ticket |
| **Inquiry Lost Reason** | Master | By `reason_name` | Configurable lost reason categories |
| **Sales Engineer Mapping** | Master | `KYN-SEM-{#####}` | Email → Sales Engineer routing rules |

---

## 🔔 Notifications

| Notification | Trigger | Channel |
|---|---|---|
| New Inquiry Assigned | Ticket created | Email + System |
| AE Review Requested | State → AE Review | Email + System |
| SLA Breach Warning | SLA status change | Email + System |
| Quotation Overdue | Quotation Prep breached | Email |
| AE Checklist Complete | AE review done | System |
| Ticket Won | State → Won | System |
| Ticket Lost | State → Lost | System |
| Follow-Up Due | Due date reached | Email + System |
| Quotation Sent | State → Quotation Sent | System |
| Integration Error | API failure | Email + System |

---

## 🤖 Claude AI Prompts

The app includes two prompt templates for the n8n → Claude AI pipeline:

| Prompt | Model | Purpose | Output |
|---|---|---|---|
| `classification_prompt.md` | Claude Haiku | Binary classification: is this a pricing inquiry? | `{is_inquiry, confidence, reason}` |
| `extraction_prompt.md` | Claude Sonnet | Structured data extraction from qualifying emails | Sender, company, products, urgency, draft reply |

Located in `kayan_inquiry/claude_prompts/`.

---

## 🗂️ Project Structure

```
kayan_inquiry/
├── setup.py
├── requirements.txt
├── .gitignore
└── kayan_inquiry/
    ├── __init__.py
    ├── hooks.py                    # App configuration
    ├── api.py                      # REST API for n8n
    ├── sla.py                      # Background SLA scheduler
    ├── utils.py                    # Quotation/SO event handlers
    ├── modules.txt
    ├── claude_prompts/
    │   ├── classification_prompt.md
    │   └── extraction_prompt.md
    ├── kayan_inquiry/              # Module directory
    │   └── doctype/
    │       ├── email_inquiry_ticket/
    │       │   ├── email_inquiry_ticket.json
    │       │   ├── email_inquiry_ticket.py
    │       │   └── email_inquiry_ticket.js
    │       ├── inquiry_product_line/
    │       ├── inquiry_activity_log/
    │       ├── inquiry_assignment_log/
    │       ├── inquiry_follow_up/
    │       ├── inquiry_ai_log/
    │       ├── inquiry_lost_reason/
    │       └── sales_engineer_mapping/
    ├── notification/               # 10 notification templates
    │   ├── new_inquiry_assigned.json
    │   ├── ae_review_requested.json
    │   ├── sla_breach_warning.json
    │   └── ... (7 more)
    └── workspace/                  # 3 role-based workspaces
        ├── sales_engineer_inquiry_workspace.json
        ├── application_engineer_workspace.json
        └── sales_manager_inquiry_workspace.json
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

**Nest Software Development**

Built for [Kayan](https://kayan.com) — Commercial & Industrial Equipment Solutions.
