# Contributing to Game Server Maker

Thanks for your interest in contributing! Whether you're adding a new game, fixing a bug, or improving docs, this guide will help you get started.

## Development Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/sabidib/gsmc.git
   cd game-server-maker
   ```

2. Install dependencies with [uv](https://docs.astral.sh/uv/):
   ```bash
   uv sync --all-extras
   ```

3. Run tests:
   ```bash
   uv run pytest tests/ -v
   ```

All tests must pass before submitting a PR. CI runs the same command on every push and pull request.

## Project Structure

```
src/gsm/
  games/
    registry.py          # GameDefinition dataclass + registry
    factorio.py          # Example: built-in Docker game
    lgsm_catalog.py      # LinuxGSM game loader (130+ games)
    lgsm_sync.py         # Sync configs from upstream LinuxGSM
  aws/
    ami.py               # AMI lookup
    ec2.py               # EC2 instance management
    ebs.py               # EBS snapshots
    eip.py               # Elastic IPs
    security_groups.py   # Security group management
  control/
    provisioner.py       # Orchestrates launch/pause/resume/destroy
    state.py             # Local state (servers.json, snapshots.json)
    ssh.py               # SSH client wrapper (paramiko)
    docker.py            # Remote Docker commands over SSH
  cli.py                 # Click CLI (all commands)
  api.py                 # FastAPI REST API
```

## Adding a New Docker Game

Docker games are hand-written definitions that wrap a community Docker image. Each game is a single Python file with a frozen `GameDefinition` dataclass.

### 1. Create the game file

Create `src/gsm/games/your_game.py`:

```python
from gsm.games.registry import GameDefinition, GamePort, register_game

your_game = GameDefinition(
    name="your-game",                          # CLI name (gsmc launch your-game)
    display_name="Your Game",                  # Human-readable name
    image="dockerhub-user/game-image:latest",  # Docker image to pull
    ports=[
        GamePort(port=27015, protocol="udp"),  # Game port (opened to 0.0.0.0)
        GamePort(port=27016, protocol="tcp"),  # RCON port, if applicable
    ],
    defaults={                                  # Default config (set via -c KEY=VALUE)
        "SERVER_NAME": "GSM Server",
        "MAX_PLAYERS": "10",
    },
    default_instance_type="t3.medium",         # EC2 instance type
    min_ram_gb=2,                              # Minimum RAM for the game
    volumes=["/data"],                         # Docker volumes to mount
    data_paths={                               # Paths inside the container
        "saves": "/data/saves",
        "config": "/data/config",
    },

    # Optional fields:
    rcon_port=27016,                           # RCON port number (None if no RCON)
    rcon_password_key="RCON_PASSWORD",           # Config key that sets the RCON password
    disk_gb=100,                               # EBS volume size (default: 100)
)

register_game(your_game)
```

### 2. Register the import

Add the import to `_load_games()` in `src/gsm/cli.py`:

```python
def _load_games():
    import gsm.games.factorio  # noqa: F401
    import gsm.games.your_game  # noqa: F401
    ...
```

Do the same in `src/gsm/api.py`'s `create_app()`.

### 3. Add tests

Add an entry to `EXPECTED_GAMES` in `tests/test_game_definitions.py`:

```python
EXPECTED_GAMES = {
    ...
    "your-game": {
        "module": "gsm.games.your_game",
        "image": "dockerhub-user/game-image:latest",
        "instance_type": "t3.medium",
        "rcon_port": 27016,
        "port_count": 2,
    },
}
```

This automatically generates three parameterized tests for your game (definition fields, volumes, registry).

### 4. Finding the right Docker image

Look for well-maintained community images on Docker Hub. Good signs:

- Active GitHub repo with recent commits
- Clear documentation of environment variables and ports
- Volume mounts for persistent data (saves, configs)
- At least a few hundred pulls

The image must accept configuration via environment variables and expose game ports. Users configure these at launch with `-c KEY=VALUE` or `--config-file`.

## Adding a LinuxGSM Game

LinuxGSM games are generated from upstream data and don't require writing Python code. Use the built-in sync commands:

```bash
# Browse all available LinuxGSM servers
gsmc sync --list

# Add a specific game
gsmc sync --add rustserver

# Sync config options (fetches defaults and descriptions)
gsmc sync

# Or add everything at once
gsmc sync --all
```

This updates `lgsm_catalog.json` and `lgsm_data.json` in `~/.gsm/`. LinuxGSM games automatically get ports, instance sizing, config options, and RCON support from the upstream data.

## GameDefinition Fields Reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `str` | Yes | CLI name, used in `gsmc launch <name>` |
| `display_name` | `str` | Yes | Human-readable name for display |
| `image` | `str` | Yes | Docker image to pull |
| `ports` | `list[GamePort]` | Yes | Ports to expose (opened in security group) |
| `defaults` | `dict[str, str]` | Yes | Default config options for this game |
| `default_instance_type` | `str` | Yes | EC2 instance type |
| `min_ram_gb` | `int` | Yes | Minimum RAM requirement |
| `volumes` | `list[str]` | Yes | Docker volume mount paths |
| `data_paths` | `dict[str, str]` | Yes | Named paths inside the container |
| `rcon_port` | `int \| None` | No | RCON port, if the game supports it |
| `rcon_password_key` | `str \| None` | No | Config key for RCON password (auto-generated if set) |
| `disk_gb` | `int` | No | EBS volume size in GB (default: 100) |
| `extra_docker_args` | `list[str]` | No | Additional `docker run` arguments |
| `password_keys` | `tuple[str, ...]` | No | Config keys that are passwords (displayed on launch) |
| `required_config` | `tuple[str, ...]` | No | Config keys that must be provided |

## Testing

Tests use [pytest](https://docs.pytest.org/) with [moto](https://github.com/getmoto/moto) for AWS mocking and `unittest.mock` for everything else. No real AWS calls are made during tests — a `conftest.py` fixture blocks any unmocked boto3 calls.

### Running tests

```bash
# All tests
uv run pytest tests/ -v

# A specific file
uv run pytest tests/test_provisioner.py -v

# A specific test
uv run pytest tests/test_provisioner.py::test_launch_server -v
```

### Writing tests

- **Game definitions** go in `tests/test_game_definitions.py` (add to `EXPECTED_GAMES`)
- **Provisioner logic** (launch, destroy, pause, resume) goes in `tests/test_provisioner*.py`
- **AWS operations** (EC2, EBS, EIP, SG) go in their respective `tests/test_*.py` files
- **CLI commands** go in `tests/test_cli*.py`
- Use the `make_server_record` and `make_snapshot_record` fixtures from `conftest.py` for mock data
- Use `mock_launch_deps` for tests that need all launch dependencies mocked
- Use `mock_remote_deps` for tests that need SSH/Docker dependencies mocked

## Submitting Changes

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Add or update tests as needed
4. Run `uv run pytest tests/ -v` and confirm all tests pass
5. Open a pull request against `main`

Keep PRs focused — one feature or fix per PR is easier to review.

## Reporting Issues

Open an issue at https://github.com/sabidib/gsmc
