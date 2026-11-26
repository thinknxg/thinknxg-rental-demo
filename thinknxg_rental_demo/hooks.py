app_name = "thinknxg_rental_demo"
app_title = "ThinkNXG Rental Demo"
app_publisher = "ThinkNXG"
app_description = "AED demo data for ThinkNXG Rental"
app_email = "dev@thinknxg.com"
app_license = "mit"
required_apps = ["frappe", "erpnext", "thinknxg_rental"]

after_migrate = ["thinknxg_rental_demo.setup.seed_if_needed"]

after_install = "thinknxg_rental_demo.setup.seed_if_needed"
