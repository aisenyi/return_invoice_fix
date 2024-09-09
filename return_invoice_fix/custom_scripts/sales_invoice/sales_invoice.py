import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from frappe import _
from frappe.utils import flt

class CustomSalesInvoice(SalesInvoice):
	def validate_pos(self):	
		# Replace validation to always pass
		pass

	def calculate_taxes_and_totals(self):
		from return_invoice_fix.custom_scripts.controllers.taxes_and_totals import custom_calculate_taxes_and_totals
		custom_calculate_taxes_and_totals(self)

		if self.doctype in (
			"Sales Order",
			"Delivery Note",
			"Sales Invoice",
			"POS Invoice",
		):
			self.calculate_commission()
			self.calculate_contribution()