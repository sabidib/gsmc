from gsm.games.registry import GameDefinition, GamePort, get_game, list_games


def test_game_definition_has_required_fields():
    game = GameDefinition(
        name="test-game",
        display_name="Test Game",
        image="test/image:latest",
        ports=[GamePort(port=25565, protocol="tcp")],
        defaults={"EULA": "TRUE"},
        default_instance_type="t3.medium",
        min_ram_gb=2,
        volumes=["/data"],
        data_paths={"saves": "/data/world"},
    )
    assert game.name == "test-game"
    assert game.ports[0].port == 25565
    assert game.rcon_port is None


def test_game_port_docker_publish_format():
    port = GamePort(port=25565, protocol="tcp")
    assert port.docker_publish() == "25565:25565/tcp"


def test_game_port_docker_publish_udp():
    port = GamePort(port=28015, protocol="udp")
    assert port.docker_publish() == "28015:28015/udp"


def test_list_games_empty_initially():
    games = list_games()
    assert isinstance(games, list)


def test_register_and_get_game():
    game = GameDefinition(
        name="test-register",
        display_name="Test Register",
        image="test/image:latest",
        ports=[GamePort(port=1234, protocol="tcp")],
        defaults={},
        default_instance_type="t3.micro",
        min_ram_gb=1,
        volumes=[],
        data_paths={},
    )
    from gsm.games.registry import _registry
    _registry[game.name] = game
    found = get_game("test-register")
    assert found is not None
    assert found.name == "test-register"
    del _registry["test-register"]


def test_get_game_not_found():
    result = get_game("nonexistent-game")
    assert result is None
