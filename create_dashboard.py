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
PANEL_H = 5

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
# the Printers group. It stays empty while the fleet is healthy and fills with
# severity-coloured rows (paper jam / out of paper / low paper / door open) when
# a trigger fires -- the at-a-glance status a wall TV needs. This panel renders
# problems itself; the datasource routes to its problems handler only on an exact
# string match, so queryType must be "5" (Problems) -- not a number, not a name.
BANNER_H = 9
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
        "severityField": True, "statusField": True, "ackField": False,
        "descriptionField": True, "showTags": False, "problemTimeline": False,
        "highlightBackground": True, "sortProblems": "severity",
        "fontSize": "150%", "pageSize": 15, "layout": "table"
    }
})
pid += 1; y += BANNER_H

GROUP_LABELS = {0: "🎨 Color Printers", 1: "⬛ Black & White Printers", 2: "⛔ Offline"}

current_group = None
col = 0
for printer_name in ordered:
    items = host_items.get(printer_name, [])
    is_color = len(items) > 1
    is_offline = len(items) == 0

    # Emit a section header row whenever the printer group changes.
    g = sort_key(printer_name)[0]
    if g != current_group:
        if col != 0:  # close the partial last row of the previous group
            y += PANEL_H
            col = 0
        panels.append({
            "id": pid, "type": "row", "title": GROUP_LABELS[g], "collapsed": False,
            "gridPos": {"h": 1, "w": 24, "x": 0, "y": y}, "panels": []
        })
        pid += 1; y += 1
        current_group = g

    x = col * W

    if is_offline:
        panels.append({
            "id": pid, "type": "stat", "datasource": DS,
            "title": printer_name,
            "gridPos": {"h": PANEL_H, "w": W, "x": x, "y": y},
            "targets": [{
                "refId": "A",
                "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": zabbix_ds_uid},
                "queryType": 0, "group": {"filter": "/.*/"}, "host": {"filter": printer_name},
                "application": {"filter": ""}, "item": {"filter": "/Toner.*/"},
                "functions": [], "options": {"showDisabledItems": False}
            }],
            "fieldConfig": {
                "defaults": {
                    "noValue": "OFFLINE",
                    "color": {"mode": "fixed", "fixedColor": "dark-red"},
                    "thresholds": {"mode": "absolute", "steps": [{"color": "dark-red", "value": None}]}
                }, "overrides": []
            },
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "colorMode": "background", "graphMode": "none",
                "textMode": "value", "justifyMode": "center"
            }
        })
    elif is_color:
        # Extract toner code from item name to use as displayName
        overrides = []
        for item in items:
            # "Toner Level: Black Ink Supply Unit T01D1/T01C1" -> "Black (T01D1)"
            raw = item["name"].replace("Toner Level: ", "")
            parts = raw.split(" Ink Supply Unit ")
            color_name = parts[0] if parts else raw
            code = parts[1].split("/")[0] if len(parts) > 1 else ""
            display = f"{color_name} ({code})" if code else color_name
            overrides.append({
                "matcher": {"id": "byName", "options": item["name"]},
                "properties": [{"id": "displayName", "value": display}]
            })

        panels.append({
            "id": pid, "type": "bargauge", "datasource": DS,
            "title": printer_name,
            "gridPos": {"h": PANEL_H, "w": W, "x": x, "y": y},
            "targets": [{
                "refId": "A",
                "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": zabbix_ds_uid},
                "queryType": 0, "group": {"filter": "/.*/"}, "host": {"filter": printer_name},
                "application": {"filter": ""}, "item": {"filter": "/Toner.*/"},
                "functions": [], "options": {"showDisabledItems": False}
            }],
            "fieldConfig": {
                "defaults": {
                    "min": 0, "max": 100, "unit": "percent",
                    "thresholds": {"mode": "absolute", "steps": [
                        {"color": "red", "value": None},
                        {"color": "orange", "value": 10}, {"color": "yellow", "value": 20},
                        {"color": "green", "value": 35}
                    ]},
                    "color": {"mode": "thresholds"}
                },
                "overrides": overrides
            },
            "options": {
                "orientation": "horizontal", "displayMode": "gradient", "showUnfilled": True,
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "minVizHeight": 14, "minVizWidth": 0, "namePlacement": "left",
                "valueMode": "text"
            }
        })
    else:
        # B&W - extract code
        raw = items[0]["name"].replace("Toner Level: ", "")
        parts = raw.split(" Ink Supply Unit ")
        code = parts[1].split("/")[0] if len(parts) > 1 else ""
        label = f"Black ({code})" if code else "Black"

        panels.append({
            "id": pid, "type": "gauge", "datasource": DS,
            "title": printer_name,
            "gridPos": {"h": PANEL_H, "w": W, "x": x, "y": y},
            "targets": [{
                "refId": "A",
                "datasource": {"type": "alexanderzobnin-zabbix-datasource", "uid": zabbix_ds_uid},
                "queryType": 0, "group": {"filter": "/.*/"}, "host": {"filter": printer_name},
                "application": {"filter": ""}, "item": {"filter": "/Toner.*/"},
                "functions": [], "options": {"showDisabledItems": False}
            }],
            "fieldConfig": {
                "defaults": {
                    "min": 0, "max": 100, "unit": "percent",
                    "thresholds": {"mode": "absolute", "steps": [
                        {"color": "red", "value": None},
                        {"color": "orange", "value": 10}, {"color": "yellow", "value": 20},
                        {"color": "green", "value": 35}
                    ]},
                    "color": {"mode": "thresholds"}
                },
                "overrides": [{
                    "matcher": {"id": "byName", "options": items[0]["name"]},
                    "properties": [{"id": "displayName", "value": label}]
                }]
            },
            "options": {
                "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                "showThresholdMarkers": True, "showThresholdLabels": False
            }
        })

    pid += 1; col += 1
    if col >= COLS: col = 0; y += PANEL_H

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
