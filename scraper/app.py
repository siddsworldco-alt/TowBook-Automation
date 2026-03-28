import os, time, re, json, glob, csv
from datetime import datetime
from flask import Flask, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

app = Flask(__name__)

USERNAME = os.getenv("TOWBOOK_USERNAME")
PASSWORD = os.getenv("TOWBOOK_PASSWORD")
DOWNLOAD_DIR = "/app/output/csv_downloads"
COMBINED_CSV = "/app/output/combined_invoices.csv"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def make_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-zygote")
    opts.add_argument("--single-process")
    opts.binary_location = "/usr/bin/chromium"
    opts.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
    })
    service = Service("/usr/bin/chromedriver")
    return webdriver.Chrome(service=service, options=opts)

def js_set_value(driver, element_id, value):
    driver.execute_script(
        "var el=document.getElementById(arguments[0]);"
        "var s=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;"
        "s.call(el,arguments[1]);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));",
        element_id, value
    )

def dismiss_overlay(driver):
    try:
        driver.execute_script("""
            var bar = document.getElementById('callRequestsBar');
            if (bar) bar.style.display = 'none';
            var overlays = document.querySelectorAll('.modal, .overlay, .popup');
            overlays.forEach(function(el) { el.style.display = 'none'; });
        """)
        time.sleep(0.5)
    except:
        pass

def wait_for_page_ready(driver, timeout=60):
    end_time = time.time() + timeout
    while time.time() < end_time:
        ready = driver.execute_script(
            'return document.readyState === "complete" && '
            '(typeof jQuery === "undefined" || jQuery.active === 0)'
        )
        if ready:
            return True
        time.sleep(0.5)
    return False

def wait_for_csv(download_dir, existing_files, timeout=60):
    end_time = time.time() + timeout
    while time.time() < end_time:
        current = set(glob.glob(os.path.join(download_dir, "*.csv")))
        new = current - existing_files
        if new and not glob.glob(os.path.join(download_dir, "*.crdownload")):
            time.sleep(1)
            return list(new)[0]
        time.sleep(1)
    return None

def clean_name(name):
    return re.sub(r'<[^>]+>', '', name).strip()

def run_scraper():
    driver = None
    results = {
        "accounts": [],
        "csv_path": None,
        "row_count": 0,
        "errors": [],
        "timestamp": datetime.utcnow().isoformat()
    }
    try:
        print("[scraper] Starting Chrome...")
        driver = make_driver()
        wait = WebDriverWait(driver, 20)

        print("[scraper] Navigating to Towbook...")
        driver.get("https://app.towbook.com/")
        time.sleep(3)

        print("[scraper] Logging in...")
        wait.until(EC.element_to_be_clickable((By.ID, "Username")))
        js_set_value(driver, "Username", USERNAME)
        js_set_value(driver, "Password", PASSWORD)
        time.sleep(1)
        wait.until(EC.element_to_be_clickable((By.NAME, "bSignIn"))).click()
        time.sleep(3)
        print(f"[scraper] Logged in — {driver.current_url}")

        print("[scraper] Loading accounts page...")
        driver.get("https://app.towbook.com/Accounts/")
        wait_for_page_ready(driver)
        time.sleep(3)

        grid_data = driver.execute_script("""
            try {
                var gridSet = Object.values(w2ui).filter(g => g.records && g.records.length > 0);
                var grid = gridSet.find(g => g.name === 'myGrid') || gridSet[0];
                if (grid && grid.records) {
                    return JSON.stringify(grid.records.map(rec => ({
                        name: rec.Name || rec.name || '',
                        balance: rec.Balance || rec.balance || '',
                        id: rec.recid || rec.Id || rec.id || ''
                    })));
                }
                return 'NO_GRID_FOUND';
            } catch(e) { return 'ERROR:' + e.message; }
        """)

        accounts_with_balances = []
        if grid_data and not grid_data.startswith("ERROR") and grid_data != "NO_GRID_FOUND":
            for rec in json.loads(grid_data):
                bal = str(rec.get("balance", "")).strip()
                name = clean_name(rec.get("name", ""))
                if bal and bal not in ["0", "0.00", "$0.00"] and name:
                    accounts_with_balances.append((name, rec["id"], bal))
                    print(f"[scraper] Found: {name} | {bal}")
        else:
            results["errors"].append({"step": "grid_extract", "error": grid_data})

        print(f"[scraper] {len(accounts_with_balances)} accounts with balances")

        downloaded_csvs = []
        for idx, (name, acc_id, bal) in enumerate(accounts_with_balances):
            print(f"[scraper] [{idx+1}/{len(accounts_with_balances)}] {name}")
            try:
                driver.get(f"https://app.towbook.com/Accounts/Account.aspx?id={acc_id}")
                wait_for_page_ready(driver)
                time.sleep(2)
                dismiss_overlay(driver)

                clicked = driver.execute_script("""
                    var links = document.querySelectorAll('a');
                    for (var i = 0; i < links.length; i++) {
                        if (links[i].title === 'Unpaid' || links[i].textContent.trim() === 'Unpaid') {
                            links[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                print(f"[scraper] Unpaid clicked: {clicked}")
                time.sleep(2)

                try:
                    wait.until(EC.presence_of_element_located((By.ID, "grid_callsGrid_check_all")))
                    driver.execute_script("document.getElementById('grid_callsGrid_check_all').click();")
                except Exception as e:
                    print(f"[scraper] Select all error: {e}")

                existing = set(glob.glob(os.path.join(DOWNLOAD_DIR, "*.csv")))

                exported = driver.execute_script("""
                    var els = document.querySelectorAll('a, td, button');
                    for (var i = 0; i < els.length; i++) {
                        if (els[i].textContent.trim() === 'Export') {
                            els[i].click();
                            return true;
                        }
                    }
                    return false;
                """)
                print(f"[scraper] Export clicked: {exported}")

                csv_file = wait_for_csv(DOWNLOAD_DIR, existing)
                if csv_file:
                    safe = re.sub(r"[^\w\-]", "_", name)
                    new_path = os.path.join(DOWNLOAD_DIR, f"{safe}_{acc_id}.csv")
                    os.rename(csv_file, new_path)
                    downloaded_csvs.append((name, acc_id, bal, new_path))
                    results["accounts"].append({"name": name, "id": acc_id, "balance": bal})
                    print(f"[scraper] CSV saved for {name}")
                else:
                    print(f"[scraper] No CSV for {name}")
                    results["errors"].append({"account": name, "error": "CSV download timed out"})

            except Exception as e:
                print(f"[scraper] Error on {name}: {e}")
                results["errors"].append({"account": name, "error": str(e)})

        if downloaded_csvs:
            print("[scraper] Combining CSVs...")
            all_data = []
            headers = None
            for name, aid, bal, path in downloaded_csvs:
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        rows = list(csv.reader(f))
                        if not rows:
                            continue
                        if headers is None:
                            headers = ["Account", "ID", "Balance"] + rows[0]
                        for row in rows[1:]:
                            all_data.append([name, aid, bal] + row)
                except Exception as e:
                    results["errors"].append({"account": name, "error": str(e)})
            if all_data:
                with open(COMBINED_CSV, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(headers)
                    w.writerows(all_data)
                results["csv_path"] = COMBINED_CSV
                results["row_count"] = len(all_data)
                print(f"[scraper] Done — {len(all_data)} rows")

    except Exception as e:
        import traceback
        print(f"[scraper] Fatal: {traceback.format_exc()}")
        results["errors"].append({"step": "fatal", "error": str(e)})
    finally:
        if driver:
            driver.quit()

    results["success"] = results.get("csv_path") is not None
    return results

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/status", methods=["GET"])
def status():
    exists = os.path.exists(COMBINED_CSV)
    info = {"csv_exists": exists}
    if exists:
        stat = os.stat(COMBINED_CSV)
        info["size_bytes"] = stat.st_size
        info["last_modified"] = datetime.utcfromtimestamp(stat.st_mtime).isoformat()
        with open(COMBINED_CSV, "r", encoding="utf-8") as f:
            info["row_count"] = sum(1 for _ in f) - 1
    return jsonify(info)

@app.route("/scrape", methods=["POST"])
def scrape():
    data = run_scraper()
    return jsonify(data), 200 if data["success"] else 500

if __name__ == "__main__":
    print("[scraper] Starting Flask on port 5050...")
    app.run(host="0.0.0.0", port=5050)
