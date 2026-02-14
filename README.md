```
                            _____ _____ __  __  _____
                           / ____|/ ____|  \/  |/ ____|
                          | |  __| (___ | \  / | |
                          | | |_ |\___ \| |\/| | |
                          | |__| |____) | |  | | |____
                           \_____|_____/|_|  |_|\_____|
```

# Game Server Maker

**Launch game servers on AWS in one command. No console clicking. No YAML files. No Terraform.**

![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Games: 130+](https://img.shields.io/badge/games-130+-orange)

<p align="center">
  <img src="demo.gif" alt="gsmc demo">
</p>

---

## ⚡ Features

- **One-command launch** — `gsmc launch factorio -n my-server` provisions EC2, installs Docker, opens ports, and starts your server
- **130+ games** — Built-in Docker games + 130+ via [LinuxGSM](https://linuxgsm.com/) integration
- **Pause & resume** — stop paying while you're not playing, pick up where you left off
- **Snapshots & cloning** — back up your world or clone a server from a snapshot
- **Elastic IPs** — pin a static address that survives pause/resume cycles
- **Full CLI + REST API** — manage from the terminal or run the optional FastAPI server

---

## 🚀 Quick Start

Setting up a game server sucks. You need an EC2 instance, Docker, security groups, port mappings, SSH keys, config files... or you could just:

**1. Install**

```bash
pipx install gsmc        # recommended
uv tool install gsmc     # if you use uv
pip install gsmc         # in a virtual environment
```

**2. Configure AWS** — set up credentials ([docs](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-files.html)) and apply the [minimum IAM policy](examples/iam-policy.json).

**3. Launch**

```bash
gsmc launch factorio -n my-server
```

**4. Play** — then manage the lifecycle:

```bash
gsmc list                      # see your servers
gsmc logs my-server            # check the output
gsmc rcon my-server /save      # send an RCON command

gsmc pause my-server           # stop paying, keep data
gsmc resume my-server          # pick up where you left off

gsmc pin my-server             # static IP across restarts
gsmc snapshot my-server        # back up the world
gsmc destroy my-server         # tear it down
```

---

## 🎮 Supported Games

### LinuxGSM games (130+)

gsmc integrates with [LinuxGSM](https://linuxgsm.com/) to support 130+ game servers. LinuxGSM games use a config-driven format — gsmc generates game definitions from upstream LinuxGSM data, each represented as a Python dataclass with ports, default settings, and instance sizing.

| Game | CLI name |
|------|----------|
| Garry's Mod | `lgsm-gmod` |
| ARK: Survival Evolved | `lgsm-arkse` |
| Counter-Strike 2 | `lgsm-cs2` |
| Team Fortress 2 | `lgsm-tf2` |
| Rust | `lgsm-rust` |
| 7 Days to Die | `lgsm-7dtd` |
| DayZ | `lgsm-dayz` |
| Project Zomboid | `lgsm-pz` |
| Palworld | `lgsm-pw` |

Run `gsmc games` to see the full list, or `gsmc sync --list` to browse all available LinuxGSM servers.

### Docker games (built-in)

These are hand-written game definitions that use community Docker images directly:

| Game | CLI name | Default instance | Approx. cost/hr |
|------|----------|-----------------|-----------------|
| Factorio | `factorio` | t3.medium | ~$0.04 |

### Adding your own game

Both LinuxGSM and Docker games are Python dataclasses in `src/gsm/games/`. To add a new Docker game, create a new file with a `GameDefinition` and call `register_game()`. To add a LinuxGSM game, use `gsmc sync --add <server_code>`. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## 📖 Usage Examples

```bash
# Factorio with default settings
gsmc launch factorio -n my-factory

# Docker game with a config file
gsmc config factorio --init -o factorio.cfg
# edit factorio.cfg...
gsmc launch factorio --config-file factorio.cfg

# LinuxGSM game with a config file
gsmc config lgsm-rust --init -o rust.cfg
# edit rust.cfg...
gsmc launch lgsm-rust --config-file rust.cfg

# Inline config (any game)
gsmc launch lgsm-gmod -c maxplayers=32 -c servername="My Server"
gsmc launch factorio -c GENERATE_NEW_SAVE=false

# Launch with a static IP that survives pause/resume
gsmc launch factorio --pin-ip -n persistent-server

# Clone a server from a snapshot
gsmc snapshot my-server
gsmc launch factorio --from-snapshot snap-abc123 -n my-server-clone

# Upload a file before launch
gsmc launch factorio -u ./saves/my-save.zip:/factorio/saves/my-save.zip
```

---

## ⚙️ Configuration Guide

Every game server has settings you'll want to tweak — player count, server name, passwords, world options, etc. gsmc gives you `gsmc config` to discover what's available, and flags on `gsmc launch` to set them.

All games are configured with `-c KEY=VALUE` on launch. Use `gsmc config <game>` to see what options are available.

```bash
# See what a game supports
gsmc config factorio
gsmc config lgsm-rust

# Override settings at launch (any game)
gsmc launch factorio -c GENERATE_NEW_SAVE=false -c SAVE_NAME=myworld
gsmc launch lgsm-gmod -c maxplayers=32 -c servername="My Server"
```

### Config files

For heavier customization, generate a config file, edit it, and pass it at launch:

```bash
# Generate a config file with defaults
gsmc config factorio --init -o factorio.env
gsmc config lgsm-rust --init -o rust.cfg

# Edit the file, then launch with it
gsmc launch factorio --config-file factorio.env
gsmc launch lgsm-rust --config-file rust.cfg
```

Both generate `.cfg` files with the game's defaults. LinuxGSM configs include commented extras with descriptions.

### Config precedence

Config values are merged in this order (last wins):

1. Game defaults
2. Config file values (`--config-file`)
3. Inline overrides (`-c key=value`)

### Required config

Some games require specific config keys to be set before they can launch (e.g. `steamuser` for games that need a Steam account to download). gsmc will tell you what's missing and how to provide it:

```bash
gsmc launch lgsm-dayz
# Error: Missing required config key 'steamuser'
#   Provide via: --config steamuser=VALUE
```

---

## 📋 CLI Reference

### `gsmc games`

List all supported games with their CLI names, instance types, and ports.

### Server Lifecycle

### `gsmc launch GAME`

Launch a game server on EC2.

| Flag | Description |
|------|-------------|
| `-n, --name TEXT` | Server name |
| `-t, --instance-type TEXT` | EC2 instance type |
| `-r, --region TEXT` | AWS region (default: us-east-1) |
| `-u, --upload LOCAL:REMOTE` | Upload file before launch (repeatable) |
| `--from-snapshot ID` | Launch from a snapshot |
| `-c, --config KEY=VALUE` | Config option (repeatable) |
| `--config-file PATH` | Config file |
| `--pin-ip` | Pin a static Elastic IP to this server |

### `gsmc list`

List all servers.

### `gsmc info SERVER`

Show detailed information for a server.

### `gsmc destroy SERVER`

Terminate a server and clean up AWS resources.

| Flag | Description |
|------|-------------|
| `--all` | Destroy all servers |
| `-y, --yes` | Skip confirmation |

### Pause & Resume

### `gsmc pause SERVER`

Stop the EC2 instance to save costs. Data persists on EBS.

### `gsmc resume SERVER`

Restart a paused or stopped server.

### Container Control

### `gsmc stop SERVER`

Stop the container (instance stays running).

### Elastic IPs

### `gsmc pin SERVER`

Pin a static Elastic IP to a server. Free while running, ~$3.65/month while paused.

### `gsmc unpin SERVER`

Release the pinned Elastic IP.

| Flag | Description |
|------|-------------|
| `-y, --yes` | Skip confirmation |

### `gsmc eips`

List all GSM-managed Elastic IPs.

| Flag | Description |
|------|-------------|
| `--cleanup` | Prompt to release orphaned EIPs |

### Snapshots

### `gsmc snapshot SERVER`

Create an EBS snapshot for backups or cloning.

### `gsmc snapshots`

List all snapshots.

### `gsmc snapshot-delete ID`

Delete a snapshot.

| Flag | Description |
|------|-------------|
| `-y, --yes` | Skip confirmation |

### Server Interaction

### `gsmc logs SERVER`

View container logs.

| Flag | Description |
|------|-------------|
| `-n, --tail LINES` | Number of lines to show |
| `-f, --follow` | Follow log output |

### `gsmc ssh SERVER`

SSH into the EC2 instance.

### `gsmc exec SERVER COMMAND...`

Run a command inside the server container.

### `gsmc rcon SERVER COMMAND...`

Send an RCON command to the server.

### `gsmc upload SERVER LOCAL REMOTE`

Upload a file to the server container.

### `gsmc download SERVER REMOTE LOCAL`

Download a file from the server container.

### Configuration

### `gsmc config GAME`

Show or generate configuration for a game. Shows available config options with defaults.

| Flag | Description |
|------|-------------|
| `--init` | Generate a local config file |
| `-o, --output PATH` | Output path (default: `<game>.cfg`) |

### LinuxGSM Catalog

### `gsmc sync`

Sync LinuxGSM game configs from upstream GitHub.

| Flag | Description |
|------|-------------|
| `--list` | List all available LinuxGSM games |
| `--add SERVER_CODE` | Add a game to the local catalog |
| `--all` | Add all games and sync configs |

### Other

### `gsmc help [COMMAND]`

Show help for a command.

### `gsmc completion SHELL`

Generate shell completion script (bash/zsh/fish).

### `gsmc api`

Start the local REST API server.

| Flag | Description |
|------|-------------|
| `-p, --port PORT` | API port (default: 8080) |
| `--host HOST` | API host (default: 127.0.0.1) |

---

## 🔧 How It Works

```
  you                gsmc              AWS
   │                  │                 │
   │  gsmc launch ..  │                 │
   │─────────────────>│  create EC2     │
   │                  │────────────────>│
   │                  │  SSH + Docker   │
   │                  │────────────────>│
   │                  │  open ports     │
   │                  │────────────────>│
   │                  │  start server   │
   │                  │────────────────>│
   │  IP + port       │                 │
   │<─────────────────│                 │
```

1. Provisions an EC2 instance running Amazon Linux 2023
2. Installs Docker via SSH
3. Pulls the game's Docker image
4. Creates security group rules for the game's ports
5. Launches the container with your config

### State files

State is tracked locally in `~/.gsm/`:

| File | Contents |
|------|----------|
| `servers.json` | Active server records |
| `snapshots.json` | Snapshot records |
| `keys/gsm-key.pem` | Auto-generated SSH key (shared across machines via SSM) |
| `lgsm_catalog.json` | LinuxGSM game catalog |
| `lgsm_data.json` | LinuxGSM config data |

### Reconciliation

`gsmc list` and `gsmc info` automatically reconcile local state with AWS before displaying results. If an instance was terminated externally (e.g. via the AWS console), gsmc detects this and removes the stale record. If an orphaned GSM-tagged instance is found running in AWS without a local record, gsmc adopts it back into state. This keeps your local view consistent with reality without requiring manual cleanup.

---

## 🐚 Shell Completion

Tab-complete server names, game names, snapshot IDs, and commands in your shell:

```bash
# bash
gsmc completion bash >> ~/.bashrc

# zsh
gsmc completion zsh >> ~/.zshrc

# fish
gsmc completion fish > ~/.config/fish/completions/gsmc.fish
```

---

## 🌐 REST API

Install with API support and start the server:

```bash
pipx install gsmc[api]          # recommended
uv tool install gsmc[api]       # if you use uv
pip install gsmc[api]           # in a virtual environment

gsmc api                         # default: 127.0.0.1:8080
gsmc api --port 9000 --host 0.0.0.0
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/servers` | List all servers |
| `GET` | `/servers/{id}` | Get server details |
| `POST` | `/servers` | Launch a server |
| `DELETE` | `/servers/{id}` | Destroy a server |
| `POST` | `/servers/{id}/pause` | Pause a server |
| `POST` | `/servers/{id}/stop` | Stop the container |
| `POST` | `/servers/{id}/resume` | Resume a server |
| `POST` | `/servers/{id}/pin` | Pin an Elastic IP |
| `POST` | `/servers/{id}/unpin` | Release the pinned IP |
| `POST` | `/servers/{id}/snapshot` | Create a snapshot |
| `GET` | `/snapshots` | List all snapshots |
| `DELETE` | `/snapshots/{id}` | Delete a snapshot |

---

## 💰 AWS Setup & Costs

### Prerequisites

- AWS credentials configured via `~/.aws/credentials`, environment variables, or an AWS profile
- The [minimum IAM policy](examples/iam-policy.json) applied to your user/role

Required permissions: EC2 instances, security groups, key pairs, EBS volumes/snapshots, Elastic IPs, SSM Parameter Store (for SSH key sharing across machines).

### Cost estimates

Costs depend on the instance type and region (us-east-1 on-demand pricing shown). You only pay while the instance is running — `gsmc pause` stops the meter.

| Instance type | vCPU | RAM | ~Cost/hr | Good for |
|---------------|------|-----|----------|----------|
| t3.medium | 2 | 4 GB | $0.04 | Factorio, most LinuxGSM games |
| t3.large | 2 | 8 GB | $0.08 | Larger servers |
| t3.xlarge | 4 | 16 GB | $0.17 | Rust, CS2, ARK |
| t3.2xlarge | 8 | 32 GB | $0.33 | High-population servers |

EBS storage (server data) costs ~$0.08/GB/month and persists while paused. Snapshots cost ~$0.05/GB/month. Elastic IPs are free while associated with a running instance, ~$3.65/month while the server is paused.

### Disclaimer

gsmc is a convenience tool, not infrastructure-as-code. It manages AWS resources (EC2 instances, EBS volumes, security groups, Elastic IPs, snapshots) on your behalf. While gsmc reconciles state automatically and does its best to track everything, **you should periodically check your AWS console** to ensure no orphaned resources are left behind. gsmc provides no guarantees — you are responsible for your AWS bill.

---

## 🛠️ Development

```bash
git clone <repo>
cd game_server_maker
uv sync --all-extras
uv run pytest tests/ -v
```

### Stack

- [Click](https://click.palletsprojects.com/) — CLI framework
- [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) — AWS SDK
- [Paramiko](https://www.paramiko.org/) — SSH
- [Rich](https://rich.readthedocs.io/) — Terminal formatting
- [rcon](https://pypi.org/project/rcon/) — RCON protocol
- [FastAPI](https://fastapi.tiangolo.com/) — REST API (optional)

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## 📄 License

[MIT](LICENSE)
