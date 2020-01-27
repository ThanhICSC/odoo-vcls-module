# -*- coding: utf-8 -*-

from odoo import models, fields, api, http, _

class ProjectTaskType(models.Model):
    _inherit = 'project.task.type'

    project_type_default = fields.Selection(
        selection_add = [('marketing', 'Marketing')],
        string='Project Type for Default',
    )

class Task(models.Model):
    
    _inherit = 'project.task'

    task_type = fields.Selection(
        selection_add = [('marketing','Marketing Campaign')],
        default='gen',
        string='Task Type',
        compute='_compute_task_type',
        store=True,)
    
    @api.depends('parent_id', 'project_id.project_type')
    def _compute_task_type(self):
        for task in self:
            if task.project_id.project_type == 'dev':
                if task.parent_id:
                    task.task_type = 'dev.task'
                else:
                    task.task_type = 'dev.vers'

            if task.project_id.project_type == 'marketing':
                task.task_type = 'marketing'
   
    organizer_id = fields.Many2one(
        'res.partner',
        string='Organizer'
    )
    business_line_id = fields.Many2many(
        'product.category',
        string='Business line',
        domain='[("is_business_line", "=", True)]'
    )
    country_group_id = fields.Many2one(
        'res.country.group',
        string='Country group',
    )

    related_events_ids = fields.One2many(
        comodel_name = 'marketing.campaign',
        inverse_name = 'marketing_task_id',
    )

    related_mailing_campaigns_ids = fields.One2many(
        comodel_name = 'mail.mass_mailing.campaign',
        inverse_name = 'marketing_task_id',
    )

    attendee_ids = fields.Many2many(
        comodel_name = 'hr.employee',
        string="Attendees",
    )

    ### COSTS related fields
    currency_id = fields.Many2one(
        comodel_name = 'res.currency',
        related = 'project_id.currency_id',
    )

    travel_cost = fields.Monetary(default=0.0)
    sponsorship_cost = fields.Monetary(default=0.0)
    registration_cost = fields.Monetary(default=0.0)
    saved_cost = fields.Monetary(string = 'Savings Negociated',default=0.0)
    total_cost = fields.Monetary(
        compute='_compute_total_cost',
        store = True,
    )
    
    @api.depends('travel_cost','sponsorship_cost','registration_cost','saved_cost')
    def _compute_total_cost(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            task.total_cost = (task.travel_cost + task.sponsorship_cost + task.registration_cost - task.saved_cost)

    

    lead_count = fields.Integer(compute="_compute_lead_count")
    opp_count = fields.Integer(compute="_compute_opp_count")
    contact_count = fields.Integer(compute="_compute_contact_count")
    convertion_ratio = fields.Float(compute="_compute_convertion_ratio")
    lead_lost = fields.Integer(compute="_compute_lead_lost")
    contact_lost = fields.Integer(compute="_compute_contact_lost")

    def _compute_lead_count(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            leads = self.env['crm.lead'].search([('marketing_task_id.id','=',task.id),('type','=','lead')])
            if leads:
                task.lead_count = len(leads)
            else:
                task.lead_count = 0

    def _compute_opp_count(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            opps = self.env['crm.lead'].search([('marketing_task_id.id','=',task.id),('type','=','opportunity')])
            if opps:
                task.opp_count = len(opps)
            else:
                task.opp_count = 0
        
    def _compute_contact_count(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            partners = self.env['res.partner'].search([('marketing_task_id.id','=',task.id)])
            if partners:
                task.contact_count = len(partners)
            else:
                task.contact_count = 0

    def _compute_convertion_ratio(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            if task.opp_count > 0 or task.lead_count > 0:
                task.convertion_ratio = 100*(task.opp_count/(task.opp_count+task.lead_count))
            else:
                task.convertion_ratio = 0.0
    
    def _compute_lead_lost(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            leads = self.env['crm.lead'].search([('marketing_task_out_id.id','=',task.id),('type','=','lead')])
            if leads:
                task.lead_lost = len(leads)
            else:
                task.lead_lost = 0
        
    def _compute_contact_lost(self):
        for task in self.filtered(lambda t: t.task_type == 'marketing'):
            partners = self.env['res.partner'].search([('marketing_task_out_id.id','=',task.id)])
            if partners:
                task.contact_lost = len(partners)
            else:
                task.contact_lost = 0

