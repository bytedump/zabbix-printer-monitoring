#!/usr/bin/env python3
"""
Generate snmpsim data files (.snmprec) that fake a fleet of network printers.

Reads ``printers.json`` and writes one ``mock/snmp/data/<community>.snmprec``
per online printer, exposing the System group plus the Printer-MIB
prtMarkerSuppliesTable (toner levels). Offline demo printers (see
``printer_mib.OFFLINE_DEMO``) get no file, so snmpsim returns nothing for them
and Zabbix sees them as unreachable.

snmpsim selects a data file by SNMP community (the file basename), so each
printer is reached with community == its lowercased name.

Run from the repo root:  python3 generate_snmprec.py
"""
import json
import os

import printer_mib as mib

DATA_DIR = os.path.join("mock", "snmp", "data")
# Use the local real inventory if present, otherwise the bundled example.
SOURCE = "printers.json" if os.path.exists("printers.json") else "printers.example.json"

# snmprec value tags (ASN.1 / SNMP types snmpsim understands).
TAG_INTEGER = 2
TAG_OCTET_STRING = 4
TAG_OCTET_STRING_HEX = "4x"  # OCTET STRING whose value is hex-encoded bytes
TAG_OBJECT_ID = 6
TAG_TIME_TICKS = 67


def build_records(printer, printer_index):
    """Return a list of (oid, tag, value) tuples for one printer."""
    records = [
        (mib.SYS_DESCR, TAG_OCTET_STRING, printer.get("model", "Network Printer")),
        (mib.SYS_OBJECT_ID, TAG_OBJECT_ID, mib.EPSON_ENTERPRISE_OID),
        # ~11.5 days of uptime in TimeTicks (1/100 s); fixed for reproducibility.
        (mib.SYS_UPTIME, TAG_TIME_TICKS, 99_999_999),
        (mib.SYS_CONTACT, TAG_OCTET_STRING, "IT Support"),
        (mib.SYS_NAME, TAG_OCTET_STRING, printer["name"]),
        (mib.SYS_LOCATION, TAG_OCTET_STRING, "Office"),
    ]
    for supply in mib.supplies(printer):
        idx = supply["idx"]
        level = mib.level_for(printer_index, idx)
        records += [
            (f"{mib.SUPPLIES_DESCRIPTION}.{idx}", TAG_OCTET_STRING, supply["description"]),
            (f"{mib.SUPPLIES_SUPPLY_UNIT}.{idx}", TAG_INTEGER, 19),  # 19 = percent
            (f"{mib.SUPPLIES_MAX_CAPACITY}.{idx}", TAG_INTEGER, mib.MAX_CAPACITY),
            (f"{mib.SUPPLIES_LEVEL}.{idx}", TAG_INTEGER, level),
        ]
    # Device-level error state (paper jam / out / low / door) as a 1-byte hex
    # bit field. 0x00 means no error; demo bytes come from printer_mib.
    error_byte = mib.error_state_byte(printer["name"])
    records.append(
        (mib.HR_PRINTER_DETECTED_ERROR_STATE, TAG_OCTET_STRING_HEX, f"{error_byte:02x}")
    )
    return records


def render(records):
    """Render records to snmprec text. snmpsim requires OIDs in ascending
    numeric order, so we sort by the OID interpreted as a tuple of ints."""
    records.sort(key=lambda r: [int(part) for part in r[0].split(".")])
    return "".join(f"{oid}|{tag}|{value}\n" for oid, tag, value in records)


def main():
    with open(SOURCE) as f:
        printers = json.load(f)

    os.makedirs(DATA_DIR, exist_ok=True)

    written, skipped = 0, 0
    for index, printer in enumerate(printers):
        name = printer["name"]
        if mib.is_offline(name):
            skipped += 1
            print(f"  offline (no data): {name}")
            continue
        path = os.path.join(DATA_DIR, f"{mib.community(name)}.snmprec")
        with open(path, "w") as f:
            f.write(render(build_records(printer, index)))
        written += 1
        print(f"  wrote {path}")

    print(f"\nDone: {written} online printers, {skipped} offline. Data dir: {DATA_DIR}")


if __name__ == "__main__":
    main()
