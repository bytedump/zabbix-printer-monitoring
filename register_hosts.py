#!/usr/bin/env python3
"""
Auto-register the printer fleet as Zabbix hosts pointing at the SNMP simulator.

For every printer in ``printers.json`` this creates a Zabbix host with an SNMP
interface aimed at the mock simulator (``MOCK_SNMP_HOST``, default ``snmp-sim``
on port 1161). For online printers it also creates the toner items whose names
match exactly what create_dashboard.py parses; offline demo printers get the
host but no items, so the dashboard renders them as OFFLINE.

Idempotent: a printer whose host already exists is skipped.

Run from the repo root, with the stack up:  python3 register_hosts.py
"""
import json
import os

import printer_mib as mib
import zbx

# Use the local real inventory if present, otherwise the bundled example.
SOURCE = "printers.json" if os.path.exists("printers.json") else "printers.example.json"
HOST_GROUP = "Printers"
SNMP_COMMUNITY_MACRO = "{$SNMP_COMMUNITY}"

# Where the SNMP interface points. Inside the compose network the simulator is
# reachable by service name; override for real printers or a different host.
MOCK_SNMP_HOST = os.environ.get("MOCK_SNMP_HOST", "snmp-sim")
SNMP_PORT = os.environ.get("MOCK_SNMP_PORT", "1161")

# Zabbix item type / value-type magic numbers (Zabbix 6.4 API).
ITEM_TYPE_SNMP = 20
VALUE_TYPE_UNSIGNED = 3
PREPROCESS_JAVASCRIPT = 21

# Zabbix trigger severities.
SEVERITY_WARNING = 2
SEVERITY_AVERAGE = 3
SEVERITY_HIGH = 4

# Paper conditions decoded from hrPrinterDetectedErrorState byte 0:
# (trigger label, bit mask, severity).
PAPER_TRIGGERS = [
    ("Paper jam", mib.PAPER_JAM, SEVERITY_HIGH),
    ("Out of paper", mib.PAPER_OUT, SEVERITY_HIGH),
    ("Door open", mib.DOOR_OPEN, SEVERITY_AVERAGE),
    ("Low paper", mib.PAPER_LOW, SEVERITY_WARNING),
]

# JavaScript preprocessing: reduce the SNMP octet string to the integer value of
# byte 0, tolerating the shapes net-snmp/Zabbix may return -- a "Hex-STRING:"
# dump, bare hex pairs, or a raw ASCII byte (e.g. 0x40 -> "@").
ERRORSTATE_DECODE_JS = (
    "var v=(value||'').trim();"
    "var m=v.match(/^Hex-STRING:\\s*([0-9A-Fa-f]{2})/);"
    "if(m){return parseInt(m[1],16);}"
    "v=v.replace(/^STRING:\\s*/,'');"
    "var h=v.match(/^([0-9A-Fa-f]{2})(\\s|$)/);"
    "if(h){return parseInt(h[1],16);}"
    "return v.length?v.charCodeAt(0):0;"
)


def ensure_group(auth):
    """Return the groupid of HOST_GROUP, creating it if needed."""
    existing = zbx.zapi("hostgroup.get", {"filter": {"name": [HOST_GROUP]}}, auth)
    if existing:
        return existing[0]["groupid"]
    return zbx.zapi("hostgroup.create", {"name": HOST_GROUP}, auth)["groupids"][0]


def create_host(auth, printer, groupid):
    """Create the host + SNMP interface; return (hostid, interfaceid)."""
    community = mib.community(printer["name"])
    result = zbx.zapi("host.create", {
        "host": printer["name"],
        "groups": [{"groupid": groupid}],
        "interfaces": [{
            "type": 2,        # SNMP
            "main": 1,
            "useip": 0,       # resolve by DNS (the compose service name)
            "ip": "",
            "dns": MOCK_SNMP_HOST,
            "port": SNMP_PORT,
            "details": {
                "version": 2,  # SNMPv2c
                "bulk": 1,
                "community": SNMP_COMMUNITY_MACRO,
            },
        }],
        "macros": [{"macro": SNMP_COMMUNITY_MACRO, "value": community}],
    }, auth)
    hostid = result["hostids"][0]
    interfaces = zbx.zapi("hostinterface.get", {"hostids": hostid}, auth)
    return hostid, interfaces[0]["interfaceid"]


def create_toner_items(auth, printer, hostid, interfaceid):
    """Create one SNMP toner item per supply, named to match the dashboard."""
    for supply in mib.supplies(printer):
        idx = supply["idx"]
        zbx.zapi("item.create", {
            "name": f"Toner Level: {supply['description']}",
            "key_": f"toner.level[{idx}]",
            "hostid": hostid,
            "type": ITEM_TYPE_SNMP,
            "snmp_oid": f"{mib.SUPPLIES_LEVEL}.{idx}",
            "value_type": VALUE_TYPE_UNSIGNED,
            "units": "%",
            "delay": "30s",
            "interfaceid": interfaceid,
        }, auth)


def create_errorstate_item(auth, hostid, interfaceid):
    """Create the SNMP item polling hrPrinterDetectedErrorState, decoding its
    first byte to an integer so triggers can bitand the paper bits."""
    zbx.zapi("item.create", {
        "name": "Printer Error State",
        "key_": "paper.errorstate",
        "hostid": hostid,
        "type": ITEM_TYPE_SNMP,
        "snmp_oid": mib.HR_PRINTER_DETECTED_ERROR_STATE,
        "value_type": VALUE_TYPE_UNSIGNED,
        "delay": "30s",
        "interfaceid": interfaceid,
        "preprocessing": [{
            "type": PREPROCESS_JAVASCRIPT,
            "params": ERRORSTATE_DECODE_JS,
            "error_handler": 0,
            "error_handler_params": "",
        }],
    }, auth)


def create_paper_triggers(auth, printer):
    """Create one trigger per paper condition, firing when its bit is set."""
    host = printer["name"]
    for label, mask, severity in PAPER_TRIGGERS:
        zbx.zapi("trigger.create", {
            "description": f"{label}: {{HOST.NAME}}",
            "expression": f"bitand(last(/{host}/paper.errorstate),{mask})={mask}",
            "priority": severity,
        }, auth)


def main():
    zbx.load_env()
    user = os.environ.get("ZABBIX_API_USER", "Admin")
    password = os.environ.get("ZABBIX_API_PASSWORD", "zabbix")

    auth = zbx.zapi("user.login", {"username": user, "password": password})
    groupid = ensure_group(auth)

    with open(SOURCE) as f:
        printers = json.load(f)

    created, skipped, offline = 0, 0, 0
    for printer in printers:
        name = printer["name"]
        if zbx.zapi("host.get", {"filter": {"host": [name]}}, auth):
            print(f"  exists, skipping: {name}")
            skipped += 1
            continue

        hostid, interfaceid = create_host(auth, printer, groupid)
        if mib.is_offline(name):
            offline += 1
            print(f"  host only (offline): {name}")
        else:
            create_toner_items(auth, printer, hostid, interfaceid)
            create_errorstate_item(auth, hostid, interfaceid)
            create_paper_triggers(auth, printer)
            created += 1
            print(f"  registered: {name} ({len(mib.supplies(printer))} toner items + paper alerts)")

    print(f"\nDone: {created} with items, {offline} offline hosts, {skipped} skipped.")


if __name__ == "__main__":
    main()
