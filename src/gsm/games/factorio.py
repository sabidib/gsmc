from gsm.games.registry import GameDefinition, GamePort, register_game

factorio = GameDefinition(
    name="factorio",
    display_name="Factorio",
    image="factoriotools/factorio",
    ports=[
        GamePort(port=34197, protocol="udp"),
        GamePort(port=27015, protocol="tcp"),
    ],
    defaults={
        "GENERATE_NEW_SAVE": "false",
        "SAVE_NAME": "GSMC Game",
        "LOAD_LATEST_SAVE": "true",
    },
    default_instance_type="t3.medium",
    min_ram_gb=2,
    volumes=["/factorio"],
    data_paths={
        "saves": "/factorio/saves",
        "config": "/factorio/config/server-settings.json",
        "mods": "/factorio/mods",
        "rcon_pw": "/factorio/config/rconpw",
    },
    rcon_port=27015,
)

register_game(factorio)
