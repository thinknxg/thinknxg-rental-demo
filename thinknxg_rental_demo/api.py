import contextlib
import io
import traceback

import frappe


def _run(company, start=None, sales_tax_template=None, apply=False):
    from thinknxg_rental_demo.demo.seed_rental_demo import run

    buf = io.StringIO()
    status = "OK"
    try:
        with contextlib.redirect_stdout(buf):
            run(company, start or None, apply=apply, sales_tax_template=sales_tax_template or None)
    except Exception:
        frappe.db.rollback()
        status = "FAILED"
        buf.write("\n" + traceback.format_exc())
    return status, buf.getvalue()


@frappe.whitelist()
def preflight(company, start=None, sales_tax_template=None):
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator only")
    status, output = _run(company, start, sales_tax_template, apply=False)
    return {"status": status, "output": output}


@frappe.whitelist()
def apply(company, start=None, sales_tax_template=None):
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator only")
    frappe.enqueue(
        "thinknxg_rental_demo.api._apply_job",
        queue="long",
        timeout=3000,
        company=company,
        start=start,
        sales_tax_template=sales_tax_template,
    )
    return "Queued. Check Error Log for 'Rental demo seed' in a few minutes."


def _apply_job(company, start=None, sales_tax_template=None):
    status, output = _run(company, start, sales_tax_template, apply=True)
    frappe.log_error(title=f"Rental demo seed {status}", message=output)


@frappe.whitelist()
def run_now():
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator only")
    frappe.enqueue("thinknxg_rental_demo.setup.seed_if_needed", queue="long", timeout=3000)
    return "Queued. Check Error Log for 'Rental demo seed' in a few minutes."


@frappe.whitelist()
def restore_settings():
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator only")
    from thinknxg_rental_demo.setup import restore_settings as _restore
    return _restore()


@frappe.whitelist()
def status():
    if frappe.session.user != "Administrator":
        frappe.throw("Administrator only")
    from thinknxg_rental_demo.setup import DEFAULT_COMPANY
    company = frappe.conf.get("rental_demo_seed_company") or DEFAULT_COMPANY
    return {
        "rental_settings_company": frappe.db.get_single_value("Rental Settings", "company"),
        "demo_company_exists": bool(frappe.db.exists("Company", company)),
        "nxgrd_items": frappe.db.count("Item", {"item_code": ["like", "NXGRD%"]}),
        "stock_ledger_entries_demo_company": frappe.db.count("Stock Ledger Entry", {"company": company}),
        "attempts_used": frappe.db.get_default("rental_demo_seed_attempts"),
        "latest_logs": frappe.get_all(
            "Error Log",
            filters={"method": ["like", "Rental demo seed%"]},
            fields=["creation", "method"],
            order_by="creation desc",
            limit=6,
        ),
    }
