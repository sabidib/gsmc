import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from botocore.exceptions import ClientError

from gsm.control.state import ServerRecord, SnapshotRecord


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "uses_moto: test uses moto @mock_aws (allows boto3 calls)"
    )


@pytest.fixture(autouse=True)
def _block_real_aws(request, monkeypatch):
    """Prevent any test from making real AWS API calls."""
    if request.node.get_closest_marker("uses_moto"):
        return

    def _blocked_client(service, *a, **kw):
        raise RuntimeError(
            f"Unmocked boto3.client('{service}') call! "
            f"Add a @patch or fixture mock for this AWS call."
        )

    def _blocked_resource(service, *a, **kw):
        raise RuntimeError(
            f"Unmocked boto3.resource('{service}') call! "
            f"Add a @patch or fixture mock for this AWS call."
        )

    monkeypatch.setattr(boto3, "client", _blocked_client)
    monkeypatch.setattr(boto3, "resource", _blocked_resource)


@pytest.fixture(autouse=True)
def _isolate_game_data(tmp_path, monkeypatch):
    """Redirect catalog/data paths to tmp_path so tests never touch ~/.gsm/."""
    import gsm.games.lgsm_catalog as cat

    data_dir = tmp_path / "gsm_data"
    data_dir.mkdir()

    src_dir = Path(__file__).parent.parent / "src" / "gsm" / "games"
    shutil.copy(src_dir / "lgsm_catalog.json", data_dir)
    shutil.copy(src_dir / "lgsm_data.json", data_dir)

    monkeypatch.setattr(cat, "CATALOG_FILE", data_dir / "lgsm_catalog.json")
    monkeypatch.setattr(cat, "LGSM_DATA_FILE", data_dir / "lgsm_data.json")
    monkeypatch.setattr(cat, "_seeded", True)
    monkeypatch.setattr(cat, "_lgsm_data", None)


# ── Record factories ──


@pytest.fixture
def make_server_record():
    """Factory for ServerRecord with sensible defaults. Override any field via kwargs."""
    def _make(**overrides):
        defaults = dict(
            id="srv-1", game="factorio", name="fact-test",
            instance_id="i-test123", region="us-east-1",
            public_ip="54.1.2.3", ports={"34197/udp": 34197},
            status="running", security_group_id="sg-test123",
            container_name="gsm-factorio-srv-1",
        )
        defaults.update(overrides)
        return ServerRecord(**defaults)
    return _make


@pytest.fixture
def make_snapshot_record():
    """Factory for SnapshotRecord with sensible defaults. Override any field via kwargs."""
    def _make(**overrides):
        defaults = dict(
            id="snap-1", snapshot_id="snap-aws-1", game="factorio",
            server_name="fact-test", server_id="srv-1", region="us-east-1",
            status="completed",
        )
        defaults.update(overrides)
        return SnapshotRecord(**defaults)
    return _make


@pytest.fixture
def make_client_error():
    """Factory for botocore ClientError."""
    def _make(code: str, message: str = "error"):
        return ClientError({"Error": {"Code": code, "Message": message}}, "TestOp")
    return _make


# ── Shared mock fixtures ──


@pytest.fixture
def mock_launch_deps(monkeypatch):
    """Mock all AWS and infra dependencies for Provisioner.launch() tests.

    Returns a SimpleNamespace with attributes:
        .ssh       - MagicMock for the SSHClient instance
        .docker    - MagicMock for the RemoteDocker instance
        .mocks     - dict of all patched function mocks, keyed by name
    """
    defaults = {
        "get_default_vpc_and_subnet": ("vpc-123", "subnet-123"),
        "get_latest_al2023_ami": "ami-test123",
        "get_or_create_security_group": "sg-test123",
        "launch_instance": "i-test123",
        "wait_for_instance_running": None,
        "get_instance_public_ip": "54.1.2.3",
        "ensure_key_pair": Path("/tmp/gsm-key.pem"),
        "find_gsm_instances": [],
        "aws_list_snapshots": [],
        "find_gsm_eips": [],
        "set_instance_tag": None,
        "delete_instance_tag": None,
    }
    mocks = {}
    for name, rv in defaults.items():
        mock = MagicMock(return_value=rv)
        monkeypatch.setattr(f"gsm.control.provisioner.{name}", mock)
        mocks[name] = mock

    mock_ssh = MagicMock()
    mock_ssh_cls = MagicMock(return_value=mock_ssh)
    monkeypatch.setattr("gsm.control.provisioner.SSHClient", mock_ssh_cls)
    mocks["SSHClient"] = mock_ssh_cls

    mock_docker = MagicMock()
    mock_docker_cls = MagicMock(return_value=mock_docker)
    monkeypatch.setattr("gsm.control.provisioner.RemoteDocker", mock_docker_cls)
    mocks["RemoteDocker"] = mock_docker_cls

    return SimpleNamespace(ssh=mock_ssh, docker=mock_docker, mocks=mocks)


@pytest.fixture
def mock_remote_deps(monkeypatch):
    """Mock SSH/Docker/key dependencies for pause, resume, and stop_container tests.

    Returns a SimpleNamespace with attributes:
        .ssh       - MagicMock for the SSHClient instance
        .docker    - MagicMock for the RemoteDocker instance
        .mocks     - dict of all patched function mocks, keyed by name

    Pause/resume tests that need extra mocks (start_instance, etc.) can
    layer them on via @patch or by adding to .mocks.
    """
    defaults = {
        "ensure_key_pair": Path("/tmp/gsm-key.pem"),
        "stop_instance": None,
        "wait_for_instance_stopped": None,
        "start_instance": None,
        "wait_for_instance_running": None,
        "get_instance_public_ip": "54.9.8.7",
    }
    mocks = {}
    for name, rv in defaults.items():
        mock = MagicMock(return_value=rv)
        monkeypatch.setattr(f"gsm.control.provisioner.{name}", mock)
        mocks[name] = mock

    mock_ssh = MagicMock()
    mock_ssh_cls = MagicMock(return_value=mock_ssh)
    monkeypatch.setattr("gsm.control.provisioner.SSHClient", mock_ssh_cls)
    mocks["SSHClient"] = mock_ssh_cls

    mock_docker = MagicMock()
    mock_docker_cls = MagicMock(return_value=mock_docker)
    monkeypatch.setattr("gsm.control.provisioner.RemoteDocker", mock_docker_cls)
    mocks["RemoteDocker"] = mock_docker_cls

    return SimpleNamespace(ssh=mock_ssh, docker=mock_docker, mocks=mocks)
