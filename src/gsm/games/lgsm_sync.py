"""Sync LinuxGSM config data from upstream GitHub.

Fetches _default.cfg files for games in our catalog and writes
lgsm_data.json with parsed config options.
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.request import urlopen

import gsm.games.lgsm_catalog as _cat

SERVERLIST_URL = (
    "https://raw.githubusercontent.com/GameServerManagers/LinuxGSM"
    "/master/lgsm/data/serverlist.csv"
)
CONFIG_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/GameServerManagers/LinuxGSM"
    "/master/lgsm/config-default/config-lgsm/{server_code}/_default.cfg"
)


def fetch_text(url: str) -> str:
    """Fetch text content from a URL."""
    with urlopen(url, timeout=30) as resp:
        return resp.read().decode("utf-8")


def fetch_serverlist() -> list[dict[str, str]]:
    """Fetch and parse serverlist.csv from LinuxGSM GitHub."""
    text = fetch_text(SERVERLIST_URL)
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def parse_game_server_settings(text: str) -> dict[str, dict]:
    """Parse the Game Server Settings section of _default.cfg.

    Extracts key="value" pairs between "#### Game Server Settings ####"
    and "#### LinuxGSM Settings ####", with inline # comments as descriptions.
    Skips startparameters (composite variable, not user-facing).
    """
    start_marker = "#### Game Server Settings ####"
    end_marker = "#### LinuxGSM Settings ####"

    start_idx = text.find(start_marker)
    if start_idx == -1:
        return {}

    end_idx = text.find(end_marker, start_idx)
    section = text[start_idx:end_idx] if end_idx != -1 else text[start_idx:]

    options = {}
    pattern = re.compile(r'^(\w+)="([^"]*)"(?:\s*#\s*(.*))?$')

    for line in section.splitlines():
        line = line.strip()
        m = pattern.match(line)
        if not m:
            continue
        key, value, comment = m.group(1), m.group(2), m.group(3)
        if key == "startparameters":
            continue
        options[key] = {
            "default": value,
            "description": (comment or "").strip(),
        }

    return options


def fetch_game_config(server_code: str) -> dict[str, dict] | None:
    """Fetch and parse config options for a single game."""
    url = CONFIG_URL_TEMPLATE.format(server_code=server_code)
    try:
        text = fetch_text(url)
    except HTTPError as e:
        if e.code == 404:
            return None
        raise
    return parse_game_server_settings(text)


def build_catalog_entry(
    server_code: str, row: dict[str, str], config_options: dict[str, dict],
) -> dict:
    """Build a catalog JSON entry from serverlist row and parsed config."""
    game_name = row["gamename"]

    ports = []
    for key in ("port", "queryport", "rconport", "appport"):
        if key in config_options:
            port_val = config_options[key]["default"]
            if port_val.isdigit():
                proto = "tcp" if key == "rconport" else "udp"
                ports.append({"port": int(port_val), "protocol": proto})

    rcon_port_str = config_options.get("rconport", {}).get("default", "")
    rcon_port = int(rcon_port_str) if rcon_port_str.isdigit() else None

    default_config = {}
    for key in ("servername", "maxplayers"):
        if key in config_options:
            default_config[key] = config_options[key]["default"]

    required_config = [
        key for key, opt in config_options.items()
        if key == "steamuser" and opt.get("default") == "username"
    ]

    return {
        "server_code": server_code,
        "display_name": f"{game_name} (LinuxGSM)",
        "ports": ports,
        "default_instance_type": "t3.medium",
        "min_ram_gb": 2,
        "rcon_port": rcon_port,
        "default_lgsm_config": default_config,
        "required_config": required_config,
    }


def load_catalog() -> dict:
    """Load lgsm_catalog.json."""
    _cat._ensure_seeded()
    if _cat.CATALOG_FILE.exists():
        return json.loads(_cat.CATALOG_FILE.read_text())
    return {}


def save_catalog(catalog: dict) -> None:
    """Write lgsm_catalog.json."""
    _cat._ensure_seeded()
    _cat.CATALOG_FILE.write_text(json.dumps(catalog, indent=2) + "\n")


def get_catalog_server_codes(catalog: dict) -> dict[str, str]:
    """Return {server_code: gsm_name} from catalog dict."""
    return {entry["server_code"]: name for name, entry in catalog.items()}


def sync_all_configs(catalog: dict, console) -> int:
    """Fetch configs for all catalog games and write lgsm_data.json.

    Returns the number of games synced.
    """
    server_codes = get_catalog_server_codes(catalog)
    serverlist = fetch_serverlist()
    serverlist_lookup = {row["gameservername"]: row for row in serverlist}

    data = {
        "_generated": datetime.now(timezone.utc).isoformat(),
        "_source": "https://github.com/GameServerManagers/LinuxGSM",
        "games": {},
    }

    synced = 0
    for server_code, gsm_name in sorted(server_codes.items()):
        console.print(f"  Fetching {server_code}...", end=" ")
        config_options = fetch_game_config(server_code)
        if config_options is None:
            console.print("NOT FOUND (skipped)")
            continue

        row = serverlist_lookup.get(server_code, {})
        data["games"][server_code] = {
            "shortname": row.get("shortname", ""),
            "gamename": row.get("gamename", ""),
            "config_options": config_options,
        }
        console.print(f"{len(config_options)} options")
        synced += 1

    _cat.LGSM_DATA_FILE.write_text(json.dumps(data, indent=2) + "\n")
    return synced


def add_game_to_catalog(
    catalog: dict, server_code: str, serverlist: list[dict[str, str]],
) -> tuple[str, dict] | str:
    """Add a game to the catalog.

    Returns (gsm_name, entry) on success, or an error message string.
    """
    for name, entry in catalog.items():
        if entry["server_code"] == server_code:
            return f"{server_code} already in catalog as '{name}'"

    row = next((r for r in serverlist if r["gameservername"] == server_code), None)
    if not row:
        return f"{server_code} not found in LinuxGSM serverlist"

    config_options = fetch_game_config(server_code)
    if config_options is None:
        return f"No _default.cfg found for {server_code}"

    gsm_name = f"lgsm-{row['shortname']}"
    entry = build_catalog_entry(server_code, row, config_options)
    catalog[gsm_name] = entry
    return (gsm_name, entry)


def add_all_games(catalog: dict, console) -> tuple[int, int]:
    """Add all LinuxGSM games to the catalog.

    Returns (added_count, skipped_count).
    """
    existing_codes = {e["server_code"] for e in catalog.values()}
    serverlist = fetch_serverlist()

    console.print(
        f"Found {len(serverlist)} games in LinuxGSM, "
        f"{len(catalog)} already in catalog\n"
    )

    added = 0
    skipped = 0

    for row in serverlist:
        server_code = row["gameservername"]
        gsm_name = f"lgsm-{row['shortname']}"

        if server_code in existing_codes:
            continue

        console.print(f"  Adding {server_code}...", end=" ")
        config_options = fetch_game_config(server_code)
        if config_options is None:
            console.print("no config (skipped)")
            skipped += 1
            continue

        catalog[gsm_name] = build_catalog_entry(server_code, row, config_options)
        existing_codes.add(server_code)
        added += 1
        console.print(f"{row['gamename']} ({len(config_options)} options)")

    return added, skipped
