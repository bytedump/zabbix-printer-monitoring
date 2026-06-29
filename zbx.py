"""
Small shared helpers for talking to the Zabbix JSON-RPC API.

Used by both create_dashboard.py and register_hosts.py so the .env loading and
error handling live in one place. ``zapi`` raises a clear RuntimeError on an
API-level error instead of silently returning None and blowing up later.
"""
import os

import requests

DEFAULT_ZABBIX_URL = "http://localhost:8080/api_jsonrpc.php"


def load_env(path=".env"):
    """Load KEY=VALUE pairs from a .env file into os.environ (if present)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                os.environ[key] = value


def zabbix_url():
    return os.environ.get("ZABBIX_URL", DEFAULT_ZABBIX_URL)


def zapi(method, params, auth=None, url=None):
    """Call a Zabbix API method and return its ``result``.

    Raises RuntimeError on transport failure or a JSON-RPC ``error`` object,
    with the Zabbix message/data attached for debugging.
    """
    payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    if auth:
        payload["auth"] = auth

    try:
        response = requests.post(url or zabbix_url(), json=payload, timeout=30)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"Zabbix API request failed on '{method}': {exc}") from exc

    data = response.json()
    if "error" in data:
        err = data["error"]
        raise RuntimeError(
            f"Zabbix API error on '{method}': {err.get('message')} {err.get('data')}"
        )
    return data.get("result")
