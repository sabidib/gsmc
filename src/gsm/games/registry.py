from dataclasses import dataclass, field


@dataclass(frozen=True)
class GamePort:
    port: int
    protocol: str  # "tcp" or "udp"

    def docker_publish(self) -> str:
        return f"{self.port}:{self.port}/{self.protocol}"

    def sg_rule(self) -> dict:
        return {
            "IpProtocol": self.protocol,
            "FromPort": self.port,
            "ToPort": self.port,
            "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": f"Game port {self.port}"}],
        }


@dataclass(frozen=True)
class GameDefinition:
    name: str
    display_name: str
    image: str
    ports: list[GamePort]
    defaults: dict[str, str]
    default_instance_type: str
    min_ram_gb: int
    volumes: list[str]
    data_paths: dict[str, str]
    rcon_port: int | None = None
    rcon_password_key: str | None = None
    extra_docker_args: list[str] = field(default_factory=list)
    lgsm_server_code: str | None = None
    config_options: dict[str, dict] = field(default_factory=dict)
    password_keys: tuple[str, ...] = ()
    disk_gb: int = 100
    required_config: tuple[str, ...] = ()


_registry: dict[str, GameDefinition] = {}


def register_game(game: GameDefinition) -> None:
    _registry[game.name] = game


def get_game(name: str) -> GameDefinition | None:
    return _registry.get(name)


def list_games() -> list[GameDefinition]:
    return list(_registry.values())
