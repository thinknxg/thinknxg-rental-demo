import contextlib
import io
import traceback

import frappe
from frappe.utils import add_days, getdate, nowdate

MAX_APPLY_ATTEMPTS = 3
ATTEMPT_KEY = "rental_demo_seed_attempts"
CONFIG_COMPANY = "rental_demo_seed_company"
CONFIG_FORCE = "rental_demo_force_company"
DEFAULT_ABBR = "NXGR"


def _log(title, message):
    frappe.log_error(title=title, message=message)


def seed_if_needed():
    """after_migrate: seed the AED demo only when site config opts in. Never raises."""
    try:
        _seed_if_needed()
    except Exception:
        frappe.db.rollback()
        _log("Rental demo seed crashed", traceback.format_exc())


def ensure_company(company):
    if frappe.db.exists("Company", company):
        return
    abbr = DEFAULT_ABBR
    n = 1
    while frappe.db.exists("Company", {"abbr": abbr}):
        n += 1
        abbr = f"{DEFAULT_ABBR}{n}"
    doc = frappe.get_doc({
        "doctype": "Company",
        "company_name": company,
        "abbr": abbr,
        "default_currency": "AED",
        "country": "United Arab Emirates",
        "create_chart_of_accounts_based_on": "Standard Template",
        "chart_of_accounts": "Standard",
    })
    doc.flags.ignore_permissions = True
    doc.insert()
    frappe.db.commit()


def ensure_fiscal_year():
    from erpnext.accounts.utils import FiscalYearError, get_fiscal_year

    start = add_days(getdate(nowdate()), -40)
    for d in (start, getdate(nowdate())):
        try:
            get_fiscal_year(d, as_dict=True)
        except FiscalYearError:
            year = d.year
            name = str(year)
            if not frappe.db.exists("Fiscal Year", name):
                fy = frappe.get_doc({
                    "doctype": "Fiscal Year",
                    "year": name,
                    "year_start_date": f"{year}-01-01",
                    "year_end_date": f"{year}-12-31",
                })
                fy.flags.ignore_permissions = True
                fy.insert()
    frappe.db.commit()


def _seed_if_needed():
    company = frappe.conf.get(CONFIG_COMPANY)
    if not company:
        return  # not opted in

    if frappe.db.exists("Item", {"item_code": ["like", "NXGRD%"]}):
        return  # already seeded

    attempts = int(frappe.db.get_default(ATTEMPT_KEY) or 0)
    if attempts >= MAX_APPLY_ATTEMPTS:
        return

    _log("Rental demo seed started", f"company={company}, attempt={attempts + 1}")
    frappe.set_user("Administrator")

    if frappe.db.exists("Company", company):
        info = frappe.db.get_value("Company", company, ["country", "default_currency"], as_dict=True)
        if info.country != "United Arab Emirates" or info.default_currency != "AED":
            _log("Rental demo seed skipped",
                 f"Company '{company}' exists but is not UAE/AED. Use a different name in {CONFIG_COMPANY}.")
            return
    else:
        ensure_company(company)

    ensure_fiscal_year()

    current = frappe.db.get_single_value("Rental Settings", "company")
    if current and current != company and not frappe.conf.get(CONFIG_FORCE):
        _log("Rental demo seed skipped",
             f"Rental Settings uses company '{current}'. The seed needs it to use '{company}'. "
             f"Set {CONFIG_FORCE} to 1 in Site Config to allow the switch, then update the site again.")
        return

    from thinknxg_rental.services.setup import setup_company

    setup_company(company)
    for field, value in (
        ("inspection_required", 1),
        ("auto_generate_billing", 0),
        ("auto_generate_jcr_billing", 0),
        ("auto_submit_sales_invoice", 0),
        ("auto_adjust_advance", 0),
    ):
        frappe.db.set_single_value("Rental Settings", field, value)
    frappe.db.commit()
    frappe.clear_cache()

    from thinknxg_rental_demo.demo.seed_rental_demo import run

    # preflight first: a failure here does not use up an attempt
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            run(company, apply=False)
    except Exception:
        _log("Rental demo seed preflight failed", buf.getvalue() + "\n" + traceback.format_exc())
        return

    frappe.db.set_default(ATTEMPT_KEY, str(attempts + 1))
    frappe.db.commit()

    buf = io.StringIO()
    status = "OK"
    try:
        with contextlib.redirect_stdout(buf):
            run(company, apply=True)
    except Exception:
        frappe.db.rollback()
        status = "FAILED"
        buf.write("\n" + traceback.format_exc())
    _log(f"Rental demo seed {status}", buf.getvalue())
