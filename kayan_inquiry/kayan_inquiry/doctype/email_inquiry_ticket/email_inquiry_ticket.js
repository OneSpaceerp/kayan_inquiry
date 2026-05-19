// Email Inquiry Ticket — Client-side controller
// Frappe/ERPNext v16

frappe.ui.form.on('Email Inquiry Ticket', {

	// -----------------------------------------------------------------------
	// SETUP
	// -----------------------------------------------------------------------
	setup(frm) {
		// Filter lead field to show only open leads
		frm.set_query('lead', function () {
			return { filters: { status: ['!=', 'Converted'] } };
		});

		// Filter customer field to show enabled customers
		frm.set_query('customer', function () {
			return { filters: { disabled: 0 } };
		});

		// Filter assigned_sales_engineer to users with Inquiry Sales Engineer role
		frm.set_query('assigned_sales_engineer', function () {
			return {
				query: 'frappe.core.doctype.user.user.user_query',
				filters: { enabled: 1 }
			};
		});

		// Filter application_engineer to users with Inquiry Application Engineer role
		frm.set_query('application_engineer', function () {
			return {
				query: 'frappe.core.doctype.user.user.user_query',
				filters: { enabled: 1 }
			};
		});
	},

	// -----------------------------------------------------------------------
	// REFRESH — Main orchestration
	// -----------------------------------------------------------------------
	refresh(frm) {
		// Apply SLA color indicator
		apply_sla_color(frm);

		// Show workflow state as page indicator
		set_workflow_page_indicator(frm);

		// Render AE requirements checklist
		render_ae_checklist(frm);

		// Add workflow action buttons
		if (!frm.is_new()) {
			add_workflow_buttons(frm);
		}
	},

	// -----------------------------------------------------------------------
	// FIELD CHANGE HANDLERS
	// -----------------------------------------------------------------------
	customer_type(frm) {
		if (frm.doc.customer_type === 'New Lead') {
			frm.set_value('customer', '');
		} else if (frm.doc.customer_type === 'Existing Customer') {
			frm.set_value('lead', '');
		}
	},

	priority(frm) {
		// Re-render SLA indicator when priority changes
		apply_sla_color(frm);
	},

	sla_status(frm) {
		apply_sla_color(frm);
	},

	workflow_state(frm) {
		set_workflow_page_indicator(frm);
	}
});


// ===========================================================================
// SLA COLOR CODING
// ===========================================================================

function apply_sla_color(frm) {
	if (!frm.doc.sla_status) return;

	const color_map = {
		'On Track': '#28a745',   // green
		'At Risk': '#f39c12',    // orange
		'Breached': '#dc3545'    // red
	};

	const color = color_map[frm.doc.sla_status] || '#6c757d';

	// Apply to the sla_status field display
	const $sla_wrapper = frm.fields_dict.sla_status && frm.fields_dict.sla_status.$wrapper;
	if ($sla_wrapper) {
		$sla_wrapper.find('.like-disabled-input, .control-value, .static-area')
			.css({
				'color': color,
				'font-weight': 'bold'
			});
	}

	// Also set a colored indicator dot
	const indicator_class_map = {
		'On Track': 'green',
		'At Risk': 'orange',
		'Breached': 'red'
	};
	const indicator = indicator_class_map[frm.doc.sla_status];
	if (indicator && frm.doc.sla_status) {
		frm.dashboard.set_headline_alert(
			`<span class="indicator whitespace-nowrap ${indicator}">
				<span class="hidden-xs">SLA: ${frm.doc.sla_status}</span>
			</span>`
		);
	}
}


// ===========================================================================
// WORKFLOW STATE PAGE INDICATOR
// ===========================================================================

function set_workflow_page_indicator(frm) {
	if (!frm.doc.workflow_state) return;

	const state_colors = {
		'New Inquiry Received':         'blue',
		'Assigned to Sales Engineer':   'cyan',
		'Pending Qualification':        'orange',
		'Application Engineer Review':  'purple',
		'Quotation Preparation':        'yellow',
		'Quotation Sent':               'blue',
		'Negotiation / Follow-Up':      'orange',
		'Won: Sales Order Generated':   'green',
		'Lost / Closed':                'red'
	};

	const color = state_colors[frm.doc.workflow_state] || 'gray';
	frm.page.set_indicator(frm.doc.workflow_state, color);
}


// ===========================================================================
// WORKFLOW ACTION BUTTONS
// ===========================================================================

function add_workflow_buttons(frm) {
	const state = frm.doc.workflow_state;
	if (!state) return;

	const user_roles = frappe.user_roles || [];
	const is_manager = user_roles.includes('Inquiry Sales Manager') || user_roles.includes('Inquiry Admin');
	const is_assigned_ae = frm.doc.application_engineer === frappe.session.user;
	const is_won = state === 'Won: Sales Order Generated';
	const is_lost = state === 'Lost / Closed';
	const is_active = !is_won && !is_lost;

	// a) Start Qualification
	if (state === 'New Inquiry Received' || state === 'Assigned to Sales Engineer') {
		frm.add_custom_button(__('Start Qualification'), function () {
			frm.set_value('workflow_state', 'Pending Qualification');
			frm.save();
		}, __('Actions'));
	}

	// b) Create Quotation
	if ((state === 'Pending Qualification' || state === 'Quotation Preparation') && !frm.doc.linked_quotation) {
		frm.add_custom_button(__('Create Quotation'), function () {
			frappe.confirm(
				__('Create a Quotation from this inquiry ticket?'),
				function () {
					frappe.call({
						method: 'kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.create_quotation_from_ticket',
						args: { ticket_name: frm.doc.name },
						freeze: true,
						freeze_message: __('Creating Quotation...'),
						callback: function (r) {
							if (r.message) {
								frappe.set_route('Form', 'Quotation', r.message);
							}
						}
					});
				}
			);
		}, __('Actions'));
	}

	// c) Request AE Review
	if (state === 'Pending Qualification') {
		frm.add_custom_button(__('Request AE Review'), function () {
			show_ae_review_dialog(frm);
		}, __('Actions'));
	}

	// d) AE Review Complete
	if (state === 'Application Engineer Review') {
		if (is_assigned_ae || is_manager) {
			frm.add_custom_button(__('AE Review Complete'), function () {
				// Validate checklist completion
				if (!validate_ae_checklist_complete(frm)) {
					frappe.msgprint({
						title: __('Checklist Incomplete'),
						indicator: 'red',
						message: __('All checked AE requirement items must be completed before marking the review as done.')
					});
					return;
				}
				frm.set_value('workflow_state', 'Quotation Preparation');
				frm.save();
			}, __('Actions'));
		}
	}

	// e) Mark Quotation Sent
	if (state === 'Quotation Preparation') {
		frm.add_custom_button(__('Mark Quotation Sent'), function () {
			frm.set_value('workflow_state', 'Quotation Sent');
			frm.save();
		}, __('Actions'));
	}

	// f) Follow Up
	if (state === 'Quotation Sent') {
		frm.add_custom_button(__('Follow Up'), function () {
			frm.set_value('workflow_state', 'Negotiation / Follow-Up');
			frm.save();
		}, __('Actions'));
	}

	// g) Re-Quote
	if (state === 'Negotiation / Follow-Up') {
		frm.add_custom_button(__('Re-Quote'), function () {
			frm.set_value('workflow_state', 'Quotation Preparation');
			frm.save();
		}, __('Actions'));
	}

	// h) Mark as Won
	if (state === 'Quotation Sent' || state === 'Negotiation / Follow-Up') {
		frm.add_custom_button(__('Mark as Won'), function () {
			frappe.confirm(
				__('Are you sure you want to mark this inquiry as Won? This will trigger Sales Order generation.'),
				function () {
					frappe.call({
						method: 'kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.mark_as_won',
						args: { ticket_name: frm.doc.name },
						freeze: true,
						freeze_message: __('Marking as Won...'),
						callback: function (r) {
							if (r.message && r.message.status === 'ok') {
								frappe.show_alert({
									message: __('Inquiry marked as Won!'),
									indicator: 'green'
								});
								frm.reload_doc();
							}
						}
					});
				}
			);
		}, __('Actions'));
		frm.page.inner_toolbar.find('[data-label="Mark+as+Won"]')
			.removeClass('btn-default').addClass('btn-success');
	}

	// i) Mark as Lost
	if (is_active) {
		frm.add_custom_button(__('Mark as Lost'), function () {
			show_mark_as_lost_dialog(frm);
		}, __('Actions'));
	}

	// j) Re-Open
	if (is_lost) {
		if (is_manager) {
			frm.add_custom_button(__('Re-Open'), function () {
				frappe.confirm(
					__('Re-open this inquiry and set it to Pending Qualification?'),
					function () {
						frm.set_value('workflow_state', 'Pending Qualification');
						frm.set_value('lost_reason', '');
						frm.set_value('lost_reason_notes', '');
						frm.save();
					}
				);
			}, __('Actions'));
		}
	}

	// k) Log Follow-Up
	if (is_active) {
		frm.add_custom_button(__('Log Follow-Up'), function () {
			show_follow_up_dialog(frm);
		}, __('Follow-Up'));
	}
}


// ===========================================================================
// AE REVIEW DIALOG
// ===========================================================================

function show_ae_review_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Request Application Engineer Review'),
		fields: [
			{
				label: __('Application Engineer'),
				fieldname: 'ae_user',
				fieldtype: 'Link',
				options: 'User',
				reqd: 1,
				get_query: function () {
					return {
						query: 'frappe.core.doctype.user.user.user_query',
						filters: { enabled: 1 }
					};
				},
				description: __('Select a user with the Inquiry Application Engineer role')
			},
			{
				label: __('Review Notes'),
				fieldname: 'review_notes',
				fieldtype: 'Text',
				description: __('Describe what the Application Engineer should review')
			}
		],
		primary_action_label: __('Request Review'),
		primary_action: function (values) {
			frappe.call({
				method: 'kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.request_ae_review',
				args: {
					ticket_name: frm.doc.name,
					ae_user: values.ae_user
				},
				freeze: true,
				freeze_message: __('Requesting AE Review...'),
				callback: function (r) {
					if (r.message && r.message.status === 'ok') {
						dialog.hide();
						frappe.show_alert({
							message: __('AE Review requested from {0}', [r.message.ae]),
							indicator: 'green'
						});
						frm.reload_doc();
					}
				}
			});
		}
	});
	dialog.show();
}


// ===========================================================================
// MARK AS LOST DIALOG
// ===========================================================================

function show_mark_as_lost_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Mark as Lost'),
		fields: [
			{
				label: __('Lost Reason'),
				fieldname: 'lost_reason',
				fieldtype: 'Link',
				options: 'Inquiry Lost Reason',
				reqd: 1
			},
			{
				label: __('Notes'),
				fieldname: 'lost_notes',
				fieldtype: 'Text',
				description: __('Optional notes about why the inquiry was lost')
			}
		],
		primary_action_label: __('Mark as Lost'),
		primary_action: function (values) {
			frappe.call({
				method: 'kayan_inquiry.kayan_inquiry.doctype.email_inquiry_ticket.email_inquiry_ticket.mark_as_lost',
				args: {
					ticket_name: frm.doc.name,
					lost_reason: values.lost_reason,
					lost_notes: values.lost_notes || ''
				},
				freeze: true,
				freeze_message: __('Marking as Lost...'),
				callback: function (r) {
					if (r.message && r.message.status === 'ok') {
						dialog.hide();
						frappe.show_alert({
							message: __('Inquiry marked as Lost.'),
							indicator: 'red'
						});
						frm.reload_doc();
					}
				}
			});
		}
	});
	dialog.show();
}


// ===========================================================================
// FOLLOW-UP DIALOG
// ===========================================================================

function show_follow_up_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __('Log Follow-Up'),
		fields: [
			{
				label: __('Follow-Up Type'),
				fieldname: 'follow_up_type',
				fieldtype: 'Select',
				options: 'Email\nCall\nMeeting\nInternal Note',
				reqd: 1,
				default: 'Email'
			},
			{
				label: __('Due Date'),
				fieldname: 'due_date',
				fieldtype: 'Date',
				reqd: 1,
				default: frappe.datetime.add_days(frappe.datetime.nowdate(), 3)
			},
			{
				label: __('Summary'),
				fieldname: 'summary',
				fieldtype: 'Text',
				reqd: 1,
				description: __('Brief description of the follow-up action')
			}
		],
		primary_action_label: __('Log'),
		primary_action: function (values) {
			frappe.call({
				method: 'frappe.client.insert',
				args: {
					doc: {
						doctype: 'Inquiry Follow-Up',
						ticket: frm.doc.name,
						follow_up_type: values.follow_up_type,
						due_date: values.due_date,
						summary: values.summary,
						created_by: frappe.session.user,
						completed: 0
					}
				},
				freeze: true,
				freeze_message: __('Logging Follow-Up...'),
				callback: function (r) {
					if (r.message) {
						dialog.hide();
						frappe.show_alert({
							message: __('Follow-Up logged: {0}', [r.message.name]),
							indicator: 'green'
						});
						frm.reload_doc();
					}
				}
			});
		}
	});
	dialog.show();
}


// ===========================================================================
// AE REQUIREMENTS CHECKLIST — Rendering & Interaction
// ===========================================================================

const DEFAULT_AE_CHECKLIST = [
	{ label: 'Requires Server Sizing', checked: false, completed: false },
	{ label: 'Requires Cloud Hosting Assessment', checked: false, completed: false },
	{ label: 'Requires Compatibility Check', checked: false, completed: false },
	{ label: 'Requires Site Survey', checked: false, completed: false }
];

function get_ae_checklist(frm) {
	if (!frm.doc.ae_requirements) {
		return JSON.parse(JSON.stringify(DEFAULT_AE_CHECKLIST));
	}
	try {
		const parsed = JSON.parse(frm.doc.ae_requirements);
		if (Array.isArray(parsed) && parsed.length > 0) {
			return parsed;
		}
		return JSON.parse(JSON.stringify(DEFAULT_AE_CHECKLIST));
	} catch (e) {
		return JSON.parse(JSON.stringify(DEFAULT_AE_CHECKLIST));
	}
}

function render_ae_checklist(frm) {
	const wrapper_field = frm.fields_dict.ae_requirements;
	if (!wrapper_field || !wrapper_field.$wrapper) return;

	// Remove previously rendered checklist
	wrapper_field.$wrapper.find('.ae-checklist-container').remove();

	const checklist = get_ae_checklist(frm);
	const user_roles = frappe.user_roles || [];
	const is_se = user_roles.includes('Inquiry Sales Engineer') || user_roles.includes('Inquiry Sales Manager') || user_roles.includes('Inquiry Admin');
	const is_ae = frm.doc.application_engineer === frappe.session.user
		|| user_roles.includes('Inquiry Application Engineer')
		|| user_roles.includes('Inquiry Admin');
	const is_read_only = frm.doc.workflow_state === 'Won: Sales Order Generated'
		|| frm.doc.workflow_state === 'Lost / Closed';

	let html = `
		<div class="ae-checklist-container" style="margin-top: 10px; padding: 12px; border: 1px solid var(--border-color, #d1d8dd); border-radius: 8px; background: var(--card-bg, #fff);">
			<div style="display: flex; align-items: center; margin-bottom: 10px;">
				<span style="font-weight: 600; font-size: 13px; color: var(--heading-color, #333);">
					${__('AE Requirements Checklist')}
				</span>
				<span class="badge" style="margin-left: 8px; font-size: 11px; background: var(--bg-light-gray, #f4f5f6); color: var(--text-muted, #8d99a6);">
					${checklist.filter(i => i.completed).length}/${checklist.filter(i => i.checked).length} ${__('completed')}
				</span>
			</div>
			<table class="table table-bordered" style="margin-bottom: 0; font-size: 13px;">
				<thead>
					<tr style="background: var(--bg-light-gray, #f8f9fa);">
						<th style="width: 45%; padding: 8px 12px;">${__('Requirement')}</th>
						<th style="width: 27%; text-align: center; padding: 8px 12px;">${__('Needed (SE)')}</th>
						<th style="width: 28%; text-align: center; padding: 8px 12px;">${__('Done (AE)')}</th>
					</tr>
				</thead>
				<tbody>`;

	checklist.forEach(function (item, idx) {
		const checked_attr = item.checked ? 'checked' : '';
		const completed_attr = item.completed ? 'checked' : '';
		const se_disabled = (is_read_only || !is_se) ? 'disabled' : '';
		const ae_disabled = (is_read_only || !is_ae || !item.checked) ? 'disabled' : '';

		const row_bg = item.completed ? 'background: var(--bg-green, #edf7ed);'
			: item.checked ? 'background: var(--bg-yellow, #fff9e6);'
			: '';

		html += `
				<tr style="${row_bg}">
					<td style="padding: 8px 12px; vertical-align: middle;">
						${item.completed
							? '<span class="indicator-pill green" style="font-size: 11px; margin-right: 4px;">✓</span>'
							: item.checked
								? '<span class="indicator-pill orange" style="font-size: 11px; margin-right: 4px;">●</span>'
								: '<span class="indicator-pill gray" style="font-size: 11px; margin-right: 4px;">○</span>'
						}
						${frappe.utils.escape_html(item.label)}
					</td>
					<td style="text-align: center; vertical-align: middle;">
						<input type="checkbox" class="ae-checklist-checked"
							data-idx="${idx}" ${checked_attr} ${se_disabled}
							style="width: 16px; height: 16px; cursor: ${se_disabled ? 'not-allowed' : 'pointer'};" />
					</td>
					<td style="text-align: center; vertical-align: middle;">
						<input type="checkbox" class="ae-checklist-completed"
							data-idx="${idx}" ${completed_attr} ${ae_disabled}
							style="width: 16px; height: 16px; cursor: ${ae_disabled ? 'not-allowed' : 'pointer'};" />
					</td>
				</tr>`;
	});

	html += `
				</tbody>
			</table>
		</div>`;

	wrapper_field.$wrapper.append(html);

	// Bind SE 'checked' toggle
	wrapper_field.$wrapper.find('.ae-checklist-checked').on('change', function () {
		const idx = $(this).data('idx');
		const is_checked = $(this).is(':checked');
		checklist[idx].checked = is_checked;
		// If unchecking, also uncheck completed
		if (!is_checked) {
			checklist[idx].completed = false;
		}
		save_ae_checklist(frm, checklist);
	});

	// Bind AE 'completed' toggle
	wrapper_field.$wrapper.find('.ae-checklist-completed').on('change', function () {
		const idx = $(this).data('idx');
		checklist[idx].completed = $(this).is(':checked');
		save_ae_checklist(frm, checklist);
	});
}

function save_ae_checklist(frm, checklist) {
	const json_str = JSON.stringify(checklist);
	frm.set_value('ae_requirements', json_str).then(function () {
		frm.dirty();
		frm.save().then(function () {
			// Re-render after save to update visual state
			render_ae_checklist(frm);
		});
	});
}

function validate_ae_checklist_complete(frm) {
	const checklist = get_ae_checklist(frm);
	// All items that are 'checked' (needed) must also be 'completed'
	const needed_items = checklist.filter(function (item) { return item.checked; });
	if (needed_items.length === 0) {
		// No items flagged as needed — pass validation
		return true;
	}
	const all_completed = needed_items.every(function (item) { return item.completed; });
	return all_completed;
}
