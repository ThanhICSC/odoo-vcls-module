# -*- coding: utf-8 -*-

from odoo import models, fields, api


class PurchaseOrderLine(models.Model):

    _inherit = 'purchase.order.line'

    is_rebilled = fields.Boolean('Is rebilled', default=True)
