# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
import lxml
import base64
from itertools import groupby
from datetime import date
from dateutil.relativedelta import relativedelta

from odoo.tools import email_re, email_split, email_escape_char, float_is_zero, float_compare, \
    pycompat, date_utils

from odoo.exceptions import AccessError, UserError, RedirectWarning, ValidationError, Warning
from collections import OrderedDict
from odoo.tools import OrderedSet

import logging
_logger = logging.getLogger(__name__)

DRAFTINVOICE = '_DraftInvoice'
ACTIVITYREPORT = '_ActivityReport'


class Invoice(models.Model):
    _inherit = 'account.invoice'

    #def _get_default_po_id(self):
        #return self.env['sale.order'].search([('invoice_ids', 'in', [self.id])], limit=1).po_id

    po_id = fields.Many2one('invoicing.po',
                            #default = _get_default_po_id,
                            string ='Client PO ref.')

    user_id = fields.Many2one(
        'res.users',
        string='Invoicing Administrator',
        related='commercial_partner_id.invoice_admin_id',
        )

    invoice_sending_date = fields.Datetime()
    
    """parent_quotation_timesheet_limite_date = fields.Date(
        string='Parent Timesheet Limit Date',
        compute='compute_parent_quotation_timesheet_limite_date'
    )"""

    temp_name = fields.Char(
        compute = 'compute_temp_name',
    )

    period_start = fields.Date()
    lc_laius = fields.Text()
    scope_of_work = fields.Text()

    vcls_due_date = fields.Date(string='Custom Due Date', compute='_compute_vcls_due_date')
    origin_sale_orders = fields.Char(compute='compute_origin_sale_orders',string='Origin')

    ready_for_approval = fields.Boolean(default=False)

    invoice_template = fields.Many2one('ir.actions.report', domain=[('model', '=', 'account.invoice'),('name', 'ilike', 'invoice')])
    activity_report_template = fields.Many2one('ir.actions.report', domain=[('model', '=', 'account.invoice'),('name', 'ilike', 'activity')])

    report_count = fields.Integer(
        compute='_compute_attachment_count',
        default = 0,
    )

    draft_count = fields.Integer(
        compute='_compute_attachment_count',
        default = 0,
    )

    communication_rate = fields.Float()

    bank_account_id = fields.Many2one(
        'res.partner.bank', string='Bank Account',
        help='Company Bank Account Number to which the invoice will be paid.',
    )

    @api.model
    def default_get(self, fields_list):
        result = super(Invoice, self).default_get(fields_list)
        company = self.env['res.company']._company_default_get()
        bank_accounts = company.bank_ids.filtered(lambda b: not b.currency_id or b.currency_id == company.currency_id)
        if bank_accounts:
            result['bank_account_id'] = bank_accounts[0].id
        return result

    @api.onchange('company_id', 'currency_id')
    def _on_change_currency(self):
        if not self.bank_account_id:
            bank_accounts = self.company_id.bank_ids.filtered(
                lambda b: not b.currency_id or b.currency_id == self.currency_id
            )
            self.bank_account_id = bank_accounts and bank_accounts[0] or False

    @api.depends('timesheet_limit_date', 'period_start')
    def compute_temp_name(self):
        for invoice in self:
            project_string = ""
            for project in invoice.project_ids:
                if not project.parent_id and project.sale_order_id:
                    project_string += project.sale_order_id.internal_ref + ' | ' 
            #project_string = invoice.project_ids.filtered(lambda p: not p.parent_id).mapped('sale_order_id.internal_ref')
            invoice.temp_name = "{} from {} to {}".format(project_string,invoice.period_start,invoice.timesheet_limit_date)

    @api.multi
    def _compute_attachment_count(self):
        for invoice in self:
            drafts = self.env['ir.attachment'].search([('res_id', '=', self.id),('name', 'like', DRAFTINVOICE)])
            if drafts:
                invoice.draft_count = len(drafts)
            else:
                invoice.draft_count = 0

            reports = self.env['ir.attachment'].search([('res_id', '=', self.id),('name', 'like', ACTIVITYREPORT)])
            if reports:
                invoice.report_count = len(reports)
            else:
                invoice.report_count = 0

    
    @api.multi
    def _get_source_data(self,vals):
        self.ensure_one()
        #we initiate variables
        laius = ""
        sow = ""
        timesheet_limit_date = False
        period_start = False
        delta = 0
        communication_rate = 0.0
        invoice_template = None #self.env['ir.actions.report']
        activity_report_template = None #self.env['ir.actions.report']
        po_id = None #self.env['invoicing.po']

        #loop in projects
        for project in self.project_ids:

            #get last  as laius if non exists
            if not vals.get('lc_laius',self.lc_laius):
                if project.summary_ids:
                    #_logger.info("SUMMARIES {}".format(project.summary_ids.mapped('project_id')))
                    last_summary = project.summary_ids.sorted(lambda s: s.create_date, reverse=True)[0]
                    laius += "Project Status on {}:\n{}\n\n".format(last_summary.create_date.date(),self.html_to_string(last_summary.external_summary))
            else:
                laius = vals.get('lc_laius',self.lc_laius)

            #get sow if non exists
            if not vals.get('scope_of_work',self.scope_of_work):
                sow += "{}\n".format(self.html_to_string(project.scope_of_work))
            else:
                sow = vals.get('scope_of_work',self.scope_of_work)

            #sales.order info
            so = project.sale_order_id
            #Timesheet limit date
            
            if not vals.get('timesheet_limit_date',self.timesheet_limit_date):
                if so.timesheet_limit_date:
                    if timesheet_limit_date:
                        if so.timesheet_limit_date < timesheet_limit_date:
                            timesheet_limit_date = so.timesheet_limit_date
                    else:
                        timesheet_limit_date = so.timesheet_limit_date
            else:
                timesheet_limit_date = vals.get('timesheet_limit_date',self.timesheet_limit_date)
            
            if not vals.get('period_start',self.period_start) and timesheet_limit_date:
                if so.invoicing_frequency == 'month' and delta < 1:
                    delta = 1
                if so.invoicing_frequency == 'trimester' and delta < 3:
                    delta = 3
                period_start = timesheet_limit_date + relativedelta(months=-1*delta,days=1)
            else:
                period_start = vals.get('period_start',self.period_start)

            #_logger.info("SO DATA {} rate {}".format(so.name,so.communication_rate))
            #PO id
            if not vals.get('po_id',self.po_id):
                if not po_id and so.po_id:
                    po_id = so.po_id.id
            else:
                po_id = vals.get('po_id',self.po_id.id)

            #Invoice Template
            if not vals.get('invoice_template',self.invoice_template):
                if not invoice_template and so.invoice_template:
                    invoice_template = so.invoice_template.id
            else:
                invoice_template = vals.get('invoice_template',self.invoice_template.id)

            #Activity Report template
            if not vals.get('activity_report_template',self.activity_report_template):
                if not activity_report_template and so.activity_report_template:
                    activity_report_template = so.activity_report_template.id
            else:
                activity_report_template = vals.get('activity_report_template',self.activity_report_template.id)
            
            #Communication  Rate
            if not vals.get('communication_rate',self.communication_rate):
                if communication_rate < float(so.communication_rate):
                    communication_rate = float(so.communication_rate) 
            else:
                communication_rate = vals.get('communication_rate',self.communication_rate) 

        vals.update({   'lc_laius': laius,
                        'scope_of_work': sow,
                        'timesheet_limit_date': timesheet_limit_date,
                        'period_start': period_start,
                        'communication_rate': communication_rate,
                        })
        
        if po_id:
            vals.update({'po_id': po_id})
        if invoice_template:
            vals.update({'invoice_template': invoice_template})
        if activity_report_template:
            vals.update({'activity_report_template': activity_report_template})

        return vals


    @api.multi
    def _get_activity_report_data(self, detailed=True):
        self.ensure_one()
        task_rate_matrix_data = {}
        project_rate_matrix_data = {}
        time_category_rate_matrix_data = {}
        product_obj = self.env['product.product']
        rate_product_ids = product_obj
        projects_row_data = OrderedDict()
        for timesheet_id in self.timesheet_ids:
            # check if so line is invoiced
            if not timesheet_id.so_line.qty_invoiced:
                continue
            project_id = timesheet_id.project_id
            task_id = timesheet_id.task_id
            parent_task_id = task_id.parent_id or task_id
            rate_product_id = timesheet_id.so_line.product_id
            rate_product_ids |= rate_product_id
            time_category_id = timesheet_id.time_category_id
            unit_amount = timesheet_id.unit_amount
            # project matrix data
            project_rate_matrix_key = (project_id, rate_product_id)
            project_rate_matrix_data.setdefault(project_rate_matrix_key, 0.)
            project_rate_matrix_data[project_rate_matrix_key] += unit_amount
            tasks_row_data = projects_row_data.setdefault(project_id, OrderedDict())
            # task matrix data
            task_rate_matrix_key = (project_id, parent_task_id, rate_product_id)
            task_rate_matrix_data.setdefault(task_rate_matrix_key, 0.)
            task_rate_matrix_data[task_rate_matrix_key] += unit_amount
            time_category_row_data = tasks_row_data.setdefault(parent_task_id, OrderedDict())
            # time category matrix data
            if detailed:
                time_category_matrix_key = (project_id, parent_task_id, time_category_id, rate_product_id)
                time_category_rate_matrix_data.setdefault(time_category_matrix_key, 0.)
                time_category_rate_matrix_data[time_category_matrix_key] += unit_amount
                time_category_row_data.setdefault(time_category_id, None)
        # reorder rate_product_ids according to the richer one
        rate_product_ids = product_obj.browse(OrderedSet([
            couple[1].id for couple in
            sorted(
                [(project_id, rate_product_id)
                    for project_id in projects_row_data.keys()
                    for rate_product_id in rate_product_ids],
                key=lambda key: project_rate_matrix_data[key],
                reverse=True
            )
        ]))
        return {
            'project_rate_matrix_data': project_rate_matrix_data,
            'task_rate_matrix_data': task_rate_matrix_data,
            'time_category_rate_matrix_data': time_category_rate_matrix_data,
            'rate_product_ids': rate_product_ids,
            'projects_row_data': projects_row_data,
        }

    @api.multi
    def _get_aggregated_invoice_report_data(self):
        rate_data, rate_subtotal = self._get_aggregated_invoice_report_rate_data()
        fixed_price_data = self._get_aggregated_invoice_report_fixed_price()
        expenses_and_communication_data = self._get_invoice_report_expenses_and_communication()
        return {
            'rate_data': rate_data,
            'fixed_price_data': fixed_price_data,
            'expenses_and_communication_data': expenses_and_communication_data,
            'rate_subtotal': rate_subtotal,
            '_t': _,
        }

    @api.multi
    def _get_detailed_invoice_report_data(self):
        rate_data, rate_subtotal = self._get_detailed_invoice_report_rate_data()
        fixed_price_data = self._get_detailed_invoice_report_fixed_price()
        expenses_and_communication_data = self._get_invoice_report_expenses_and_communication()
        return {
            'rate_data': rate_data,
            'fixed_price_data': fixed_price_data,
            'expenses_and_communication_data': expenses_and_communication_data,
            'rate_subtotal': rate_subtotal,
            '_t': _,
        }

    @api.multi
    def _get_aggregated_invoice_report_rate_data(self):
        """
        :param self:
        :return: ordered dictionary with the following structure
        {
            section_line_id: {
                rate_line_record : {
                    'qty': qty,
                    'price': price,
                    'currency_id': currency,
                }
            }
        }
        """
        self.ensure_one()
        data = OrderedDict()
        total_not_taxed = 0.
        for timesheet_id in self.timesheet_ids.filtered(lambda t: t.so_line.qty_invoiced)\
                .sorted(lambda t: t.so_line.price_unit, reverse=True):
            rate_sale_line_id = timesheet_id.so_line
            service_sale_line_id = timesheet_id.task_id.sale_line_id
            service_section_line_id = service_sale_line_id.section_line_id
            rates_dict = data.setdefault(service_section_line_id, OrderedDict())
            values = rates_dict.setdefault(
                rate_sale_line_id, {
                    'qty': 0.,
                    'price': rate_sale_line_id.price_unit,
                    'currency_id': rate_sale_line_id.currency_id,
                    'uom_id': rate_sale_line_id.product_uom,
                })
            timesheet_uom_id = timesheet_id.product_uom_id
            qty = timesheet_uom_id._compute_quantity(
                timesheet_id.unit_amount_rounded,
                rate_sale_line_id.product_uom
            )
            values['qty'] += qty
            total_not_taxed += qty * values['price']
        # assert abs(total_not_taxed - self.amount_untaxed) < 0.001, _('Something went wrong')
        return data, total_not_taxed

    @api.multi
    def _get_detailed_invoice_report_rate_data(self):
        """
        :param self:
        :return: ordered dictionary with the following structure
        {
            task1_record: {
                rate_line_record : {
                    'qty': qty,
                    'price': price,
                    'currency_id': currency,
                }
            }
        }
        """
        self.ensure_one()
        data = OrderedDict()
        total_not_taxed = 0.
        for timesheet_id in self.timesheet_ids.filtered(lambda t: t.so_line.qty_invoiced)\
                .sorted(lambda t: t.so_line.price_unit, reverse=True):
            rate_sale_line_id = timesheet_id.so_line
            task_id = timesheet_id.task_id
            rates_dict = data.setdefault(task_id, OrderedDict())
            values = rates_dict.setdefault(
                rate_sale_line_id, {
                    'qty': 0.,
                    'price': rate_sale_line_id.price_unit,
                    'currency_id': rate_sale_line_id.currency_id,
                    'uom_id': rate_sale_line_id.product_uom,
                })
            timesheet_uom_id = timesheet_id.product_uom_id
            qty = timesheet_uom_id._compute_quantity(
                timesheet_id.unit_amount_rounded,
                rate_sale_line_id.product_uom
            )
            values['qty'] += qty
            total_not_taxed += qty * values['price']
        # assert abs(total_not_taxed - self.amount_untaxed) < 0.001, _('Something went wrong')
        return data, total_not_taxed

    @api.multi
    def _get_detailed_invoice_report_fixed_price(self):
        """
        :param self:
        :return: ordered dictionary with the following structure
        {
            service_line_record : {
                'subtotal': subtotal,
                'currency_id': currency,
            }
        }
        """
        self.ensure_one()
        fixed_price_data = OrderedDict()
        for line in self.invoice_line_ids:
            if line.product_id.vcls_type != 'vcls_service':
                continue
            fixed_price_data.setdefault(line, {
                'subtotal': line.price_subtotal,
                'currency_id': line.currency_id,
            })
        for key in list(fixed_price_data):
            value = fixed_price_data[key]
            if not value['subtotal']:
                del fixed_price_data[key]
        return fixed_price_data

    @api.multi
    def _get_invoice_report_expenses_and_communication(self):
        """
        :param self:
        :return: ordered dictionary with the following structure
        {
            product_category_record : {
                'subtotal': subtotal,
                'currency_id': currency,
            }
        }
        """
        self.ensure_one()
        data = OrderedDict()
        for line in self.invoice_line_ids:
            if line.product_id.vcls_type not in ('expense', 'communication'):
                continue
            value = data.setdefault(line.product_id.categ_id, {
                'subtotal': 0.,
                'currency_id': line.currency_id,
            })
            value['subtotal'] += line.price_subtotal
        for key in list(data):
            value = data[key]
            if not value['subtotal']:
                del data[key]
        return data

    @api.multi
    def _get_aggregated_invoice_report_fixed_price(self):
        """
        :param self:
        :return: ordered dictionary with the following structure
        {
            service_section_line_record : {
                'subtotal': subtotal,
                'currency_id': currency,
            }
        }
        """
        self.ensure_one()
        fixed_price_data = OrderedDict()
        for line in self.invoice_line_ids:
            if line.product_id.vcls_type != 'vcls_service':
                continue
            section_line_id = line.section_line_id
            value = fixed_price_data.setdefault(section_line_id, {
                'subtotal': 0.,
                'currency_id': line.currency_id,
            })
            value['subtotal'] += line.price_subtotal
        for key in list(fixed_price_data):
            value = fixed_price_data[key]
            if not value['subtotal']:
                del fixed_price_data[key]
        return fixed_price_data

    def get_communication_amount(self):
        total_amount = 0
        lines = self.invoice_line_ids
        _logger.info("Invoice Lines {}".format(len(lines)))
        for line in lines:
            product = line.product_id
            _logger.info("Product {} elligible {}".format(product.name, product.communication_elligible))
            if product:
                if product.id != self.env.ref('vcls-invoicing.product_communication_rate').id:
                    if product.communication_elligible:
                        total_amount += line.price_subtotal
                        _logger.info("Communication Elligible {}".format(product.name))
                else:
                    # We suppress the communication rate line if already existingin order to replace and recompute it
                    line.unlink()
            else:
                total_amount += line.price_subtotal
        return total_amount

    @api.multi
    def action_ready_for_approval(self):

        if self.filtered(lambda inv: not inv.partner_id):
            raise UserError(_("The field Vendor is required, please complete it to validate the Vendor Bill."))
        if self.filtered(lambda inv: float_compare(inv.amount_total, 0.0, precision_rounding=inv.currency_id.rounding) == -1):
            raise UserError(_("You cannot validate an invoice with a negative total amount. You should create a credit note instead."))
        if self.filtered(lambda inv: not inv.account_id):
            raise UserError(_('No account was found to create the invoice, be sure you have installed a chart of account.'))

        for invoice in self:
            invoice.write({'ready_for_approval': True})
            #and we send a scheduled action to the AM and the LC's
            activity_type = self.env['mail.activity.type'].search([('name','=','Invoice Review')],limit=1)
            if activity_type:
                users_to_notify = self.env['res.users']
                users_to_notify |= invoice.commercial_partner_id.user_id
                #we also notify the LM of the invoice admin
                connected_employee = self.env['hr.employee'].search([('user_id','=',invoice.user_id.id)],limit=1)
                if connected_employee:
                    users_to_notify |= connected_employee.parent_id.user_id
                #we add the LC's
                users_to_notify |= invoice.project_ids.mapped('user_id')
                #users_to_notify 
                for user in users_to_notify:
                    self.env['mail.activity'].create({
                    'res_id': invoice.id,
                    'res_model_id': self.env.ref('account.model_account_invoice').id,
                    'activity_type_id': activity_type.id,
                    'user_id': user.id,
                    'summary': _('Please review the invoice PDF for {}.').format(
                        invoice.name),
                    })

    @api.model
    def create(self, vals):
        ret = super(Invoice, self).create(vals)
        ret._onchange_partner_id()
        #_logger.info("INVOICE CREATED {} vals {} create {}".format(ret.temp_name, vals, ret.partner_id))
        return ret

    @api.multi
    def write(self, vals):
        ret = False
        
        for inv in self:

            if vals.get('sent'):
                vals.update({'invoice_sending_date': fields.Datetime.now()})

            if not self.env.context.get('source_data'):
                vals = inv.with_context(source_data=True)._get_source_data(vals)

            #call parent
            ret = super(Invoice, inv).write(vals)

            #release timesheets if any
            if inv.state == 'cancel':
                if inv.timesheet_ids:
                    for timesheet in inv.timesheet_ids:
                        timesheet.stage_id = 'invoiceable'
            
            #communication rate
            #_logger.info("COM RATE {} {}".format(inv.communication_rate,self.env.context.get('communication_rate')))
            if inv.communication_rate > 0 and not self.env.context.get('communication_rate'):
                try:
                    total_amount = inv.get_communication_amount()
                except:
                    total_amount = False
                    #_logger.info("COM RATE ERROR")
                if total_amount:
                    line = self.env['account.invoice.line'].new()
                    line.invoice_id = inv.id
                    line.product_id = self.env.ref('vcls-invoicing.product_communication_rate').id
                    line._onchange_product_id()
                    line.price_unit = total_amount * inv.communication_rate
                    line.name = "Communication ({}%)".format(100*inv.communication_rate)
                    #_logger.info("COM RATE PRICE {}".format(line.price_unit))
                    line.quantity = 1
                    super(Invoice, inv.with_context(communication_rate=True)).write({
                        'invoice_line_ids': [(4, line.id)]
                    })
        return ret

    """@api.depends('invoice_line_ids')
    def compute_parent_quotation_timesheet_limite_date(self):
        for invoice in self:
            so_with_timesheet_limit_date = invoice._get_parents_quotations().filtered(
                lambda so: so.timesheet_limit_date)
            if so_with_timesheet_limit_date:
                invoice.parent_quotation_timesheet_limite_date = so_with_timesheet_limit_date[0].timesheet_limit_date"""

    def _get_parents_quotations(self):
        return self.mapped('invoice_line_ids.sale_line_ids.order_id')

    @api.depends('payment_term_id', 'invoice_sending_date')
    def _compute_vcls_due_date(self):
        for rec in self:
            if rec.payment_term_id and rec.invoice_sending_date:
                pterm = rec.payment_term_id
                pterm_list = \
                    pterm.with_context(currency_id=rec.company_id.currency_id.id).compute(value=1,
                                                                                          date_ref=date.today())[0]
                rec.vcls_due_date = max(line[0] for line in pterm_list)

    @api.depends('invoice_line_ids')
    def compute_origin_sale_orders(self):
        for rec in self:
            sale_orders = rec._get_parents_quotations()
            rec.origin_sale_orders = ','.join(sale_orders.mapped('name'))

    @api.multi
    def unlink(self):
        for invoice in self:
            if invoice.timesheet_ids:
                for timesheet in invoice.timesheet_ids:
                    timesheet.stage_id = 'invoiceable'
        
            ret = super(Invoice, invoice).unlink()

        return ret

    @api.multi
    def html_to_string(self, html_format):
        self.ensure_one()
        return lxml.html.document_fromstring(html_format).text_content()

    def parent_quotation_informations(self):

        if not self.origin:
            return []
        names = self.origin.split(', ')
        customer_precedent_invoice = ""
        quotation = self.env['sale.order'].search([('name', 'in', names)], limit=1)
        if not quotation:
            return []
        parent_order = quotation.parent_id or quotation
        while parent_order.parent_id:
            parent_order = parent_order.parent_id



        return [
            ('name', parent_order.name),
            ('scope_work', self.html_to_string(parent_order.scope_of_work) or ''),
            ('po_id', parent_order.po_id.name or ''),
            ('From', self.timesheet_limit_date and self.timesheet_limit_date.strftime("%d/%m/%Y") or ''),
            ('To', self.timesheet_limit_date and self.timesheet_limit_date.strftime("%d/%m/%Y") or '')
        ]

    def get_analytic_accounts_lines(self):
        so_names = self.origin.split(', ')
        line_ids = self.env['sale.order'].sudo().search([('name', 'in', so_names)]).mapped(
            'analytic_account_id.line_ids')
        categs = {}
        supplier_expenses = self.invoice_line_ids.filtered(lambda il: il.product_id.purchase_ok)
        communications_expenses = self.invoice_line_ids.filtered(lambda il: il.product_id.id != self.env.ref(
            'vcls-invoicing.product_communication_rate').id and il.product_id.communication_elligible)
        if supplier_expenses:
            categs.update({'supplier_expenses': supplier_expenses})
        if communications_expenses:
            categs.update({'communications_expenses': communications_expenses})
        for categ_id, same_product_categ in groupby(line_ids, key=lambda al: al.product_id.categ_id):
            same_product_cat = list(same_product_categ)
            categ_name = categ_id.name
            categ_amount = sum(p.amount for p in same_product_cat)
            categs.update({categ_name: categ_amount})
        return categs

    def deliverable_grouped_lines(self):
        sale_line_ids = self.invoice_line_ids.mapped('sale_line_ids').filtered(
            lambda sol: not sol.product_id.recurring_invoice)
        deliverable_groups = {}
        deliverable_lines = []
        for deliverable_id, same_deliverable in groupby(sale_line_ids, key=lambda sol: sol.product_id.deliverable_id):
            deliverable_name = deliverable_id.name
            if deliverable_name:
                deliverable_lines += [line for line in same_deliverable]
                deliverable_groups[deliverable_name] = deliverable_lines
        return deliverable_groups

    @api.multi
    def _create_activity_attachment(self, report_template, report_name):
        self.ensure_one()
        attachment_obj = self.env['ir.attachment']
        data = report_template.render_qweb_pdf(self.ids)
        name = self._get_invoice_report_filename(report_name)
        values = {
            'name': name,
            'datas': base64.b64encode(data[0]),
            'datas_fname': name,
            'res_model': self._name,
            'res_id': self.id,
            'mimetype': 'application/pdf'
        }
        attachment_id = attachment_obj.create(values)
        if self._context.get('_get_attachment_id'):
            return attachment_id
        return {
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': '/web/content/%s/%s?download=true' % (attachment_id.id, name)
        }

    @api.multi
    def _get_invoice_report_filename(self, report_name):
        self.ensure_one()
        project_string = ''
        for project in self.project_ids:
            project_string += project.name.split('|')[0]

        count_attachments = self.env['ir.attachment'].search_count([('res_model', '=', self._name),
                                                                    ('res_id', '=', self.id),
                                                                    ('name', 'like', report_name)]) + 1
        return (self.timesheet_limit_date and self.timesheet_limit_date.strftime('%Y-%m-%d') or '') \
            + project_string + report_name + '_V' + str(count_attachments)

    @api.multi
    def generate_report(self, report_template, report_name, message):
        self.ensure_one()
        if not self.timesheet_ids and report_name==ACTIVITYREPORT:
            raise UserError(_('There is no timesheet associated with the invoice: %s') % self.name)
        if not report_template:
            raise ValidationError(_(message))
        # create attachment
        attachment =  self._create_activity_attachment(report_template, report_name)
        self._compute_attachment_count()
        return attachment

    @api.multi
    def action_generate_draft_invoice(self):
        self.ensure_one()
        return self.generate_report(self.invoice_template, DRAFTINVOICE, _("You need to select an invoice template"))

    @api.multi
    def action_generate_activity_report(self):
        self.ensure_one()
        return self.generate_report(self.activity_report_template,
                                    ACTIVITYREPORT, _("You need to select an activity report template"))

    @api.multi
    def action_invoice_sent(self):
        """
        Override of action_invoice_sent to attach the invoice_template
        """
        if not self.invoice_template:
            raise ValidationError(_('Please set an Invoice Template before'))
        res = super(Invoice, self).action_invoice_sent()
        attachment_id = self.with_context(_get_attachment_id=True)._create_activity_attachment(self.invoice_template,
                                                                                               self._get_invoice_report_filename(DRAFTINVOICE))
        res['context'].update({'default_attachment_ids': attachment_id.ids})
        return res

    @api.multi
    def action_activity_report_attachments(self):
        action = self.env.ref('vcls-invoicing.action_invoice_attachment').read()[0]
        action['domain'] = [('res_id', '=', self.id),('name', 'like', ACTIVITYREPORT)]
        return action

    @api.multi
    def action_generate_draft_invoice_attachments(self):
        action = self.env.ref('vcls-invoicing.action_invoice_attachment').read()[0]
        action['domain'] = [('res_id', '=', self.id), ('name', 'like', DRAFTINVOICE)]
        return action
