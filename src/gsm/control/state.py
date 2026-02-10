import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".gsm"


@dataclass
class ServerRecord:
    id: str
    game: str
    name: str
    instance_id: str
    region: str
    public_ip: str
    ports: dict[str, int]
    status: str
    security_group_id: str
    launch_time: str = ""
    container_name: str = ""
    rcon_password: str = ""
    config: dict[str, str] = field(default_factory=dict)
    eip_allocation_id: str = ""
    eip_public_ip: str = ""

    def __post_init__(self):
        if not self.launch_time:
            self.launch_time = datetime.now(timezone.utc).isoformat()
        if not self.container_name:
            self.container_name = f"gsm-{self.game}-{self.id[:8]}"

    @property
    def connection_string(self) -> str:
        if self.ports:
            first_port = next(iter(self.ports.values()))
            return f"{self.public_ip}:{first_port}"
        return self.public_ip


class ServerState:
    def __init__(self, state_dir: Path = DEFAULT_STATE_DIR):
        self.state_dir = state_dir
        self.state_file = state_dir / "servers.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict]:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text())

    def _save_all(self, data: dict[str, dict]) -> None:
        self.state_file.write_text(json.dumps(data, indent=2))

    def save(self, record: ServerRecord) -> None:
        data = self._load()
        data[record.id] = asdict(record)
        self._save_all(data)

    def get(self, server_id: str) -> ServerRecord | None:
        data = self._load()
        if server_id in data:
            return ServerRecord(**data[server_id])
        return None

    def get_by_name_or_id(self, name_or_id: str) -> ServerRecord | None:
        data = self._load()
        if name_or_id in data:
            return ServerRecord(**data[name_or_id])
        for record_data in data.values():
            if record_data.get("name") == name_or_id:
                return ServerRecord(**record_data)
        for sid, record_data in data.items():
            if sid.startswith(name_or_id):
                return ServerRecord(**record_data)
        return None

    def list_all(self) -> list[ServerRecord]:
        data = self._load()
        return [ServerRecord(**v) for v in data.values()]

    def delete(self, server_id: str) -> None:
        data = self._load()
        data.pop(server_id, None)
        self._save_all(data)

    def update_status(self, server_id: str, status: str) -> None:
        data = self._load()
        if server_id in data:
            data[server_id]["status"] = status
            self._save_all(data)

    def name_exists(self, name: str) -> bool:
        data = self._load()
        return any(r.get("name") == name for r in data.values())

    def update_field(self, server_id: str, field: str, value) -> None:
        data = self._load()
        if server_id in data:
            data[server_id][field] = value
            self._save_all(data)


@dataclass
class SnapshotRecord:
    id: str
    snapshot_id: str
    game: str
    server_name: str
    server_id: str
    region: str
    status: str
    created_at: str = ""
    config: dict[str, str] = field(default_factory=dict)
    rcon_password: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()


class SnapshotState:
    def __init__(self, state_dir: Path = DEFAULT_STATE_DIR):
        self.state_dir = state_dir
        self.state_file = state_dir / "snapshots.json"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, dict]:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text())

    def _save_all(self, data: dict[str, dict]) -> None:
        self.state_file.write_text(json.dumps(data, indent=2))

    def save(self, record: SnapshotRecord) -> None:
        data = self._load()
        data[record.id] = asdict(record)
        self._save_all(data)

    def get(self, snapshot_id: str) -> SnapshotRecord | None:
        data = self._load()
        if snapshot_id in data:
            return SnapshotRecord(**data[snapshot_id])
        return None

    def list_all(self) -> list[SnapshotRecord]:
        data = self._load()
        return [SnapshotRecord(**v) for v in data.values()]

    def delete(self, snapshot_id: str) -> None:
        data = self._load()
        data.pop(snapshot_id, None)
        self._save_all(data)
