"""Load the AED thinkNXG Rental demo from a bench console. See README.md."""
import csv
import json
from datetime import timedelta
from pathlib import Path

DATA = Path(__file__).resolve().parent


def read_data():
    def rows(filename):
        with (DATA / filename).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    scenario = json.loads((DATA / "scenarios.json").read_text(encoding="utf-8"))
    items = {row["item_code"]: row for row in rows("items.csv")}
    parties = {row["key"]: row for row in rows("parties.csv")}
    return scenario, items, parties


def run(company, start=None, *, apply=False, sales_tax_template=None):
    """Print preflight by default. With apply=True, seed and commit one demo dataset."""
    import frappe
    from frappe.utils import cint, getdate, nowdate
    from erpnext.accounts.utils import get_fiscal_year

    if frappe.session.user != "Administrator":
        raise RuntimeError("Run from a bench console as Administrator.")
    if not {"erpnext", "thinknxg_rental"}.issubset(frappe.get_installed_apps()):
        raise RuntimeError("Install ERPNext v16 and thinknxg_rental, and run bench migrate.")

    from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_difference_account
    from thinknxg_rental import __version__
    from thinknxg_rental.services import warehouses
    from thinknxg_rental.services.setup import get_root

    scenario, items, parties = read_data()
    day_zero = getdate(start) if start else getdate(nowdate()) - timedelta(days=23)

    def day(offset):
        if isinstance(offset, str):
            offset = scenario["offsets"][offset]
        return day_zero + timedelta(days=offset)

    def check(condition, message):
        if not condition:
            raise RuntimeError(message)

    check(day("supplier_return") < getdate(nowdate()), "Select an earlier start; supplier return must be before today.")
    company_doc = frappe.get_doc("Company", company)
    check(company_doc.default_currency == scenario["currency"] and company_doc.country == scenario["country"],
          "Select an existing United Arab Emirates company with AED as its default currency.")
    settings = frappe.get_doc("Rental Settings")
    check(settings.company == company, "Run Create Default Warehouses and Items for this demo company in Rental Settings.")
    check(cint(settings.inspection_required), "Enable Return Inspection in Rental Settings.")
    check(not any(cint(settings.get(field)) for field in (
        "auto_generate_billing", "auto_generate_jcr_billing", "auto_submit_sales_invoice", "auto_adjust_advance"
    )), "Turn off automatic rental/JCR billing, Sales Invoice submission and advance adjustment for the demo.")
    own_yard = warehouses.default_own_warehouse()
    cross_yard = warehouses.default_cross_hire_warehouse()
    for warehouse in (own_yard, cross_yard, settings.inspection_warehouse, settings.repair_warehouse,
                      settings.scrap_warehouse):
        entry = frappe.db.get_value("Warehouse", warehouse, ["company", "is_group", "disabled"], as_dict=True) if warehouse else None
        check(entry and entry.company == company and not entry.is_group and not entry.disabled,
              f"Configure all operational warehouse leaves for {company}; check {warehouse or 'the blank warehouse'}.")

    for field in ("rental_charge_item", "cross_hire_charge_item", "damage_recovery_item", "loss_recovery_item"):
        check(settings.get(field) and frappe.db.exists("Item", settings.get(field)),
              f"Run rental setup to create the service item for {field}.")
    profile = frappe.db.get_value("Rental Company Profile", {"company": company}, ["name", "currency", "vat_country"], as_dict=True)
    check(profile and profile.currency == "AED" and profile.vat_country == scenario["country"],
          "Configure this company's UAE Rental Company Profile in AED.")
    if sales_tax_template:
        template = frappe.db.get_value("Sales Taxes and Charges Template", sales_tax_template, ["company", "disabled"], as_dict=True)
        check(template and template.company == company and not template.disabled, "Choose an enabled sales tax template for this company.")
    cost_center = company_doc.get("cost_center")
    check(cost_center and frappe.db.exists("Cost Center", cost_center), "Set the company's default cost center.")
    opening_account = get_difference_account("Opening Stock", company)
    check(opening_account, "Configure the company's Temporary Opening account.")
    for offset in range(scenario["offsets"]["supplier_return"] + 1):
        get_fiscal_year(day(offset), company=company, verbose=0)
    get_fiscal_year(getdate(nowdate()), company=company, verbose=0)
    check(frappe.db.exists("UOM", "Nos"), "Create the ERPNext Nos UOM.")
    root = get_root("Item Group", "parent_item_group")
    check(root, "Set up the ERPNext Item Group tree.")

    def leaf(doctype, preferred):
        return frappe.db.exists(doctype, {"name": preferred, "is_group": 0}) or frappe.db.get_value(
            doctype, {"is_group": 0}, "name", order_by="name asc"
        )

    customer_group = leaf("Customer Group", "Commercial")
    supplier_group = leaf("Supplier Group", "Services")
    territory = leaf("Territory", "United Arab Emirates")
    check(all((customer_group, supplier_group, territory)), "Configure a leaf customer group, supplier group and territory.")

    def check_collisions():
        group_name = "NXGRD Rental Demo Catalogue"
        checks = [("Item Group", group_name), ("Job Type", scenario["job"]["code"])]
        checks.extend(("Item", code) for code in [*items, scenario["job"]["code"]])
        checks.extend(("Rental Item Profile", code) for code in items)
        for row in parties.values():
            kind = row["kind"]
            checks.append((kind, {f"{kind.lower()}_name": row["party_name"]}))
            if row["site_name"]:
                checks.append(("Rental Site", row["site_name"]))
                checks.append(("Project", {"project_name": row["project_name"]}))
        for doctype, key in checks:
            check(not frappe.db.exists(doctype, key), f"{doctype} {key} already exists. Use a clean demo site or restore a pre-demo backup.")

    check_collisions()
    print(f"thinkNXG Rental {__version__} | {company} | AED | opening {day(0)} | last stock movement {day('supplier_return')}")
    if not apply:
        print("Preflight passed. No demo documents were written. Run again with apply=True to seed.")
        return

    from thinknxg_rental.services import billing, cross_hire, jcr_billing, ledger
    from thinknxg_rental.thinknxg_rental.doctype.cross_hire_off_hire_note import cross_hire_off_hire_note
    from thinknxg_rental.thinknxg_rental.doctype.hire_order import hire_order
    from thinknxg_rental.thinknxg_rental.doctype.hire_order_contract import hire_order_contract
    from thinknxg_rental.thinknxg_rental.doctype.hire_off_hire_note import hire_off_hire_note
    from thinknxg_rental.thinknxg_rental.doctype.rental_contract import rental_contract
    from thinknxg_rental.thinknxg_rental.doctype.rental_damage_settlement import rental_damage_settlement
    from thinknxg_rental.thinknxg_rental.doctype.rental_pro_forma_invoice import rental_pro_forma_invoice

    def insert(doc, submit=False):
        doc.insert()
        if submit:
            doc.submit()
        return doc

    def create(doctype, *, submit=False, **values):
        return insert(frappe.get_doc({"doctype": doctype, **values}), submit)

    def stock_item(code, label, group, stock=1):
        return create("Item", item_code=code, item_name=label, description=label, item_group=group,
                      stock_uom="Nos", is_stock_item=stock, is_purchase_item=stock, is_sales_item=1,
                      has_serial_no=0, has_batch_no=0)

    def create_hire_order(key, qty, dispatch_offset, end_offset, label):
        customer, site, project = customers[key]
        return create("Hire Order", submit=True, company=company, customer=customer, rental_site=site,
                      project=project, cost_center=cost_center, order_date=day(1), required_from=day(dispatch_offset),
                      expected_return_date=day(end_offset), customer_reference=f"NXGRD-{key}-PO",
                      rate_basis="Daily", billing_cycle="Monthly", remarks=label,
                      items=[{"item_code": code, "qty": count, "rate_basis": "Daily"} for code, count in qty.items()])

    def item_contract(order, grace_days):
        doc = hire_order.make_contract(order.name)
        doc.contract_date = order.order_date
        doc.grace_days = grace_days
        doc.minimum_hire_days = 1
        doc.align_to_calendar_month = 0
        doc.taxes_and_charges = sales_tax_template
        return insert(doc, True)

    def reserve(doc):
        return insert(rental_contract.make_reservation(doc.name), True)

    def dispatch(contract, reservation, offset):
        doc = rental_contract.make_delivery_order(contract.name)
        doc.rental_material_reservation = reservation.name
        doc.posting_date = day(offset)
        doc.posting_time = "09:30:00"
        check(bool(doc.items), f"No rows were found for dispatch on {contract.name}.")
        return insert(doc, True)

    def off_hire(contract, off_offset, return_offset, quantities=None, lost=None, ownership=None):
        doc = rental_contract.make_off_hire_note(contract.name)
        doc.off_hire_date = day(off_offset)
        doc.return_date = day(return_offset)
        picked = []
        for row in doc.items:
            if ownership and row.ownership != ownership:
                continue
            row.returned_qty = row.at_site_qty if quantities is None else quantities.get(row.item_code, 0)
            row.lost_qty = (lost or {}).get(row.item_code, 0)
            if row.returned_qty or row.lost_qty:
                picked.append(row)
        doc.set("items", picked)
        return insert(doc, True)

    def inspect(note, amounts=None):
        doc = hire_off_hire_note.make_inspection(note.name)
        doc.inspection_date = getdate(note.return_date) + timedelta(days=1)
        for row in doc.items:
            row.update((amounts or {}).get(row.item_code, {}))
            row.good_qty = row.returned_qty - (row.repairable_qty or 0) - (row.damaged_qty or 0)
        return insert(doc, True)

    def near(actual, expected, label):
        check(abs(float(actual) - expected) < 0.01, f"{label}: expected {expected}, got {actual}.")

    try:
        # Serialize this package for the selected company and recheck after taking the lock.
        frappe.db.get_value("Company", company, "name", for_update=True)
        check_collisions()
        print("Creating catalogue, parties, project sites and opening stock...")
        group = create("Item Group", item_group_name="NXGRD Rental Demo Catalogue", parent_item_group=root)
        for code, row in items.items():
            stock_item(code, row["item_name"], group.name)
            create("Rental Item Profile", item=code, category=row["category"], item_name=row["item_name"],
                   item_group=group.name, stock_uom="Nos", inspection_required=1, default_rate_basis="Daily",
                   weight_per_unit=float(row["weight_kg"]), minimum_hire_days=1,
                   daily_rate=float(row["daily_rate"]), weekly_rate=float(row["weekly_rate"]),
                   monthly_rate=float(row["monthly_rate"]), security_deposit_rate=float(row["deposit_per_unit"]),
                   replacement_value=float(row["replacement_value"]), repair_charge=float(row["repair_charge"]),
                   lost_charge=float(row["lost_charge"]), beyond_repair_charge=float(row["beyond_repair_charge"]))
        job = scenario["job"]
        stock_item(job["code"], job["label"], group.name, stock=0)
        create("Job Type", item_code=job["code"], item_name=job["label"], job_type_name=job["label"],
               item_group=group.name, stock_uom="Nos",
               rental_items=[{"item_code": code, "qty": qty, "uom": "Nos", "item_name": items[code]["item_name"]}
                             for code, qty in job["kit"].items()])

        customers = {}
        for key, row in parties.items():
            is_customer = row["kind"] == "Customer"
            if is_customer:
                party = create("Customer", customer_name=row["party_name"], customer_type="Company",
                               customer_group=customer_group, territory=territory, default_currency="AED")
            else:
                party = create("Supplier", supplier_name=row["party_name"], supplier_type="Company",
                               supplier_group=supplier_group, country=scenario["country"], default_currency="AED")
            address = create("Address", address_title=row["party_name"], address_type="Billing",
                             address_line1=row["address_line1"], city=row["city"], country=scenario["country"],
                             is_primary_address=1, links=[{"link_doctype": party.doctype, "link_name": party.name}])
            if not is_customer:
                supplier = party.name
                continue
            project = create("Project", project_name=row["project_name"], company=company,
                             customer=party.name, expected_start_date=day(1), expected_end_date=day("cross_end"))
            site = create("Rental Site", site_name=row["site_name"], customer=party.name, company=company,
                          project=project.name, address=address.name, location=row["city"])
            customers[key] = (party.name, site.name, project.name)

        opening = create("Stock Reconciliation", submit=True, company=company, purpose="Opening Stock",
                         posting_date=day(0), posting_time="08:00:00", set_posting_time=1, expense_account=opening_account,
                         cost_center=cost_center, items=[{"item_code": code, "warehouse": own_yard,
                           "qty": float(row["opening_qty"]), "valuation_rate": float(row["valuation_rate"])}
                           for code, row in items.items()])
        expected = scenario["expected"]
        near(opening.difference_amount, expected["opening_stock_value"], "Opening stock value")

        print("Seeding owned-stock hire, return inspection, settlement and billing...")
        own = scenario["own"]
        order = create_hire_order("OWN", own["order"], "own_dispatch", "own_end", "Owned formwork hire with a partial return")
        contract = item_contract(order, own["grace_days"])
        pro_forma = insert(rental_pro_forma_invoice.make_from_rental_contract(contract.name))
        near(pro_forma.total_deposit, expected["own_pro_forma_deposit"], "Pro forma deposit")
        owned_delivery = dispatch(contract, reserve(contract), "own_dispatch")
        own_note = off_hire(contract, "own_off_hire", "own_return", quantities=own["returned"], lost=own["lost"])
        own_inspection = inspect(own_note, own["inspection"])
        settlement = insert(hire_off_hire_note.make_damage_settlement(own_note.name), True)
        recovery_invoice = rental_damage_settlement.make_sales_invoice(settlement.name)
        near(settlement.grand_total, expected["damage_net"], "Damage and loss before tax")
        schedules = billing.generate_billing(contract.name, upto_date=str(day("own_bill_through")))
        near(sum(frappe.db.get_value("Rental Billing Schedule", name, "total_amount") for name in schedules),
             expected["own_rental_net"], "Owned-stock rental before tax")
        near(sum(ledger.get_site_balance(contract.name).values()), expected["own_at_site_qty"], "Owned units at site")

        print("Seeding job hire, erection, dismantle and JCR charges...")
        customer, site, project = customers["JOB"]
        hoc = create("Hire Order Contract", submit=True, company=company, customer=customer, rental_site=site,
                     project=project, cost_center=cost_center, order_date=day(1), required_from=day("job_dispatch"),
                     customer_reference="NXGRD-JOB-PO", contract_charge_billing="On Erection",
                     taxes_and_charges=sales_tax_template, items=[{
                         "job_type": job["code"], "job_type_name": job["label"], "location": job["location"],
                         "qty": 1, "included_days": job["included_days"], "contract_rate": job["contract_rate"],
                         "excess_rate_basis": "Daily", "excess_rate": job["excess_daily_rate"]}])
        job_contract = insert(hire_order_contract.make_rental_contract(hoc.name), True)
        job_delivery = dispatch(job_contract, reserve(job_contract), "job_dispatch")
        jcr = hire_order_contract.make_jcr(hoc.name)
        jcr.job_lines[0].job_qty = 1
        jcr.job_lines[0].erection_date = day("job_dispatch")
        insert(jcr, True)
        jcr.reload().record_dismantle(str(day("job_dismantle")))
        job_note = off_hire(job_contract, "job_dismantle", "job_dismantle")
        job_inspection = inspect(job_note)
        job_invoice_names = []
        contract_net = excess_net = 0
        for row in jcr_billing.get_schedules(jcr.name):
            if row.billing_type == "Contract":
                contract_net += row.amount
            else:
                excess_net += row.amount
            if row.amount > 0:
                job_invoice_names.append(jcr_billing.make_sales_invoice(row.name))
        near(contract_net, expected["job_contract_net"], "JCR contract before tax")
        near(excess_net, expected["job_excess_net"], "JCR excess before tax")
        near(sum(ledger.get_site_balance(job_contract.name).values()), 0, "Job units at site")

        print("Seeding the cross-hire shortfall, zero-value custody and supplier return...")
        cross = scenario["cross"]
        cross_order = create_hire_order("CROSS", {cross["item"]: cross["total_qty"]}, "cross_dispatch", "cross_end",
                                   "Mixed ownership hire with a supplier shortfall")
        cross_contract = item_contract(cross_order, 1)
        cross_reservation = reserve(cross_contract)
        near(cross_reservation.total_shortfall_qty, cross["borrowed_qty"], "Cross-hire shortfall")
        cho = rental_contract.make_cross_hire_order(cross_contract.name)
        cho.update({"supplier": supplier, "order_date": day(4), "hire_from": day("cross_receipt"),
                    "expected_return_date": day("supplier_return"), "rate_basis": "Daily",
                    "receive_at": "Cross Hire Yard", "material_reservation": cross_reservation.name})
        check(len(cho.items) == 1, "Expected one cross-hire shortfall line.")
        near(cho.items[0].qty, cross["borrowed_qty"], "Cross Hire Order quantity")
        cho.items[0].rate_basis = "Daily"
        cho.items[0].rate = cross["supplier_daily_rate"]
        insert(cho, True)
        purchase_order = frappe.get_doc("Purchase Order", cho.purchase_order)
        if purchase_order.docstatus == 0:
            purchase_order.submit()
        receipt = cross_hire.make_cross_hire_receipt(cho.name)
        receipt.posting_date = day("cross_receipt")
        receipt.posting_time = "08:30:00"
        insert(receipt, True)
        near(receipt.items[0].rate, 0, "Cross-hire receipt item rate")
        near(receipt.items[0].qty, cross["borrowed_qty"], "Cross-hire receipt quantity")
        cross_delivery = dispatch(cross_contract, cross_reservation, "cross_dispatch")
        near(sum(row.qty for row in cross_delivery.items if row.ownership == "Cross Hire"), cross["borrowed_qty"],
             "Cross-hire dispatch quantity")
        cross_note = off_hire(cross_contract, "cross_off_hire", "cross_return", ownership="Cross Hire")
        cross_inspection = inspect(cross_note)
        supplier_return = cross_hire_off_hire_note.make_cross_hire_off_hire_note(cho.name)
        supplier_return.off_hire_date = day("supplier_return")
        supplier_return.last_billable_date = day("cross_return")
        insert(supplier_return, True)
        supplier_bill = cross_hire.make_supplier_invoice(cho.name, upto_date=str(day("supplier_return")))
        near(frappe.db.get_value("Purchase Invoice", supplier_bill, "net_total"), expected["supplier_net"],
             "Supplier hire before tax")
        near(sum(ledger.get_site_balance(cross_contract.name).values()), expected["cross_owned_at_site_qty"],
             "Mixed hire remaining units at site")

        result = {
            "opening_stock": opening.name,
            "owned": {"order": order.name, "contract": contract.name, "delivery": owned_delivery.name,
                      "off_hire": own_note.name, "inspection": own_inspection.name, "settlement": settlement.name,
                      "recovery_invoice": recovery_invoice, "billing_schedules": schedules,
                      "rental_invoices": [frappe.db.get_value("Rental Billing Schedule", name, "sales_invoice")
                                          for name in schedules], "pro_forma": pro_forma.name},
            "job": {"order": hoc.name, "contract": job_contract.name, "delivery": job_delivery.name, "jcr": jcr.name,
                    "off_hire": job_note.name, "inspection": job_inspection.name, "invoices": job_invoice_names},
            "cross_hire": {"order": cross_order.name, "contract": cross_contract.name, "reservation": cross_reservation.name,
                           "cross_hire_order": cho.name, "purchase_order": purchase_order.name, "receipt": receipt.name,
                           "delivery": cross_delivery.name, "off_hire": cross_note.name, "inspection": cross_inspection.name,
                           "supplier_off_hire": supplier_return.name, "purchase_returns": supplier_return.purchase_returns,
                           "purchase_invoice": supplier_bill},
        }
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    print(json.dumps(result, indent=2))
    return result
