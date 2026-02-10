from gsm.games.registry import _registry, get_game
from gsm.games.lgsm_catalog import (
    LGSM_IMAGE,
    LGSM_VOLUMES,
    LGSM_DATA_PATHS,
    get_lgsm_config_options,
    load_catalog,
    make_game,
    register_lgsm_catalog,
)


def test_make_game_sets_image():
    game = make_game("lgsm-rust")
    assert game.image == f"{LGSM_IMAGE}:rust"


def test_make_game_sets_defaults():
    game = make_game("lgsm-rust")
    assert "servername" in game.defaults


def test_make_game_sets_lgsm_server_code():
    game = make_game("lgsm-rust")
    assert game.lgsm_server_code == "rustserver"


def test_make_game_uses_standard_volumes():
    game = make_game("lgsm-rust")
    assert game.volumes == list(LGSM_VOLUMES)


def test_make_game_uses_standard_data_paths():
    game = make_game("lgsm-rust")
    assert game.data_paths == dict(LGSM_DATA_PATHS)


def test_make_game_sets_ports():
    game = make_game("lgsm-rust")
    assert len(game.ports) == 3
    assert game.ports[0].port == 28015
    assert game.ports[0].protocol == "udp"


def test_make_game_sets_rcon_port():
    game = make_game("lgsm-rust")
    assert game.rcon_port == 28016


def test_make_game_no_rcon_port():
    game = make_game("lgsm-gmod")
    assert game.rcon_port is None


def test_register_lgsm_catalog_populates_registry():
    catalog = load_catalog()
    saved = dict(_registry)
    try:
        register_lgsm_catalog()
        for name in catalog:
            assert get_game(name) is not None, f"{name} not registered"
            assert get_game(name).lgsm_server_code is not None
    finally:
        for name in catalog:
            _registry.pop(name, None)
        _registry.update(saved)


def test_catalog_has_expected_games():
    catalog = load_catalog()
    # Catalog should have games and include some known entries
    assert len(catalog) > 0
    for name in ("lgsm-rust", "lgsm-csgo", "lgsm-gmod"):
        assert name in catalog, f"{name} missing from catalog"
    # All names should follow the lgsm- prefix convention
    for name in catalog:
        assert name.startswith("lgsm-"), f"unexpected name: {name}"


def test_existing_games_have_no_lgsm_code():
    from gsm.games.factorio import factorio
    assert factorio.lgsm_server_code is None


def test_catalog_entries_have_default_config():
    catalog = load_catalog()
    entry = catalog["lgsm-rust"]
    assert "servername" in entry["default_lgsm_config"]
    assert "maxplayers" in entry["default_lgsm_config"]
    assert "rconpassword" in entry["default_lgsm_config"]


def test_make_game_copies_default_config():
    catalog = load_catalog()
    game = make_game("lgsm-rust")
    assert game.defaults == catalog["lgsm-rust"]["default_lgsm_config"]
    # Verify it's a copy, not the same object
    assert game.defaults is not catalog["lgsm-rust"]["default_lgsm_config"]


def test_game_definition_has_config_options():
    """Verify lgsm-rust GameDefinition has config_options loaded from JSON."""
    import gsm.games.lgsm_catalog as cat

    fake_data = {
        "games": {
            "rustserver": {
                "shortname": "rust",
                "gamename": "Rust",
                "config_options": {
                    "ip": {"default": "0.0.0.0", "description": ""},
                    "port": {"default": "28015", "description": ""},
                    "maxplayers": {"default": "50", "description": ""},
                },
            }
        }
    }
    original = cat._lgsm_data
    try:
        cat._lgsm_data = fake_data
        game = make_game("lgsm-rust")
        assert "ip" in game.config_options
        assert "port" in game.config_options
        assert game.config_options["ip"]["default"] == "0.0.0.0"
    finally:
        cat._lgsm_data = original


def test_get_lgsm_config_options_missing_game():
    """get_lgsm_config_options returns empty dict for unknown server code."""
    import gsm.games.lgsm_catalog as cat

    original = cat._lgsm_data
    try:
        cat._lgsm_data = {"games": {}}
        assert get_lgsm_config_options("nonexistent") == {}
    finally:
        cat._lgsm_data = original


def test_make_game_populates_required_config():
    """make_game sets required_config when catalog entry has steamuser sentinel."""
    import gsm.games.lgsm_catalog as cat

    fake_data = {
        "games": {
            "acserver": {
                "shortname": "ac",
                "gamename": "Assetto Corsa",
                "config_options": {
                    "steamuser": {"default": "username", "description": ""},
                    "servername": {"default": "LinuxGSM", "description": ""},
                },
            }
        }
    }
    # Patch catalog to include required_config
    catalog = load_catalog()
    original_entry = catalog["lgsm-ac"]
    patched_entry = dict(original_entry, required_config=["steamuser"])
    original_catalog = cat._load_catalog

    def fake_load():
        c = original_catalog()
        c["lgsm-ac"] = patched_entry
        return c

    original_lgsm = cat._lgsm_data
    try:
        cat._lgsm_data = fake_data
        cat._load_catalog = fake_load
        game = make_game("lgsm-ac")
        assert game.required_config == ("steamuser",)
    finally:
        cat._lgsm_data = original_lgsm
        cat._load_catalog = original_catalog


def test_valheim_requires_serverpassword():
    """Valheim has serverpassword in required_config from code overrides."""
    game = make_game("lgsm-vh")
    assert "serverpassword" in game.required_config
