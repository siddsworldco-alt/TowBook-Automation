import os, time, re, json, glob, csv, requests
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
PROGRESS_WEBHOOK_URL = os.getenv("N8N_PROGRESS_WEBHOOK_URL")
DOWNLOAD_DIR = "/app/output/csv_downloads"
COMBINED_CSV = "/app/output/combined_invoices.csv"
SCREENSHOT_DIR = "/app/output/screenshots"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def send_update(message, status="info", data=None):
    print(f"[scraper-log] {status.upper()}: {message}")
    if not PROGRESS_WEBHOOK_URL:
        print("[scraper-log] No webhook URL configured, skipping.")
        return
    try:
        payload = {
            "message": message,
            "status": status,
            "timestamp": datetime.utcnow().isoformat(),
            "data": data or {}
        }
        requests.post(PROGRESS_WEBHOOK_URL, json=payload, timeout=5)
    except Exception as e:
        print(f"[ERROR] Failed to send update to n8n: {e}")

def take_screenshot(driver, name):
    try:
        timestamp = datetime.now().strftime("%H-%M-%S")
        path = os.path.join(SCREENSHOT_DIR, f"{timestamp}_{name}.png")
        driver.save_screenshot(path)
        print(f"[scraper] Screenshot saved: {path}")
        return path
    except Exception as e:
        print(f"[scraper] Failed to save screenshot: {e}")
        return None

def make_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    opts.binary_location = "/usr/bin/chromium"
    opts.add_experimental_option("prefs", {
        "download.default_directory": DOWNLOAD_DIR,
        "download.prompt_for_download": False,
        "safebrowsing.enabled": True,
    })
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=opts)
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": DOWNLOAD_DIR
    })
    return driver

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
        send_update("Starting Chrome browser...", "start")
        driver = make_driver()
        wait = WebDriverWait(driver, 25)

        send_update("Navigating to Towbook login page...", "process")
        driver.get("https://app.towbook.com/")
        
        send_update("Entering credentials...", "process")
        try:
            username_field = wait.until(EC.element_to_be_clickable((By.ID, "Username")))
            js_set_value(driver, "Username", USERNAME)
            js_set_value(driver, "Password", PASSWORD)
            wait.until(EC.element_to_be_clickable((By.NAME, "bSignIn"))).click()
        except Exception as e:
            take_screenshot(driver, "login_failed")
            raise Exception(f"Login elements not found or clickable: {e}")

        if "login" in driver.current_url.lower():
            take_screenshot(driver, "auth_failed")
            raise Exception("Still on login page after clicking sign-in. Check credentials or CAPTCHA.")

        send_update(f"Successfully logged in.", "success")

        send_update("Loading Accounts page...", "process")
        driver.get("https://app.towbook.com/Accounts/")
        wait_for_page_ready(driver)

        send_update("Extracting accounts with balances...", "process")
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
        else:
            take_screenshot(driver, "grid_error")
            results["errors"].append({"step": "grid_extract", "error": grid_data})
            send_update(f"Failed to find accounts grid: {grid_data}", "error")

        total_acc = len(accounts_with_balances)
        send_update(f"Found {total_acc} accounts with balances.", "info", {"count": total_acc})

        downloaded_csvs = []
        for idx, (name, acc_id, bal) in enumerate(accounts_with_balances):
            progress = f"[{idx+1}/{total_acc}]"
            send_update(f"{progress} Processing account: {name} (Balance: {bal})", "process")
            try:
                driver.get(f"https://app.towbook.com/Accounts/Account.aspx?id={acc_id}")
                wait_for_page_ready(driver)
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
                if not clicked:
                    send_update(f"{progress} Warning: 'Unpaid' tab not found for {name}", "warning")
                
                time.sleep(2)

                try:
                    wait.until(EC.presence_of_element_located((By.ID, "grid_callsGrid_check_all")))
                    driver.execute_script("document.getElementById('grid_callsGrid_check_all').click();")
                except Exception as e:
                    send_update(f"{progress} Note: Could not find 'select all' checkbox for {name}", "info")

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
                
                if not exported:
                    send_update(f"{progress} Error: 'Export' button not found for {name}", "error")
                    take_screenshot(driver, f"export_missing_{acc_id}")
                    continue

                csv_file = wait_for_csv(DOWNLOAD_DIR, existing)
                if csv_file:
                    safe = re.sub(r"[^\w\-]", "_", name)
                    new_path = os.path.join(DOWNLOAD_DIR, f"{safe}_{acc_id}.csv")
                    os.rename(csv_file, new_path)
                    downloaded_csvs.append((name, acc_id, bal, new_path))
                    results["accounts"].append({
                        "name": name, 
                        "id": acc_id, 
                        "balance": bal,
                        "filename": f"{safe}_{acc_id}.csv"
                    })
                    send_update(f"{progress} CSV downloaded for {name}", "success")
                else:
                    send_update(f"{progress} Timeout waiting for CSV download: {name}", "error")
                    results["errors"].append({"account": name, "error": "CSV download timed out"})

            except Exception as e:
                send_update(f"{progress} Error on account {name}: {str(e)}", "error")
                results["errors"].append({"account": name, "error": str(e)})

        if downloaded_csvs:
            send_update("Combining all downloaded CSVs...", "process")
            all_data = []
            headers = None
            for name, aid, bal, path in downloaded_csvs:
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        rows = list(csv.reader(f))
                        if not rows: continue
                        if headers is None:
                            headers = ["Account", "AccountID", "AccountBalance"] + rows[0]
                        for row in rows[1:]:
                            all_data.append([name, aid, bal] + row)
                except Exception as e:
                    results["errors"].append({"account": name, "error": f"Merge error: {e}"})
            
            if all_data:
                with open(COMBINED_CSV, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f)
                    w.writerow(headers)
                    w.writerows(all_data)
                results["csv_path"] = COMBINED_CSV
                results["row_count"] = len(all_data)
                send_update(f"Job complete! Combined {len(all_data)} total rows.", "finish", {"rows": len(all_data)})

    except Exception as e:
        import traceback
        err_msg = f"Fatal scrapier error: {str(e)}"
        send_update(err_msg, "fatal")
        print(traceback.format_exc())
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
    print("[FLASK] !!! EMERGENCY LOG: ROUTE HIT !!!")
    print("[flask] Scrape request received! Starting run_scraper...")
    try:
        data = run_scraper()
        print("[flask] Scrape completed successfully.")
        return jsonify(data)
    except Exception as e:
        print(f"[flask] Fatal error during scrape: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    print("[scraper] Starting Flask on port 5050 with threading enabled...")
    app.run(host="0.0.0.0", port=5050, threaded=True)
