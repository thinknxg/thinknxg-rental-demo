# thinkNXG Rental Demo Dataset

**Frappe and ERPNext v16 · UAE · AED**

This dataset creates a fictional UAE formwork and scaffolding rental operation. It includes 12 stocked items with rental profiles and opening balances, one non-stock Job Type and its five-item kit, three customers, one supplier, four UAE addresses, and three projects with Rental Sites. The loader uses the app's document controllers and stock and billing functions, so the figures come from the resulting transactions.

The schema was checked against [thinkNXG Rental v2.8.1, repository commit 894ae26](https://github.com/thinknxg/ThinkNXG-Rental/commit/894ae26ff183995079dc940bd3291095b526b3f3).

## Set up the demo site

1. Use a disposable Frappe/ERPNext v16 site with thinkNXG Rental installed and migrated. Complete the ERPNext setup wizard with a UAE company whose default currency is **AED**. You can use **NXG Gulf Formwork Rentals LLC** as a fictional company name.
2. Complete the company's chart of accounts, default inventory, income, expense, receivable and payable accounts, default cost center, and fiscal years covering the demo period and today. Open the stock and accounts posting dates for that period. Take a site backup.
3. In Rental Settings, choose the AED company and click **Create Default Warehouses and Items**. Confirm the own rental yard, cross-hire yard, inspection, repair and scrap warehouses. Enable the Return Inspection setting. Turn off automatic item rental billing, automatic JCR billing, automatic Sales Invoice submission and automatic advance adjustment. The default UAE Rental Company Profile must exist for the demo pro forma. Use the company's own VAT configuration.
4. Extract the ZIP on the bench server. Keep all four input and loader files together. From the bench directory, open the console for the demo site.

~~~bash
bench --site rental-demo.example.test console
~~~

Run preflight. Replace the path and company name with yours.

~~~python
from runpy import run_path
seed = run_path("/absolute/path/rental_demo/seed_rental_demo.py")["run"]
seed("NXG Gulf Formwork Rentals LLC")
~~~

Then create the records.

~~~python
seed("NXG Gulf Formwork Rentals LLC", apply=True)
~~~

The start defaults to 23 days before the site's current date. To choose a date, pass the same ISO date to preflight and apply, for example `start="2026-11-03"` if it fits your site. To attach a particular enabled Sales Taxes and Charges Template to the hire contracts, pass `sales_tax_template="your template name"` to both calls. Without that argument the standard invoice handlers can use the company's default tax template. The pro forma uses the separate Rental Company Profile. The checks below use amounts before tax.

The loader submits opening stock and operational documents, including stock movements and cross-hire procurement and returns. The pro forma, Sales Invoices and supplier Purchase Invoice are drafts for review. The console prints the generated document names grouped by scenario. If an operation fails before commit, the loader requests rollback. If you want to replay the demo from day zero, restore the pre-demo backup or use a fresh site. A second run against the same dataset prefix stops on duplicate masters.

## Demo walkthrough

Open **thinkNXG Rental** in Desk or use the staff Command Center at `/rental/admin`. Look up the generated document IDs from the console or search for customers, sites and item codes starting with **NXGRD**.

| Scenario | Walk the transaction chain | Expected result |
|---|---|---|
| **Palm Quay Podium, Dubai** | Hire Order → Rental Contract → reservation → delivery → partial off-hire → inspection → damage settlement. Open the billing schedule and the separate pro forma. | 260 units dispatched, 80 returned, one lost and **179 still on site**. The inspection routes 77 good units back to the yard, two to repair and one to scrap. The sample 14-day rental period totals **AED 4,091.50** and the damage/loss settlement totals **AED 470.00**, both before tax. |
| **Meridian Atrium, Abu Dhabi** | Open the Job Type to see the kit, then follow Hire Order Contract → Rental Contract → reserved physical items → delivery → Job Completion Report → dismantle, full material return, inspection and JCR billing. | 108 pieces issued and returned. One erected job includes 10 days. The contract charge is **AED 4,800.00** and four excess days at AED 180.00 total **AED 720.00** before tax. The excess may span two schedules when the example crosses a month boundary. |
| **Creekstone Flyover, Sharjah** | Hire Order for 32 shoring tower frames → reserve 12 owned frames → Cross Hire Order for 20 → Purchase Order → Purchase Receipt → deliver both ownership categories → customer off-hire for the 20 supplier frames → inspection → supplier off-hire and Purchase Return → draft Purchase Invoice. | The 20 borrowed frames are received and returned at zero stock value. The draft supplier hire is **AED 1,625.00** before tax for 20 frames × 13 days × AED 6.25. **12 owned frames** remain at the customer site. |

Additional checks and presenter actions are in [DEMO_CHECKLIST.md](DEMO_CHECKLIST.md).

## Files

| File | Contents |
|---|---|
| `seed_rental_demo.py` | Bench loader with preflight, source-document mappings, transaction checks and generated document IDs. |
| `items.csv` | Stock catalogue, quantities, valuation rates, rental rates and damage charges. |
| `parties.csv` | Fictional UAE parties, addresses, projects and sites. |
| `scenarios.json` | Scenario inputs, day offsets and expected quantities and amounts. |

The CSV files are inputs to the bench loader. The loader resolves generated document names and child rows, submits the operational documents and lets the app create their ledger entries. Python syntax, dataset references and control totals were checked offline. Run preflight and apply on a configured demo bench to verify the live transaction flows.
