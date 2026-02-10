from gsm.games.lgsm_sync import build_catalog_entry, parse_game_server_settings


SAMPLE_CONFIG = """\
##################################
#### Game Server Settings ####
##################################

## Predefined Parameters | https://docs.linuxgsm.com/configuration/start-parameters
ip="0.0.0.0"
port="28015"
rconport="28016"
rconpassword="CHANGE_ME"
servername="LinuxGSM"
gamemode="vanilla"           # values: vanilla, softcore
serverlevel="Procedural Map" # values: Procedural Map, Barren, HapisIsland
maxplayers="50"
worldsize="3000"   # default: 3000, range: 1000-6000, map size in meters.
saveinterval="300" # Auto-save in seconds.
tickrate="30"      # default: 30, range: 15-100.

## Server Parameters | https://docs.linuxgsm.com/configuration/start-parameters#additional-parameters
startparameters="-batchmode +server.ip ${ip} +server.port ${port}"

##################################
#### LinuxGSM Settings ####
##################################

updateonstart="off"
"""


def test_parse_game_server_settings():
    options = parse_game_server_settings(SAMPLE_CONFIG)

    assert "ip" in options
    assert options["ip"]["default"] == "0.0.0.0"

    assert "port" in options
    assert options["port"]["default"] == "28015"

    assert "rconpassword" in options
    assert options["rconpassword"]["default"] == "CHANGE_ME"

    assert "servername" in options
    assert options["servername"]["default"] == "LinuxGSM"

    assert "gamemode" in options
    assert options["gamemode"]["default"] == "vanilla"
    assert "vanilla, softcore" in options["gamemode"]["description"]

    assert "worldsize" in options
    assert "1000-6000" in options["worldsize"]["description"]

    assert "saveinterval" in options
    assert "Auto-save" in options["saveinterval"]["description"]


def test_parse_skips_startparameters():
    options = parse_game_server_settings(SAMPLE_CONFIG)
    assert "startparameters" not in options


def test_parse_empty_string():
    options = parse_game_server_settings("")
    assert options == {}


def test_parse_no_game_server_section():
    text = """\
#### LinuxGSM Settings ####
updateonstart="off"
"""
    options = parse_game_server_settings(text)
    assert options == {}


def test_parse_no_end_marker():
    text = """\
#### Game Server Settings ####
ip="0.0.0.0"
port="28015"
"""
    options = parse_game_server_settings(text)
    assert "ip" in options
    assert "port" in options


def test_parse_empty_description():
    text = """\
#### Game Server Settings ####
ip="0.0.0.0"
#### LinuxGSM Settings ####
"""
    options = parse_game_server_settings(text)
    assert options["ip"]["description"] == ""


def test_build_catalog_entry_includes_required_config():
    """build_catalog_entry detects steamuser sentinel and includes required_config."""
    config_options = {
        "steamuser": {"default": "username", "description": ""},
        "port": {"default": "27015", "description": ""},
        "servername": {"default": "My Server", "description": ""},
    }
    row = {"gamename": "Test Game"}
    entry = build_catalog_entry("testserver", row, config_options)
    assert entry["required_config"] == ["steamuser"]


def test_build_catalog_entry_no_required_config():
    """build_catalog_entry returns empty required_config when no steamuser sentinel."""
    config_options = {
        "port": {"default": "27015", "description": ""},
        "servername": {"default": "My Server", "description": ""},
    }
    row = {"gamename": "Test Game"}
    entry = build_catalog_entry("testserver", row, config_options)
    assert entry["required_config"] == []
