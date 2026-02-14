"""Tests for multi-machine state sharing features:
- _parse_ports_tag
- Orphan adoption with tag data
- Auto-reconcile TTL
- pin_ip/unpin_ip EC2 tags
- launch() passes tags to launch_instance
- SSM active-regions CRUD
- Name uniqueness via EC2 tags
"""
import time
from unittest.mock import patch, MagicMock, call

import pytest
from botocore.exceptions import ClientError

from gsm.control.provisioner import Provisioner, _parse_ports_tag
from gsm.control.state import ServerState
from gsm.games.factorio import factorio


# ── _parse_ports_tag ──


class TestParsePortsTag:
    def test_single_port(self):
        assert _parse_ports_tag("27015/udp") == {"27015/udp": 27015}

    def test_multiple_ports(self):
        result = _parse_ports_tag("27015/udp,34197/udp")
        assert result == {"27015/udp": 27015, "34197/udp": 34197}

    def test_tcp_ports(self):
        result = _parse_ports_tag("25565/tcp,25575/tcp")
        assert result == {"25565/tcp": 25565, "25575/tcp": 25575}

    def test_empty_string(self):
        assert _parse_ports_tag("") == {}

    def test_whitespace_handling(self):
        result = _parse_ports_tag(" 27015/udp , 34197/udp ")
        assert result == {"27015/udp": 27015, "34197/udp": 34197}

    def test_malformed_entry_skipped(self):
        result = _parse_ports_tag("27015/udp,bad,34197/udp")
        assert result == {"27015/udp": 27015, "34197/udp": 34197}

    def test_non_numeric_port_skipped(self):
        result = _parse_ports_tag("abc/udp,27015/udp")
        assert result == {"27015/udp": 27015}


# ── Orphan adoption with tags ──


@patch("gsm.control.provisioner.find_gsm_eips")
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_adopts_orphan_with_tags(mock_find, mock_snaps, mock_eips, tmp_path):
    """Orphaned EC2 instance uses tag data for ports, rcon, sg, eip."""
    mock_eips.return_value = [
        {"AllocationId": "eipalloc-orphan", "PublicIp": "52.1.2.3", "Tags": []},
    ]
    mock_find.return_value = [{
        "instance_id": "i-orphan",
        "state": "running",
        "public_ip": "5.6.7.8",
        "gsm_id": "orphan-tag",
        "gsm_game": "factorio",
        "gsm_name": "tagged-server",
        "gsm_ports": "27015/udp,34197/udp",
        "gsm_rcon_password": "secret123",
        "gsm_sg_id": "sg-orphan",
        "gsm_eip_alloc_id": "eipalloc-orphan",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile(extra_regions={"us-east-1"})

    record = provisioner.state.get("orphan-tag")
    assert record is not None
    assert record.ports == {"27015/udp": 27015, "34197/udp": 34197}
    assert record.rcon_password == "secret123"
    assert record.security_group_id == "sg-orphan"
    assert record.eip_allocation_id == "eipalloc-orphan"
    assert record.eip_public_ip == "52.1.2.3"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_adopts_orphan_no_tags(mock_find, mock_snaps, mock_eips, tmp_path):
    """Orphan without extra tags still works (empty fields)."""
    mock_find.return_value = [{
        "instance_id": "i-bare",
        "state": "running",
        "public_ip": "1.2.3.4",
        "gsm_id": "bare-001",
        "gsm_game": "factorio",
        "gsm_name": "bare",
        "gsm_ports": "",
        "gsm_rcon_password": "",
        "gsm_sg_id": "",
        "gsm_eip_alloc_id": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile(extra_regions={"us-east-1"})

    record = provisioner.state.get("bare-001")
    assert record is not None
    assert record.ports == {}
    assert record.rcon_password == ""
    assert record.security_group_id == ""
    assert record.eip_allocation_id == ""


# ── Cross-machine sync of tag-backed fields on known servers ──


@patch("gsm.control.provisioner.find_gsm_eips")
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_syncs_eip_from_tags(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server gets EIP fields updated when another machine pinned."""
    mock_eips.return_value = [
        {"AllocationId": "eipalloc-cross", "PublicIp": "52.0.0.1", "Tags": []},
    ]
    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "running",
        "public_ip": "52.0.0.1",
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
        "gsm_ports": "34197/udp",
        "gsm_rcon_password": "",
        "gsm_sg_id": "sg-test123",
        "gsm_eip_alloc_id": "eipalloc-cross",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    # Machine B has the server but without EIP info
    provisioner.state.save(make_server_record(eip_allocation_id="", eip_public_ip=""))

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.eip_allocation_id == "eipalloc-cross"
    assert record.eip_public_ip == "52.0.0.1"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_clears_eip_when_tag_removed(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server gets EIP fields cleared when another machine unpinned."""
    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "running",
        "public_ip": "54.1.2.3",
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
        "gsm_ports": "34197/udp",
        "gsm_rcon_password": "",
        "gsm_sg_id": "sg-test123",
        "gsm_eip_alloc_id": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    # Machine B still thinks EIP is pinned
    provisioner.state.save(make_server_record(
        eip_allocation_id="eipalloc-old", eip_public_ip="52.0.0.99",
    ))

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.eip_allocation_id == ""
    assert record.eip_public_ip == ""


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_syncs_rcon_and_sg_from_tags(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server gets rcon_password and security_group_id from tags."""
    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "running",
        "public_ip": "54.1.2.3",
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
        "gsm_ports": "34197/udp",
        "gsm_rcon_password": "cross-pw",
        "gsm_sg_id": "sg-remote",
        "gsm_eip_alloc_id": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    # Machine B adopted with empty fields
    provisioner.state.save(make_server_record(
        security_group_id="", rcon_password="", ports={},
    ))

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.security_group_id == "sg-remote"
    assert record.rcon_password == "cross-pw"
    assert record.ports == {"34197/udp": 34197}


# ── Auto-reconcile TTL ──


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
def test_auto_reconcile_runs_first_time(mock_find, mock_snaps, mock_eips, tmp_path):
    """auto_reconcile runs reconcile when no TTL file exists."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.auto_reconcile()

    # Reconcile was called (find_gsm_instances was invoked)
    mock_find.assert_called()
    # TTL file now exists
    assert (tmp_path / ".last_reconcile").exists()


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
def test_auto_reconcile_skips_within_ttl(mock_find, mock_snaps, mock_eips, tmp_path):
    """auto_reconcile skips when TTL file is recent."""
    ttl_file = tmp_path / ".last_reconcile"
    ttl_file.write_text(str(time.time()))

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.auto_reconcile()

    mock_find.assert_not_called()


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
def test_auto_reconcile_runs_after_ttl_expires(mock_find, mock_snaps, mock_eips, tmp_path):
    """auto_reconcile runs when TTL file is older than 30 seconds."""
    ttl_file = tmp_path / ".last_reconcile"
    ttl_file.write_text(str(time.time() - 60))

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.auto_reconcile()

    mock_find.assert_called()


def test_auto_reconcile_swallows_errors(tmp_path, monkeypatch):
    """auto_reconcile silently catches all errors."""
    provisioner = Provisioner(state_dir=tmp_path)
    monkeypatch.setattr(provisioner, "reconcile", MagicMock(side_effect=RuntimeError("boom")))

    # Should not raise
    provisioner.auto_reconcile()


# ── reconcile writes TTL ──


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
def test_reconcile_writes_ttl_file(mock_find, mock_snaps, mock_eips, tmp_path):
    """Explicit reconcile() writes TTL file."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile(extra_regions={"us-east-1"})

    ttl_file = tmp_path / ".last_reconcile"
    assert ttl_file.exists()
    ts = float(ttl_file.read_text().strip())
    assert time.time() - ts < 5


# ── pin_ip/unpin_ip set/delete EC2 tags ──


@patch("gsm.control.provisioner.set_instance_tag")
@patch("gsm.control.provisioner.associate_eip", return_value="eipassoc-123")
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-tag", "52.10.20.30"))
def test_pin_ip_sets_ec2_tag(mock_alloc, mock_assoc, mock_set_tag, make_server_record, tmp_path):
    """pin_ip sets gsm:eip-alloc-id tag on the EC2 instance."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="running"))
    provisioner = Provisioner(state_dir=tmp_path)

    provisioner.pin_ip("srv-1")

    mock_set_tag.assert_called_once_with("us-east-1", "i-test123", "gsm:eip-alloc-id", "eipalloc-tag")


@patch("gsm.control.provisioner.delete_instance_tag")
@patch("gsm.control.provisioner.get_instance_public_ip", return_value="54.99.88.77")
@patch("gsm.control.provisioner.release_eip")
@patch("gsm.control.provisioner.disassociate_eip")
def test_unpin_ip_deletes_ec2_tag(mock_disassoc, mock_release, mock_get_ip, mock_del_tag, make_server_record, tmp_path):
    """unpin_ip deletes gsm:eip-alloc-id tag from the EC2 instance."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        status="running",
        eip_allocation_id="eipalloc-unpin",
        eip_public_ip="52.10.20.30",
    ))
    provisioner = Provisioner(state_dir=tmp_path)

    provisioner.unpin_ip("srv-1")

    mock_del_tag.assert_called_once_with("us-east-1", "i-test123", "gsm:eip-alloc-id")


# ── launch() passes tags ──


def test_launch_passes_ports_tag(mock_launch_deps, tmp_path):
    """launch() passes ports_tag to launch_instance."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1")

    call_kwargs = mock_launch_deps.mocks["launch_instance"].call_args
    assert "ports_tag" in call_kwargs.kwargs
    assert "34197/udp" in call_kwargs.kwargs["ports_tag"]


def test_launch_passes_rcon_password_tag(mock_launch_deps, tmp_path):
    """launch() passes rcon_password to launch_instance for Docker games."""
    from gsm.games.registry import GameDefinition, GamePort
    game_with_rcon = GameDefinition(
        name="test-rcon-tag", display_name="Test RCON Tag",
        image="test/rcon",
        ports=[GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")],
        defaults={"EULA": "TRUE"}, default_instance_type="t3.medium", min_ram_gb=2,
        volumes=["/data"], data_paths={"world": "/data/world"},
        rcon_port=25575, rcon_password_key="RCON_PASSWORD",
    )
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=game_with_rcon, region="us-east-1")

    call_kwargs = mock_launch_deps.mocks["launch_instance"].call_args
    assert call_kwargs.kwargs["rcon_password"] == record.rcon_password
    assert record.rcon_password != ""


# ── launch() EIP tag during pin ──


@patch("gsm.control.provisioner.set_instance_tag")
@patch("gsm.control.provisioner.associate_eip", return_value="eipassoc-launch")
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-launch", "52.10.20.60"))
def test_launch_pin_ip_sets_eip_tag(mock_alloc, mock_assoc, mock_set_tag, mock_launch_deps, tmp_path):
    """launch(pin_ip=True) sets gsm:eip-alloc-id tag on the instance."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1", pin_ip=True)

    # set_instance_tag should have been called with the eip-alloc-id
    tag_calls = [c for c in mock_set_tag.call_args_list if c.args[2] == "gsm:eip-alloc-id"]
    assert len(tag_calls) == 1
    assert tag_calls[0].args[3] == "eipalloc-launch"


# ── SSM active-regions ──


def _make_ssm_mock(existing_value=None):
    """Create a mock SSM client. Returns (mock_client, mock_boto3_client)."""
    mock_ssm = MagicMock()
    if existing_value is not None:
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": existing_value}
        }
    else:
        mock_ssm.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "not found"}},
            "GetParameter",
        )
    mock_boto3 = MagicMock(return_value=mock_ssm)
    return mock_ssm, mock_boto3


def test_get_active_regions_empty(tmp_path, monkeypatch):
    """_get_active_regions returns empty set when param doesn't exist."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value=None)
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    result = provisioner._get_active_regions()
    assert result == set()


def test_get_active_regions_with_values(tmp_path, monkeypatch):
    """_get_active_regions parses comma-separated regions."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="us-east-1,eu-west-1")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    result = provisioner._get_active_regions()
    assert result == {"us-east-1", "eu-west-1"}


def test_add_active_region_new(tmp_path, monkeypatch):
    """_add_active_region adds a new region to SSM."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value=None)
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner._add_active_region("us-west-2")

    mock_ssm.put_parameter.assert_called_once()
    put_args = mock_ssm.put_parameter.call_args
    assert put_args.kwargs["Value"] == "us-west-2"


def test_add_active_region_idempotent(tmp_path, monkeypatch):
    """_add_active_region is a no-op when region already present."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="us-east-1,us-west-2")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner._add_active_region("us-west-2")

    mock_ssm.put_parameter.assert_not_called()


def test_remove_active_region_with_servers_remaining(tmp_path, monkeypatch, make_server_record):
    """_remove_active_region is a no-op when servers remain in the region."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="us-east-1")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record(region="us-east-1"))
    provisioner._remove_active_region("us-east-1")

    # Should not have modified SSM since servers still exist in the region
    mock_ssm.put_parameter.assert_not_called()
    mock_ssm.delete_parameter.assert_not_called()


def test_remove_active_region_last_region(tmp_path, monkeypatch):
    """_remove_active_region deletes SSM param when no regions left."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="us-east-1")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner._remove_active_region("us-east-1")

    mock_ssm.delete_parameter.assert_called_once()


def test_remove_active_region_other_regions_remain(tmp_path, monkeypatch):
    """_remove_active_region updates SSM with remaining regions."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="us-east-1,us-west-2")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner._remove_active_region("us-east-1")

    mock_ssm.put_parameter.assert_called_once()
    assert mock_ssm.put_parameter.call_args.kwargs["Value"] == "us-west-2"


# ── Reconcile includes SSM active-regions ──


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
def test_reconcile_includes_ssm_regions(mock_find, mock_snaps, mock_eips, tmp_path, monkeypatch):
    """reconcile() queries SSM active-regions."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="eu-west-1,ap-southeast-1")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile()

    # Should have queried at least the SSM regions + default
    regions_called = {c.args[0] for c in mock_find.call_args_list}
    assert "eu-west-1" in regions_called
    assert "ap-southeast-1" in regions_called


# ── Name uniqueness via EC2 tags ──


def test_launch_name_duplicate_in_ec2(mock_launch_deps, tmp_path, monkeypatch):
    """launch() raises when name exists in EC2 tags (cross-machine duplicate)."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value=None)
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    # find_gsm_instances returns an instance with matching name
    def find_with_duplicate(region):
        return [{
            "instance_id": "i-remote",
            "state": "running",
            "public_ip": "1.2.3.4",
            "gsm_id": "remote-001",
            "gsm_game": "factorio",
            "gsm_name": "my-server",
            "gsm_ports": "",
            "gsm_rcon_password": "",
            "gsm_sg_id": "",
            "gsm_eip_alloc_id": "",
        }]

    mock_launch_deps.mocks["find_gsm_instances"].side_effect = find_with_duplicate

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="A server named 'my-server' already exists"):
        provisioner.launch(game=factorio, region="us-east-1", name="my-server")


def test_launch_name_check_tolerates_errors(mock_launch_deps, tmp_path, monkeypatch):
    """launch() proceeds when EC2 name check fails (best-effort)."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value=None)
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    # First call (reconcile) returns [], second (name check) raises
    call_count = [0]
    original_mock = mock_launch_deps.mocks["find_gsm_instances"]

    def find_side_effect(region):
        call_count[0] += 1
        if call_count[0] <= 1:
            return []  # reconcile
        raise RuntimeError("network error")

    original_mock.side_effect = find_side_effect

    provisioner = Provisioner(state_dir=tmp_path)
    # Should not raise — EC2 check failure is swallowed
    record = provisioner.launch(game=factorio, region="us-east-1", name="unique-name")
    assert record.name == "unique-name"


# ── container_name tag sync ──


def test_launch_passes_container_name_tag(mock_launch_deps, tmp_path):
    """launch() passes container_name to launch_instance."""
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1")

    call_kwargs = mock_launch_deps.mocks["launch_instance"].call_args
    assert "container_name" in call_kwargs.kwargs
    assert call_kwargs.kwargs["container_name"] == record.container_name


def test_launch_passes_launch_time_tag(mock_launch_deps, tmp_path):
    """launch() passes launch_time to launch_instance."""
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1")

    call_kwargs = mock_launch_deps.mocks["launch_instance"].call_args
    assert "launch_time" in call_kwargs.kwargs
    assert call_kwargs.kwargs["launch_time"] == record.launch_time


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_adopts_orphan_with_container_name(mock_find, mock_snaps, mock_eips, tmp_path):
    """Orphan adoption uses gsm:container-name tag."""
    mock_find.return_value = [{
        "instance_id": "i-orphan",
        "state": "running",
        "public_ip": "5.6.7.8",
        "gsm_id": "orphan-cn",
        "gsm_game": "factorio",
        "gsm_name": "my-fact",
        "gsm_ports": "",
        "gsm_rcon_password": "",
        "gsm_sg_id": "",
        "gsm_eip_alloc_id": "",
        "gsm_container_name": "gsm-factorio-custom",
        "gsm_launch_time": "2024-01-01T00:00:00+00:00",
        "gsm_container_stopped": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile(extra_regions={"us-east-1"})

    record = provisioner.state.get("orphan-cn")
    assert record.container_name == "gsm-factorio-custom"
    assert record.launch_time == "2024-01-01T00:00:00+00:00"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_syncs_container_name_for_known_server(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server gets container_name updated from tag (e.g. after snapshot restore)."""
    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "running",
        "public_ip": "54.1.2.3",
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
        "gsm_ports": "34197/udp",
        "gsm_rcon_password": "",
        "gsm_sg_id": "sg-test123",
        "gsm_eip_alloc_id": "",
        "gsm_container_name": "gsm-factorio-restored",
        "gsm_launch_time": "",
        "gsm_container_stopped": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record(container_name="gsm-factorio-srv-1"))

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.container_name == "gsm-factorio-restored"


# ── container-stopped tag sync ──


@patch("gsm.control.provisioner.set_instance_tag")
def test_stop_container_sets_tag(mock_set_tag, mock_remote_deps, make_server_record, tmp_path):
    """stop_container() sets gsm:container-stopped tag."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="sc-tag", instance_id="i-sc-tag"))
    provisioner = Provisioner(state_dir=tmp_path)

    with patch.object(provisioner, "_refresh_record", return_value=state.get("sc-tag")):
        provisioner.stop_container("sc-tag")

    mock_set_tag.assert_called_once_with("us-east-1", "i-sc-tag", "gsm:container-stopped", "true")


@patch("gsm.control.provisioner.delete_instance_tag")
def test_resume_container_clears_tag(mock_del_tag, mock_remote_deps, make_server_record, tmp_path):
    """_resume_container() clears gsm:container-stopped tag."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="rc-tag", instance_id="i-rc-tag", status="stopped"))
    provisioner = Provisioner(state_dir=tmp_path)

    with patch.object(provisioner, "_refresh_record", return_value=state.get("rc-tag")):
        provisioner.resume("rc-tag")

    mock_del_tag.assert_called_once_with("us-east-1", "i-rc-tag", "gsm:container-stopped")


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_adopts_orphan_with_stopped_container(mock_find, mock_snaps, mock_eips, tmp_path):
    """Orphan with gsm:container-stopped=true gets status='stopped' not 'running'."""
    mock_find.return_value = [{
        "instance_id": "i-stopped-orphan",
        "state": "running",
        "public_ip": "5.6.7.8",
        "gsm_id": "stopped-orphan",
        "gsm_game": "factorio",
        "gsm_name": "stopped-fact",
        "gsm_ports": "",
        "gsm_rcon_password": "",
        "gsm_sg_id": "",
        "gsm_eip_alloc_id": "",
        "gsm_container_name": "",
        "gsm_launch_time": "",
        "gsm_container_stopped": "true",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile(extra_regions={"us-east-1"})

    record = provisioner.state.get("stopped-orphan")
    assert record.status == "stopped"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_syncs_stopped_for_known_server(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server on Machine B sees stopped status from Machine A via tag."""
    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "running",
        "public_ip": "54.1.2.3",
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
        "gsm_ports": "34197/udp",
        "gsm_rcon_password": "",
        "gsm_sg_id": "sg-test123",
        "gsm_eip_alloc_id": "",
        "gsm_container_name": "",
        "gsm_launch_time": "",
        "gsm_container_stopped": "true",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    # Machine B thinks server is running
    provisioner.state.save(make_server_record(status="running"))

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.status == "stopped"


# ── destroy_all discovers cross-machine servers ──


@patch("gsm.control.provisioner.terminate_instance")
@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_destroy_all_discovers_cross_machine(mock_find, mock_snaps, mock_eips, mock_terminate, tmp_path):
    """destroy_all() reconciles first so it discovers servers from other machines."""
    mock_find.return_value = [{
        "instance_id": "i-remote",
        "state": "running",
        "public_ip": "1.2.3.4",
        "gsm_id": "remote-srv",
        "gsm_game": "factorio",
        "gsm_name": "remote-fact",
        "gsm_ports": "",
        "gsm_rcon_password": "",
        "gsm_sg_id": "",
        "gsm_eip_alloc_id": "",
        "gsm_container_name": "",
        "gsm_launch_time": "",
        "gsm_container_stopped": "",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    # Machine B has no local servers initially
    assert provisioner.state.list_all() == []

    with patch.object(provisioner, "_refresh_record", side_effect=lambda sid: provisioner.state.get(sid)):
        provisioner.destroy_all()

    # The orphan from Machine A should have been adopted and destroyed
    mock_terminate.assert_called_once_with("us-east-1", "i-remote")


# ── list_eips includes SSM active regions ──


def test_list_eips_includes_ssm_regions(tmp_path, monkeypatch):
    """list_eips() scans SSM active-regions, not just local."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value="eu-west-1")
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)
    monkeypatch.setattr("gsm.control.provisioner.find_gsm_eips", MagicMock(return_value=[]))

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.list_eips()

    from gsm.control.provisioner import find_gsm_eips
    regions_called = {c.args[0] for c in find_gsm_eips.call_args_list}
    assert "eu-west-1" in regions_called


# ── _add_active_region called early in launch ──


def test_launch_adds_active_region_early(mock_launch_deps, tmp_path, monkeypatch):
    """_add_active_region is called right after launch_instance, before Docker setup."""
    mock_ssm, mock_boto3 = _make_ssm_mock(existing_value=None)
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", mock_boto3)

    call_order = []

    def track_docker_wait():
        call_order.append("docker_wait")

    mock_launch_deps.docker.wait_for_docker = MagicMock(side_effect=track_docker_wait)

    def track_put(**kwargs):
        call_order.append("put_parameter")

    mock_ssm.put_parameter = MagicMock(side_effect=track_put)

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1")

    # _add_active_region (put_parameter) should happen BEFORE docker setup
    assert "put_parameter" in call_order
    assert "docker_wait" in call_order
    assert call_order.index("put_parameter") < call_order.index("docker_wait")


# ── _refresh_record syncs tag-backed fields ──


def test_refresh_record_syncs_eip_from_tags(make_server_record, tmp_path, monkeypatch):
    """_refresh_record picks up EIP changes from EC2 tags."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(eip_allocation_id="", eip_public_ip=""))

    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{
            "InstanceId": "i-test123",
            "State": {"Name": "running"},
            "PublicIpAddress": "54.1.2.3",
            "Tags": [
                {"Key": "gsm:id", "Value": "srv-1"},
                {"Key": "gsm:eip-alloc-id", "Value": "eipalloc-cross"},
                {"Key": "gsm:container-name", "Value": "gsm-factorio-srv-1"},
                {"Key": "gsm:sg-id", "Value": "sg-test123"},
            ],
        }]}],
    }
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", MagicMock(return_value=mock_ec2))
    monkeypatch.setattr("gsm.control.provisioner.find_gsm_eips", MagicMock(return_value=[
        {"AllocationId": "eipalloc-cross", "PublicIp": "52.0.0.1"},
    ]))

    provisioner = Provisioner(state_dir=tmp_path)
    result = provisioner._refresh_record("srv-1")

    assert result.eip_allocation_id == "eipalloc-cross"
    assert result.eip_public_ip == "52.0.0.1"


def test_refresh_record_syncs_stopped_from_tag(make_server_record, tmp_path, monkeypatch):
    """_refresh_record picks up container-stopped from EC2 tags."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="running"))

    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [{"Instances": [{
            "InstanceId": "i-test123",
            "State": {"Name": "running"},
            "PublicIpAddress": "54.1.2.3",
            "Tags": [
                {"Key": "gsm:id", "Value": "srv-1"},
                {"Key": "gsm:container-stopped", "Value": "true"},
            ],
        }]}],
    }
    monkeypatch.setattr("gsm.control.provisioner.boto3.client", MagicMock(return_value=mock_ec2))

    provisioner = Provisioner(state_dir=tmp_path)
    result = provisioner._refresh_record("srv-1")

    assert result.status == "stopped"
