# -*- coding: utf-8 -*-
from odoo import models, fields, tools, api, _
from odoo.exceptions import UserError

import requests
import json
import logging

_logger = logging.getLogger(__name__)

URL_POWER_AUTOMATE = "https://prod-29.westeurope.logic.azure.com:443/workflows/9f6737616b7047498a61a053cd883fc2/triggers/manual/paths/invoke?api-version=2016-06-01&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=W5bnOEb4gMnP_E9_VnzK7c8AuYb2zGovg5BHwIoi-U8"

class SaleOrder(models.Model):

    _inherit = 'sale.order'
    parent_project_id = fields.Many2one(
        'project.project', compute='_get_parent_project_id'
    )
    family_task_count = fields.Integer(
        'Family Task Count', compute='_get_family_task_count'
    )

    forecasted_amount = fields.Monetary(
        compute = "_compute_forecasted_amount",
        store = True,
        default = 0.0,
    )

    sp_folder = fields.Char('Sharepoint Folder')

    @api.multi
    def _get_parent_project_id(self):
        for project in self:
            project.parent_project_id = project.parent_id.project_id if self.parent_id else self.project_id

    @api.multi
    def _get_family_task_count(self):
        for project in self:
            parent_project, child_projects = self._get_family_projects()
            family_projects = (parent_project | child_projects)
            project.family_task_count = len(family_projects.mapped('tasks'))


    @api.multi
    def quotation_program_print(self):
        """ Print all quotation related to the program
        """
        programs = self.mapped('program_id')
        quot = self.search([('program_id', 'in', programs.ids), ('state', '=', 'draft')])
        return self.env.ref('sale.action_report_saleorder').report_action(quot)

    @api.multi
    def action_view_project_ids(self):
        self.ensure_one()
        parent_project = self.parent_id.project_id
        if parent_project and self.env.user.has_group('project.group_project_manager'):
            action = parent_project.action_view_timesheet_plan()
            return action
        return super(SaleOrder, self).action_view_project_ids()

    @api.multi
    def action_view_family_parent_project(self):
        self.ensure_one()
        parent_project_id = self.parent_id.project_id if self.parent_id else self.project_id
        if not parent_project_id:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': _('Project'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'project.project',
            'res_id': parent_project_id.id,
            'view_id': self.env.ref('vcls-project.vcls_specific_project_form').id,
            'context': self.env.context,
        }

    @api.multi
    def action_view_family_parent_tasks(self):
        self.ensure_one()
        parent_project, child_projects = self._get_family_projects()
        return {
            'name': _('Tasks'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'kanban,tree,form,calendar,pivot,graph,activity',
            'res_model': 'project.task',
            'domain': [('project_id', 'in', (parent_project | child_projects).ids)],
            'context': {
                'search_default_project_id': self.project_id.id,
            },
        }
    
    @api.multi
    @api.depends('order_line','order_line.forecasted_amount')
    def _compute_forecasted_amount(self):
        """
        This methods sums the total of forecast potential revenues.
        Triggered by the forecast write/create methods
        """
        for so in self:
            so.forecasted_amount = sum(so.order_line.mapped('forecasted_amount'))
            # _logger.info("FORECASTED AMOUNT {}".format(so.forecasted_amount))
    
    @api.multi
    def _action_confirm(self):
        self.action_sync()
        return super(SaleOrder, self)._action_confirm()

    @api.multi
    def write(self, values):
        if values.get('active', None) is False:
            project_ids = self.mapped('project_id')
            project_ids.write({'active': False})
        return super(SaleOrder, self).write(values)

    def action_sync(self):
        super(SaleOrder, self).action_sync()
        for order in self.filtered(lambda p: p.project_id):
            project_id = order.project_id
            if project_id.scope_of_work:
                order.scope_of_work = project_id.scope_of_work
            if project_id.company_id:
                order.company_id = project_id.company_id
                order.order_line.write({'company_id': project_id.company_id.id})
            tasks_values = {}
            if order.expected_start_date or order.expected_end_date:
                tasks = project_id.tasks | project_id.tasks.mapped('child_ids')
                if order.expected_start_date:
                    tasks_values.update({'date_start': order.expected_start_date})
                if order.expected_end_date:
                    tasks_values.update({'date_end': order.expected_end_date})
                if tasks_values:
                    tasks.write(tasks_values)
            project_id.name = order.name
            for task_id in project_id.tasks:
                task_id.name = task_id.sale_line_id.name

    @api.multi
    def create_sp_folder(self):
        self.ensure_one()
        """
        As long as the migration in the new Sharepoint Online is not complete, 
        the client Name should start with AAA (to not interfer in the other folders)
        """
        if self.internal_ref and self.partner_id.altname:
            client_name = "AAA-TEST-" + self.partner_id.altname
            project_name = str(self.internal_ref.split('-', 1)[1].split('|', 1)[0])
            header_to_send = {
                "Content-Type": "application/json",
                "Referrer": "https://vcls.odoo.com/create-sp-folder"
            }
            data_to_send = {
                "client": client_name,
                "project": project_name,
            }
            response = requests.post(URL_POWER_AUTOMATE, data=json.dumps(data_to_send), headers=header_to_send)

            if response.status_code in [200, 202]:
                message = "Sucess"
                data_back = response.json()
                self.sp_folder = data_back['clientSiteUrl']
                message_log = ("The Sharepoint Folder has been created, here is the link: {}".format(self.sp_folder))
                self.message_post(body=message_log)
                _logger.info("Call API: Power Automate message: {}, whith the client: {} and for the project: {}, and the Sharepoint link is: {}".format(message, client_name, project_name, self.sp_folder))
                return

            if response.status_code in [400, 403]:
                _logger.warning("Call API: Power Automate message: {}, whith the client: {} and for the project: {}".format(response.status_code, client_name, project_name))
                raise UserError(_("Sharepoint didn't respond, Please try again"))

            if response.status_code in [500, 501, 503]:
                message = "Server error"
            else:
                message = response.status_code

            _logger.warning("Call API: Power Automate message: {}, whith the client: {} and for the project: {}".format(message, client_name, project_name))
            raise UserError(_("Sharepoint didn't respond, Please try again"))
        else:
           raise UserError(_("Please make sure that the project has an internal reference and an Alt name"))
