from __future__ import annotations

import json
from pathlib import Path

from gsm.control.state import DEFAULT_STATE_DIR
from gsm.games.registry import GameDefinition, GamePort, register_game

LGSM_IMAGE = "gameservermanagers/gameserver"
LGSM_VOLUMES = ["/data"]
LGSM_DATA_PATHS = {
    "serverfiles": "/data/serverfiles",
    "log": "/data/log",
    "config": "/data/config-lgsm",
}

CATALOG_FILE = DEFAULT_STATE_DIR / "lgsm_catalog.json"
LGSM_DATA_FILE = DEFAULT_STATE_DIR / "lgsm_data.json"

# Game-specific config requirements not detectable from upstream data.
# Valheim requires a server password (min 5 chars) or it won't start.
_REQUIRED_CONFIG_OVERRIDES = {
    "vhserver": ("serverpassword",),
}
_lgsm_data: dict | None = None
_seeded = False


def _ensure_seeded() -> None:
    """Copy bundled JSON from package data to ~/.gsm/ if not already present."""
    global _seeded
    if _seeded:
        return
    DEFAULT_STATE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, target in [
        ("lgsm_catalog.json", CATALOG_FILE),
        ("lgsm_data.json", LGSM_DATA_FILE),
    ]:
        if not target.exists():
            from importlib.resources import files as pkg_files

            ref = pkg_files("gsm.games").joinpath(filename)
            target.write_text(ref.read_text(encoding="utf-8"), encoding="utf-8")
    _seeded = True


def _load_lgsm_data() -> dict:
    global _lgsm_data
    _ensure_seeded()
    if _lgsm_data is None:
        if LGSM_DATA_FILE.exists():
            _lgsm_data = json.loads(LGSM_DATA_FILE.read_text())
        else:
            _lgsm_data = {"games": {}}
    return _lgsm_data


def get_lgsm_config_options(server_code: str) -> dict[str, dict]:
    """Get config options for a LinuxGSM game from the synced JSON data."""
    data = _load_lgsm_data()
    game_data = data.get("games", {}).get(server_code, {})
    return game_data.get("config_options", {})


def _load_catalog() -> dict[str, dict]:
    """Load the catalog JSON file."""
    _ensure_seeded()
    if CATALOG_FILE.exists():
        return json.loads(CATALOG_FILE.read_text())
    return {}


def _parse_catalog_entry(name: str, entry: dict) -> tuple[str, GameDefinition]:
    """Parse a JSON catalog entry into a GameDefinition."""
    ports = [GamePort(port=p["port"], protocol=p["protocol"]) for p in entry["ports"]]
    server_code = entry["server_code"]
    rcon_port = entry.get("rcon_port")
    return name, GameDefinition(
        name=name,
        display_name=entry["display_name"],
        image=f"{LGSM_IMAGE}:{name.removeprefix('lgsm-')}",
        ports=ports,
        defaults=dict(entry.get("default_lgsm_config", {})),
        default_instance_type=entry["default_instance_type"],
        min_ram_gb=entry["min_ram_gb"],
        volumes=list(LGSM_VOLUMES),
        data_paths=dict(LGSM_DATA_PATHS),
        rcon_port=rcon_port,
        rcon_password_key="rconpassword" if rcon_port else None,
        lgsm_server_code=server_code,
        config_options=get_lgsm_config_options(server_code),
        disk_gb=entry.get("disk_gb", 100),
        required_config=tuple(dict.fromkeys(
            tuple(entry.get("required_config", []))
            + _REQUIRED_CONFIG_OVERRIDES.get(server_code, ())
        )),
    )


def load_catalog() -> dict[str, dict]:
    """Load the catalog JSON file (public API)."""
    return _load_catalog()


def make_game(name: str) -> GameDefinition:
    """Build a GameDefinition for a catalog entry by gsm name."""
    catalog = _load_catalog()
    if name not in catalog:
        raise KeyError(f"{name} not found in catalog")
    _, game = _parse_catalog_entry(name, catalog[name])
    return game


def register_lgsm_catalog() -> None:
    """Register all LinuxGSM games from the catalog JSON."""
    catalog = _load_catalog()
    for name, entry in catalog.items():
        _, game = _parse_catalog_entry(name, entry)
        register_game(game)
