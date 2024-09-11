import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice, validate_inter_company_party
from frappe import _
from frappe.utils import flt, cint, comma_and, get_link_to_form
import erpnext
from erpnext.controllers.selling_controller import SellingController, set_default_income_account_for_item
from erpnext.controllers.stock_controller import StockController
from erpnext.controllers.accounts_controller import AccountsController
from erpnext.utilities.transaction_base import TransactionBase
from erpnext.controllers.sales_and_purchase_return import validate_return
from erpnext.accounts.deferred_revenue import validate_service_stop_date
from erpnext.stock.doctype.batch.batch import set_batch_nos
from erpnext.accounts.doctype.loyalty_program.loyalty_program import validate_loyalty_points

class CustomAccountsController(AccountsController):
	# Customization: Replace validate function from accounts controller to remove
	# the warning pop up about payment reconciliation in return invoices
	def validate(self):
		if not self.get("is_return") and not self.get("is_debit_note"):
			self.validate_qty_is_not_zero()

		if (
			self.doctype in ["Sales Invoice", "Purchase Invoice"]
			and self.get("is_return")
			and self.get("update_stock")
		):
			self.validate_zero_qty_for_return_invoices_with_stock()

		if self.get("_action") and self._action != "update_after_submit":
			self.set_missing_values(for_validate=True)

		self.ensure_supplier_is_not_blocked()

		self.validate_date_with_fiscal_year()
		self.validate_party_accounts()

		self.validate_inter_company_reference()

		self.disable_pricing_rule_on_internal_transfer()
		self.disable_tax_included_prices_for_internal_transfer()
		self.set_incoming_rate()
		self.init_internal_values()

		if self.meta.get_field("currency"):
			self.calculate_taxes_and_totals()

			if not self.meta.get_field("is_return") or not self.is_return:
				self.validate_value("base_grand_total", ">=", 0)

			validate_return(self)

		self.validate_all_documents_schedule()

		if self.meta.get_field("taxes_and_charges"):
			self.validate_enabled_taxes_and_charges()
			self.validate_tax_account_company()

		self.validate_party()
		self.validate_currency()
		self.validate_party_account_currency()
		self.validate_return_against_account()

		if self.doctype in ["Purchase Invoice", "Sales Invoice"]:
			if invalid_advances := [x for x in self.advances if not x.reference_type or not x.reference_name]:
				frappe.throw(
					_(
						"Rows: {0} in {1} section are Invalid. Reference Name should point to a valid Payment Entry or Journal Entry."
					).format(
						frappe.bold(comma_and([x.idx for x in invalid_advances])),
						frappe.bold(_("Advance Payments")),
					)
				)

			# Customization: Comment out warning pop up about payment reconciliation in return invoices
			'''if self.get("is_return") and self.get("return_against") and not self.get("is_pos"):
				if self.get("update_outstanding_for_self"):
					document_type = "Credit Note" if self.doctype == "Sales Invoice" else "Debit Note"
					frappe.msgprint(
						_(
							"We can see {0} is made against {1}. If you want {1}'s outstanding to be updated, uncheck '{2}' checkbox. <br><br> Or you can use {3} tool to reconcile against {1} later."
						).format(
							frappe.bold(document_type),
							get_link_to_form(self.doctype, self.get("return_against")),
							frappe.bold("Update Outstanding for Self"),
							get_link_to_form("Payment Reconciliation", "Payment Reconciliation"),
						)
					)'''

			pos_check_field = "is_pos" if self.doctype == "Sales Invoice" else "is_paid"
			if cint(self.allocate_advances_automatically) and not cint(self.get(pos_check_field)):
				self.set_advances()

			self.set_advance_gain_or_loss()

			if self.is_return:
				self.validate_qty()
			else:
				self.validate_deferred_start_and_end_date()

			self.validate_deferred_income_expense_account()
			self.set_inter_company_account()

class CustomStockController(CustomAccountsController, StockController):
	def validate(self):
		CustomAccountsController.validate(self)
		if not self.get("is_return"):
			self.validate_inspection()
		self.validate_serialized_batch()
		self.clean_serial_nos()
		self.validate_customer_provided_item()
		self.set_rate_of_stock_uom()
		self.validate_internal_transfer()
		self.validate_putaway_capacity()

class CustomSellingController(CustomStockController, SellingController):
	def validate(self):
		CustomStockController.validate(self)
		self.validate_items()
		if not (self.get("is_debit_note") or self.get("is_return")):
			self.validate_max_discount()
		self.validate_selling_price()
		self.set_qty_as_per_stock_uom()
		self.set_po_nos(for_validate=True)
		self.set_gross_profit()
		set_default_income_account_for_item(self)
		self.set_customer_address()
		self.validate_for_duplicate_items()
		self.validate_target_warehouse()
		self.validate_auto_repeat_subscription_dates()


class CustomSalesInvoice(CustomSellingController, SalesInvoice):
	def validate(self):
		CustomSellingController.validate(self)

		if not (self.is_pos or self.is_debit_note):
			self.so_dn_required()

		self.set_tax_withholding()

		self.validate_proj_cust()
		self.validate_pos_return()
		self.validate_with_previous_doc()
		self.validate_uom_is_integer("stock_uom", "stock_qty")
		self.validate_uom_is_integer("uom", "qty")
		self.check_sales_order_on_hold_or_close("sales_order")
		self.validate_debit_to_acc()
		self.clear_unallocated_advances("Sales Invoice Advance", "advances")
		self.add_remarks()
		self.validate_fixed_asset()
		self.set_income_account_for_fixed_assets()
		self.validate_item_cost_centers()
		self.check_conversion_rate()
		self.validate_accounts()

		validate_inter_company_party(
			self.doctype, self.customer, self.company, self.inter_company_invoice_reference
		)

		if cint(self.is_pos):
			self.validate_pos()

		if cint(self.update_stock):
			self.validate_dropship_item()
			self.validate_item_code()
			self.validate_warehouse()
			self.update_current_stock()
			self.validate_delivery_note()

		# validate service stop date to lie in between start and end date
		validate_service_stop_date(self)

		if not self.is_opening:
			self.is_opening = "No"

		if self._action != "submit" and self.update_stock and not self.is_return:
			set_batch_nos(self, "warehouse", True)

		if self.redeem_loyalty_points:
			lp = frappe.get_doc("Loyalty Program", self.loyalty_program)
			self.loyalty_redemption_account = (
				lp.expense_account if not self.loyalty_redemption_account else self.loyalty_redemption_account
			)
			self.loyalty_redemption_cost_center = (
				lp.cost_center
				if not self.loyalty_redemption_cost_center
				else self.loyalty_redemption_cost_center
			)

		self.set_against_income_account()
		self.validate_time_sheets_are_submitted()
		self.validate_multiple_billing("Delivery Note", "dn_detail", "amount")
		if not self.is_return:
			self.validate_serial_numbers()
		else:
			self.timesheets = []
		self.update_packing_list()
		self.set_billing_hours_and_amount()
		self.update_timesheet_billing_for_project()
		self.set_status()
		if self.is_pos and not self.is_return:
			self.verify_payment_amount_is_positive()

		# validate amount in mode of payments for returned invoices for pos must be negative
		if self.is_pos and self.is_return:
			self.verify_payment_amount_is_negative()

		if (
			self.redeem_loyalty_points
			and self.loyalty_program
			and self.loyalty_points
			and not self.is_consolidated
		):
			validate_loyalty_points(self, self.loyalty_points)

		self.reset_default_field_value("set_warehouse", "items", "warehouse")

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