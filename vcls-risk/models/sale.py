# -*- coding: utf-8 -*-

from odoo import models, fields, api

from odoo.exceptions import UserError, ValidationError

class SaleOrder(models.Model):
    _inherit = 'sale.order'
    risk_id = fields.Many2one('risk', string='Risk')

    def action_risk(self):
        view_ids = [self.env.ref('vcls-risk.view_risk_tree').id,
                    self.env.ref('vcls-risk.view_risk_kanban').id, 
                    self.env.ref('vcls-risk.view_risk_form').id ]
        risk_id = self.risk_id

        return {
            'name': 'All Risks',
            'view_type': 'form',
            'view_mode': 'tree,kanban,form',
            'view_ids': view_ids,
            'target': 'current',
            'res_model': 'risk',
            'type': 'ir.actions.act_window',
            'context': {'search_default_id': risk_id.id,},
        } 

class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def create_risk(self):
        so_line_price = self.price_unit
        product_prices = self.product_id.item_ids

        if self.product_id.seniority_level_id:
            for product_price in product_prices:
                if product_price.pricelist_id == self.pricelist_id and product_price.fixed_price != so_line_price:
                    risk_type = self.env['risk.type'].search([('name', '=', 'Sale Order Risk'), ('model_name', '=', 'sale.order.line')])
                    resource ='sale.order.line,' + str(self.id)
                    risk = self.env['risk'].search([('resource', '=', resource)])
                    if not risk_type:
                        risk_type = self.env['risk.type'].create({'name': 'Sale Order Risk', 'active': True, 'model_name': 'sale.order.line'})
                    if not risk:
                        risk = self.env['risk'].create({'risk_type_id': risk_type.id, 'resource': resource}).id
                        self.order_id.risk_id = risk
        
    @api.multi
    def write(self, vals):
        result = super(SaleOrderLine, self).write(vals)
        self.create_risk()
        return result

    @api.model
    def create(self, vals):
        result = super(SaleOrderLine, self).create(vals)
        result.create_risk()
        return result