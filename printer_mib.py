"""
Shared Printer-MIB (RFC 3805) layout used by the mock SNMP layer.

Both ``generate_snmprec.py`` (which writes the snmpsim data files) and
``register_hosts.py`` (which creates the matching Zabbix SNMP items) import
this module so the OID indexes, supply descriptions and the set of
demo-offline printers stay in sync. If the two scripts disagree on an index,
Zabbix would poll an OID the simulator never serves.
"""

# --- System group OIDs (.iso.org.dod.internet.mgmt.mib-2.system) ---
SYS_DESCR = "1.3.6.1.2.1.1.1.0"
SYS_OBJECT_ID = "1.3.6.1.2.1.1.2.0"
SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
SYS_CONTACT = "1.3.6.1.2.1.1.4.0"
SYS_NAME = "1.3.6.1.2.1.1.5.0"
SYS_LOCATION = "1.3.6.1.2.1.1.6.0"

# Epson SNMP enterprise OID (iso.org.dod.internet.private.enterprises.epson).
EPSON_ENTERPRISE_OID = "1.3.6.1.4.1.1248"

# --- prtMarkerSuppliesTable column OIDs (Printer-MIB, RFC 3805) ---
# Each column is indexed as <COLUMN>.<prtMarkerSuppliesIndex>; we use a single
# marker (sub-index 1) so the supply index is <COLUMN>.1.<n>.
SUPPLIES_DESCRIPTION = "1.3.6.1.2.1.43.11.1.1.6.1"  # prtMarkerSuppliesDescription
SUPPLIES_SUPPLY_UNIT = "1.3.6.1.2.1.43.11.1.1.7.1"  # prtMarkerSuppliesSupplyUnit
SUPPLIES_MAX_CAPACITY = "1.3.6.1.2.1.43.11.1.1.8.1"  # prtMarkerSuppliesMaxCapacity
SUPPLIES_LEVEL = "1.3.6.1.2.1.43.11.1.1.9.1"  # prtMarkerSuppliesLevel

# Per RFC 3805: when MaxCapacity is 100 the Level is expressed as a percentage,
# which is exactly the unit the Grafana gauges expect (0-100).
MAX_CAPACITY = 100

# Cartridge codes per ink colour, mirroring the "<code>/<code>" pattern that
# real Epson WorkForce units report (and that create_dashboard.py parses).
CARTRIDGE_CODES = {
    "Black": "T01D1/T01C1",
    "Cyan": "T01C2/T01B2",
    "Magenta": "T01C3/T01B3",
    "Yellow": "T01C4/T01B4",
}

# Colour printers expose the full CMYK set; mono printers expose Black only.
COLOR_SUPPLIES = ["Black", "Cyan", "Magenta", "Yellow"]
MONO_SUPPLIES = ["Black"]

# Demo-only: these printers get no snmpsim data file and no Zabbix items, so
# the dashboard renders them as OFFLINE (its "0 items" branch). This is a mock
# convenience and is intentionally NOT a field in printers.json (the real
# inventory should not carry a fake status).
OFFLINE_DEMO = {"Garage_BW", "Storage_BW"}

# Deterministic toner spread. Values are picked so the fleet hits every Grafana
# threshold band (green >50, yellow 30-50, orange 15-30, red <15) — useful for
# tuning the dashboard visuals without random noise between runs.
_LEVEL_SPREAD = [96, 88, 73, 61, 52, 44, 35, 28, 19, 12, 7, 3]


def community(name):
    """SNMP community string for a printer = snmpsim data-file basename."""
    return name.lower()


def is_offline(name):
    return name in OFFLINE_DEMO


def supplies(printer):
    """Return the ordered supply list for a printer.

    Each entry: {idx, color, code, description}. ``idx`` is the SNMP supply
    sub-index (1-based) and ``code`` is the cartridge code shown in Grafana.
    """
    colors = COLOR_SUPPLIES if printer.get("type") == "Color" else MONO_SUPPLIES
    result = []
    for i, color in enumerate(colors, start=1):
        full_code = CARTRIDGE_CODES[color]
        result.append({
            "idx": i,
            "color": color,
            "code": full_code.split("/")[0],
            "description": f"{color} Ink Supply Unit {full_code}",
        })
    return result


def level_for(printer_index, supply_idx):
    """Deterministic toner percentage for (printer position, supply index)."""
    return _LEVEL_SPREAD[(printer_index * 3 + supply_idx * 5) % len(_LEVEL_SPREAD)]
