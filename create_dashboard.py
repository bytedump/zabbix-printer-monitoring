import json
import os

import requests

import zbx

"""
=============================================================================
PRINTER DASHBOARD GENERATOR SCRIPT
=============================================================================
This script automates the creation of a Grafana dashboard for printers monitored
in Zabbix. It does the following:

1. ZABBIX INTEGRATION:
   - Connects to the Zabbix API using credentials from the .env file.
   - Fetches all hosts and checks their items (specifically Toner Levels).
   - Identifies if a printer is Color, Black & White, or Offline based on items.

2. GRAFANA INTEGRATION:
   - Connects to the Grafana API.
   - Automatically finds the Zabbix Data Source UID (no hardcoding needed).
   - Deletes any old versions of the dashboard.
   - Builds a JSON structure for the new dashboard with a 4-column layout.
   - Injects 'Gauge' panels for B&W, 'BarGauge' for Color, and 'Stat' for Offline.
   - Posts the new dashboard back to Grafana.
=============================================================================
"""

# Load credentials from .env (if present) into the environment.
zbx.load_env()

# --- GRAFANA CONFIGURATION ---
GRAFANA_URL = "http://localhost:3000/api"
GRAFANA_ADMIN_PASSWORD = os.environ.get("GRAFANA_ADMIN_PASSWORD", "admin")
AUTH = ("admin", GRAFANA_ADMIN_PASSWORD)

# --- ZABBIX CONFIGURATION ---
ZABBIX_API_USER = os.environ.get("ZABBIX_API_USER", "Admin")
ZABBIX_API_PASSWORD = os.environ.get("ZABBIX_API_PASSWORD", "zabbix")

# 1. Authenticate with Zabbix
zauth = zbx.zapi("user.login", {"username": ZABBIX_API_USER, "password": ZABBIX_API_PASSWORD})

# 2. Automatically find the Zabbix Data Source UID in Grafana
zabbix_ds_uid = None
datasources = requests.get(f"{GRAFANA_URL}/datasources", auth=AUTH).json()
for ds in datasources:
    if ds.get("type") == "alexanderzobnin-zabbix-datasource":
        zabbix_ds_uid = ds.get("uid")
        break

if not zabbix_ds_uid:
    print("Error: Zabbix Data Source not found in Grafana! Please configure it first.")
    exit(1)

print(f"Found Zabbix Data Source UID: {zabbix_ds_uid}")

# Panel-level datasource reference. Grafana 10 (schemaVersion 39) needs this on the
# panel itself, not only on the target, or panels render empty ("No data").
DS = {"type": "alexanderzobnin-zabbix-datasource", "uid": zabbix_ds_uid}

inventory = "printers.json" if os.path.exists("printers.json") else "printers.example.json"
with open(inventory) as f:
    all_printers = json.load(f)

hosts = zbx.zapi("host.get", {"output": ["host"]}, zauth)
host_items = {}
for h in hosts:
    if h["host"] == "Zabbix server": continue
    items = zbx.zapi("item.get", {"hostids": h["hostid"], "output": ["name", "lastvalue"]}, zauth)
    # Classify on TONER items only. Hosts now also carry a "Printer Error State"
    # item, so counting all items would push every B&W printer (1 toner + 1
    # error item) into the Color branch and stop offline hosts reading as empty.
    host_items[h["host"]] = [it for it in items if it["name"].startswith("Toner Level:")]

# Delete old dashboards
for d in requests.get(f"{GRAFANA_URL}/search?type=dash-db", auth=AUTH).json():
    requests.delete(f"{GRAFANA_URL}/dashboards/uid/{d['uid']}", auth=AUTH)

# Layout: 4 columns x 5 rows = 20 printers
COLS = 4
W = 6  # 6 * 4 = 24

# Per-section panel heights. Colour panels hold four dials; B&W one big dial;
# offline a status block.
GROUP_H = {0: 7, 1: 6, 2: 3}

# Value/label font sizes for TV legibility. B&W gauges show the printer name big;
# colour gauges pack four dials (CMYK) so their text is smaller.
BW_GAUGE_TEXT = {"valueSize": 40, "titleSize": 24}
COLOR_BAR_TEXT = {"valueSize": 34, "titleSize": 22}

def sort_key(name):
    items = host_items.get(name, [])
    if not items: return (2, name)
    if len(items) > 1: return (0, name)
    return (1, name)

ordered = sorted([p["name"] for p in all_printers], key=sort_key)

panels = []
pid = 1
y = 0

# Title
panels.append({
    "id": pid, "type": "text", "title": "", "transparent": True,
    "gridPos": {"h": 3, "w": 24, "x": 0, "y": y},
    "options": {"mode": "markdown",
                "content": "# 🖨️ Printer Monitoring — Toner Levels\n\n*Live fleet status · auto-refresh every 30s*"}
})
pid += 1; y += 3

# Paper-alert banner: the Zabbix datasource's dedicated Problems panel, scoped to
# the Printers group. It stays empty while the fleet is healthy and lists one
# clean row per paper problem (jam / out of paper / low paper / door open) when a
# trigger fires. The panel renders problems itself; the datasource routes to its
# problems handler only on an exact string match, so queryType must be "5"
# (Problems) -- not a number, not a name.
BANNER_H = 8
panels.append({
    "id": pid, "type": "alexanderzobnin-zabbix-triggers-panel", "datasource": DS,
    "title": "⚠️ Paper Alerts",
    "gridPos": {"h": BANNER_H, "w": 24, "x": 0, "y": y},
    "targets": [{
        "refId": "A",
        "datasource": DS,
        "queryType": "5",
        "group": {"filter": "/.*/"},
        "host": {"filter": "/.*/"},
        "application": {"filter": ""},
        "proxy": {"filter": ""},
        "trigger": {"filter": "/Paper jam|Out of paper|Low paper|Door open/"},
        "options": {"showProblems": "problems", "minSeverity": 0,
                    "acknowledged": 0, "sortProblems": "severity", "limit": 1001}
    }],
    "options": {
        # Severity shown as a filled colour block (the bold red/orange/yellow
        # cell), no Status column, no heart icon, a short HH:mm change time --
        # Printer / Severity / Problem / Time.
        "hostField": True, "hostTechNameField": False,
        "severityField": True, "statusField": False, "statusIcon": False,
        "ackField": False, "descriptionField": True, "ageField": False,
        "showTags": False, "problemTimeline": False, "highlightBackground": True,
        "customLastChangeFormat": True, "lastChangeFormat": "HH:mm",
        "sortProblems": "severity", "fontSize": "150%", "pageSize": 50, "layout": "table"
    }
})
pid += 1; y += BANNER_H

GROUP_LABELS = {0: "🎨 Color Printers", 1: "⬛ Black & White Printers", 2: "⛔ Offline"}

GROUPS = {g: [n for n in ordered if sort_key(n)[0] == g] for g in (0, 1, 2)}

THRESHOLDS = {"mode": "absolute", "steps": [
    {"color": "red", "value": None}, {"color": "orange", "value": 10},
    {"color": "yellow", "value": 20}, {"color": "green", "value": 35}]}


def toner_target(host_filter):
    """A Zabbix metrics target for the toner items of the matching host(s)."""
    return {
        "refId": "A",
        "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": zabbix_ds_uid},
        "queryType": 0, "group": {"filter": "/.*/"}, "host": {"filter": host_filter},
        "application": {"filter": ""}, "item": {"filter": "/Toner.*/"},
        "functions": [], "options": {"showDisabledItems": False},
    }


def row_header(title):
    global pid, y
    panels.append({"id": pid, "type": "row", "title": title, "collapsed": False,
                   "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []})
    pid += 1; y += 1


# --- Colour printers: one wide panel each, four thick ink bars (CMYK), two per
# row so the bars are long and legible from across the room ---
if GROUPS[0]:
    row_header(GROUP_LABELS[0])
    col = 0
    W_COLOR, COLS_COLOR = 12, 2
    for printer_name in GROUPS[0]:
        overrides = []
        for item in host_items.get(printer_name, []):
            # "Toner Level: Black Ink Supply Unit T01D1/T01C1" -> "Black"
            color_name = item["name"].replace("Toner Level: ", "").split(" Ink Supply Unit ")[0]
            overrides.append({"matcher": {"id": "byName", "options": item["name"]},
                              "properties": [{"id": "displayName", "value": color_name}]})
        panels.append({
            "id": pid, "type": "bargauge", "datasource": DS, "title": printer_name,
            "gridPos": {"h": GROUP_H[0], "w": W_COLOR, "x": col * W_COLOR, "y": y},
            "targets": [toner_target(printer_name)],
            "fieldConfig": {"defaults": {"min": 0, "max": 100, "unit": "percent",
                                         "thresholds": THRESHOLDS, "color": {"mode": "thresholds"}},
                            "overrides": overrides},
            "options": {"orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "minVizHeight": 28, "minVizWidth": 0, "namePlacement": "left",
                        "valueMode": "text", "text": COLOR_BAR_TEXT}})
        pid += 1; col += 1
        if col >= COLS_COLOR: col = 0; y += GROUP_H[0]
    if col != 0: y += GROUP_H[0]

# --- B&W printers: one gauge each, the printer name rendered big inside (Black
# is the only supply, so its dial carries the printer name). ---
if GROUPS[1]:
    row_header(GROUP_LABELS[1])
    col = 0
    for printer_name in GROUPS[1]:
        item_name = host_items[printer_name][0]["name"]
        panels.append({
            "id": pid, "type": "gauge", "datasource": DS, "title": "",
            "gridPos": {"h": GROUP_H[1], "w": W, "x": col * W, "y": y},
            "targets": [toner_target(printer_name)],
            "fieldConfig": {"defaults": {"min": 0, "max": 100, "unit": "percent",
                                         "thresholds": THRESHOLDS, "color": {"mode": "thresholds"}},
                            "overrides": [{"matcher": {"id": "byName", "options": item_name},
                                           "properties": [{"id": "displayName", "value": printer_name}]}]},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "showThresholdMarkers": True, "showThresholdLabels": False,
                        "text": BW_GAUGE_TEXT}})
        pid += 1; col += 1
        if col >= COLS: col = 0; y += GROUP_H[1]
    if col != 0: y += GROUP_H[1]

# --- Offline printers: a red OFFLINE block each ---
if GROUPS[2]:
    row_header(GROUP_LABELS[2])
    col = 0
    for printer_name in GROUPS[2]:
        panels.append({
            "id": pid, "type": "stat", "datasource": DS, "title": printer_name,
            "gridPos": {"h": GROUP_H[2], "w": W, "x": col * W, "y": y},
            "targets": [toner_target(printer_name)],
            "fieldConfig": {"defaults": {"noValue": "OFFLINE",
                                         "color": {"mode": "fixed", "fixedColor": "dark-red"},
                                         "thresholds": {"mode": "absolute", "steps": [{"color": "dark-red", "value": None}]}},
                            "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none",
                        "textMode": "value", "justifyMode": "center"}})
        pid += 1; col += 1
        if col >= COLS: col = 0; y += GROUP_H[2]
    if col != 0: y += GROUP_H[2]

dashboard = {
    "dashboard": {
        "uid": "printers-dashboard",
        "title": "Printer Monitoring",
        "tags": ["printers", "monitoring"],
        "timezone": "browser",
        "refresh": "30s",
        "panels": panels,
        "time": {"from": "now-1h", "to": "now"},
        "schemaVersion": 39,
        "liveNow": True
    },
    "overwrite": True
}

res = requests.post(f"{GRAFANA_URL}/dashboards/db", json=dashboard, auth=AUTH).json()
print("Dashboard updated:", res)
print(f"\nKiosk URL: http://localhost:3000/d/printers-dashboard/printer-monitoring?kiosk")
