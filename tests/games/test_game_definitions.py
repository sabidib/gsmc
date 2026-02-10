import pytest

from gsm.games.registry import GameDefinition, GamePort


EXPECTED_GAMES = {
    "factorio": {
        "module": "gsm.games.factorio",
        "image": "factoriotools/factorio",
        "instance_type": "t3.medium",
        "rcon_port": 27015,
        "port_count": 2,
    },
}


@pytest.mark.parametrize("game_name,expected", EXPECTED_GAMES.items())
def test_game_definition(game_name, expected):
    import importlib
    mod = importlib.import_module(expected["module"])
    game_attr = game_name.replace("-", "_")
    game: GameDefinition = getattr(mod, game_attr)
    assert game.name == game_name
    assert game.image == expected["image"]
    assert game.default_instance_type == expected["instance_type"]
    assert game.rcon_port == expected["rcon_port"]
    assert len(game.ports) == expected["port_count"]


@pytest.mark.parametrize("game_name,expected", EXPECTED_GAMES.items())
def test_game_has_volumes(game_name, expected):
    import importlib
    mod = importlib.import_module(expected["module"])
    game_attr = game_name.replace("-", "_")
    game: GameDefinition = getattr(mod, game_attr)
    assert len(game.volumes) > 0


@pytest.mark.parametrize("game_name,expected", EXPECTED_GAMES.items())
def test_game_registered(game_name, expected):
    import importlib
    importlib.import_module(expected["module"])
    from gsm.games.registry import get_game
    assert get_game(game_name) is not None
