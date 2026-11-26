import contextlib
import io
import json
import traceback

import frappe
from frappe.utils import add_days, getdate, nowdate

MAX_APPLY_ATTEMPTS = 3
ATTEMPT_KEY = "rental_demo_seed_attempts"
SNAP_KEY = "rental_demo_prev_settings"
CONFIG_COMPANY = "rental_demo_seed_company"
DEFAULT_ABBR = "NXGR"
DEFAULT_COMPANY = "NXG Gulf Formwork Rentals LLC"
SKIP_FIELDTYPES = ("Section Break", "Column Break", "Tab Break", "Table", "HTML", "Button", "Heading")
SKIP_ROW_KEYS = ("name", "owner", "creation", "modified", "modified_by", "docstatus",
                 "parent", "parentfield", "parenttype", "idx", "doctype")
TABLES = ("own_rental_warehouses", "cross_hire_warehouses")


def _log(title, message):
    frappe.log_error(title=title, message=message)


def seed_if_needed():
    """after_install / after_migrate / run_now. Never raises."""
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

    for d in (add_days(getdate(nowdate()), -40), getdate(nowdate())):
        try:
            get_fiscal_year(d, as_dict=True)
        except FiscalYearError:
            name = str(d.year)
            if not frappe.db.exists("Fiscal Year", name):
                fy = frappe.get_doc({
                    "doctype": "Fiscal Year",
                    "year": name,
                    "year_start_date": f"{d.year}-01-01",
                    "year_end_date": f"{d.year}-12-31",
                })
                fy.flags.ignore_permissions = True
                fy.insert()
    frappe.db.commit()


def snapshot_settings():
    """Save the Rental Settings as they were before the first switch. The first snapshot is kept."""
    if frappe.db.get_default(SNAP_KEY):
        return
    s = frappe.get_doc("Rental Settings")
    data = {}
    for df in s.meta.fields:
        if df.fieldtype in SKIP_FIELDTYPES:
            continue
        value = s.get(df.fieldname)
        if value is not None:
            data[df.fieldname] = value
    data["_tables"] = {
        t: [{k: v for k, v in row.as_dict().items() if k not in SKIP_ROW_KEYS} for row in s.get(t)]
        for t in TABLES
    }
    raw = json.dumps(data, default=str)
    frappe.db.set_default(SNAP_KEY, raw)
    frappe.db.commit()
    _log("Rental demo previous settings snapshot", raw)


def restore_settings():
    raw = frappe.db.get_default(SNAP_KEY)
    if not raw:
        frappe.throw("No saved Rental Settings snapshot found.")
    data = json.loads(raw)
    tables = data.pop("_tables", {})
    s = frappe.get_doc("Rental Settings")
    for key, value in data.items():
        s.set(key, value)
    for t, rows in tables.items():
        s.set(t, [])
        for row in rows:
            s.append(t, row)
    s.flags.ignore_permissions = True
    s.save()
    frappe.db.commit()
    return f"Rental Settings restored to company {data.get('company')}."


def _seed_if_needed():
    company = frappe.conf.get(CONFIG_COMPANY) or DEFAULT_COMPANY

    if frappe.db.exists("Item", {"item_code": ["like", "NXGRD%"]}):
        return  # already seeded

    attempts = int(frappe.db.get_default(ATTEMPT_KEY) or 0)
    if attempts >= MAX_APPLY_ATTEMPTS:
        _log("Rental demo seed stopped", f"{attempts} apply attempts already used. Restore a backup and clear the counter before retrying.")
        return

    _log("Rental demo seed started", f"company={company}, attempt={attempts + 1}")
    frappe.set_user("Administrator")

    if frappe.db.exists("Company", company):
        info = frappe.db.get_value("Company", company, ["country", "default_currency"], as_dict=True)
        if info.country != "United Arab Emirates" or info.default_currency != "AED":
            _log("Rental demo seed skipped", f"Company '{company}' exists but is not UAE/AED.")
            return
    else:
        ensure_company(company)

    ensure_fiscal_year()

    current = frappe.db.get_single_value("Rental Settings", "company")
    if current and current != company:
        snapshot_settings()  # keep the KREATAO settings so they can be restored

    from thinknxg_rental.services.setup import setup_company

    setup_company(company)
    for field, value in (
        ("company", company),
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
