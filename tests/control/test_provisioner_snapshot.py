import pytest
from unittest.mock import patch

from gsm.control.provisioner import Provisioner
from gsm.control.state import ServerState, SnapshotState


@patch("gsm.control.provisioner.wait_for_snapshot_complete")
@patch("gsm.control.provisioner.create_snapshot", return_value="snap-aws-123")
@patch("gsm.control.provisioner.get_instance_root_volume_id", return_value="vol-abc123")
def test_snapshot_server(mock_vol, mock_create_snap, mock_wait, make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    snap = provisioner.snapshot("srv-1")

    mock_vol.assert_called_once_with("us-east-1", "i-test123")
    mock_create_snap.assert_called_once()
    mock_wait.assert_called_once_with("us-east-1", "snap-aws-123")
    assert snap.snapshot_id == "snap-aws-123"
    assert snap.game == "factorio"
    assert snap.server_id == "srv-1"
    assert snap.status == "completed"
    assert provisioner.snapshot_state.get(snap.id) is not None


def test_snapshot_not_found(tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        provisioner.snapshot("nonexistent")


@patch("gsm.control.provisioner.aws_delete_snapshot")
@patch("gsm.control.provisioner.find_amis_using_snapshot", return_value=[])
def test_delete_snapshot(mock_find_amis, mock_aws_del, make_snapshot_record, tmp_path):
    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="del-snap-1", snapshot_id="snap-aws-del", game="factorio",
        server_name="fact-1", server_id="srv-del", region="us-west-2",
    ))
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.delete_snapshot("del-snap-1")

    mock_aws_del.assert_called_once_with("us-west-2", "snap-aws-del")
    assert provisioner.snapshot_state.get("del-snap-1") is None


@patch("gsm.control.provisioner.aws_delete_snapshot")
@patch("gsm.control.provisioner.deregister_ami")
@patch("gsm.control.provisioner.find_amis_using_snapshot", return_value=["ami-leftover1", "ami-leftover2"])
def test_delete_snapshot_deregisters_lingering_amis(mock_find_amis, mock_dereg, mock_aws_del, make_snapshot_record, tmp_path):
    """Snapshot delete deregisters AMIs backed by the snapshot before deleting."""
    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="ami-snap-1", snapshot_id="snap-with-ami", region="us-east-1",
    ))
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.delete_snapshot("ami-snap-1")

    mock_find_amis.assert_called_once_with("us-east-1", "snap-with-ami")
    assert mock_dereg.call_count == 2
    mock_dereg.assert_any_call("us-east-1", "ami-leftover1")
    mock_dereg.assert_any_call("us-east-1", "ami-leftover2")
    mock_aws_del.assert_called_once_with("us-east-1", "snap-with-ami")


def test_delete_snapshot_not_found(tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        provisioner.delete_snapshot("nonexistent")


def test_list_snapshots(make_snapshot_record, tmp_path):
    snap_state = SnapshotState(state_dir=tmp_path)
    for i in range(3):
        snap_state.save(make_snapshot_record(
            id=f"list-snap-{i}", snapshot_id=f"snap-aws-{i}",
            server_name=f"mc-{i}", server_id=f"srv-{i}",
        ))
    provisioner = Provisioner(state_dir=tmp_path)
    assert len(provisioner.list_snapshots()) == 3


@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot(mock_ami_snap, mock_launch_deps, make_snapshot_record, tmp_path):
    from gsm.games.factorio import factorio

    # aws_list_snapshots must return the snapshot so reconcile doesn't delete it
    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-restore", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "restore-snap"},
    ]}]

    mock_launch_deps.docker.find_gsm_container.return_value = "gsm-factorio-old12345"

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="restore-snap", snapshot_id="snap-aws-restore",
        server_name="fact-orig", server_id="srv-orig",
    ))

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1", from_snapshot="restore-snap")

    mock_ami_snap.assert_called_once()
    mock_launch_deps.mocks["get_latest_al2023_ami"].assert_not_called()
    # Reuses the old container — no pull, no run/create
    mock_launch_deps.docker.pull.assert_not_called()
    mock_launch_deps.docker.run.assert_not_called()
    mock_launch_deps.docker.create.assert_not_called()
    mock_launch_deps.docker.start.assert_called_once_with("gsm-factorio-old12345")
    assert record.container_name == "gsm-factorio-old12345"
    assert record.game == "factorio"
    assert record.status == "running"


def test_launch_from_snapshot_not_found(mock_launch_deps, tmp_path):
    from gsm.games.factorio import factorio

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="Snapshot nonexistent not found"):
        provisioner.launch(game=factorio, from_snapshot="nonexistent")


@patch("gsm.control.provisioner.deregister_ami")
@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot_deregisters_ami(mock_ami_snap, mock_dereg, mock_launch_deps, make_snapshot_record, tmp_path):
    """Launching from a snapshot deregisters the temporary AMI after success."""
    from gsm.games.factorio import factorio

    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-dereg", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "snap-dereg"},
    ]}]
    mock_launch_deps.docker.find_gsm_container.return_value = "gsm-factorio-old"

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="snap-dereg", snapshot_id="snap-aws-dereg",
        server_name="fact-orig", server_id="srv-orig",
    ))

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1", from_snapshot="snap-dereg")

    mock_dereg.assert_called_once_with("us-east-1", "ami-restored")


@patch("gsm.control.provisioner.deregister_ami")
def test_launch_normal_does_not_deregister(mock_dereg, mock_launch_deps, tmp_path):
    """Normal launch (no snapshot) does NOT call deregister_ami."""
    from gsm.games.factorio import factorio

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1")

    mock_dereg.assert_not_called()


@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot_no_container_found(mock_ami_snap, mock_launch_deps, make_snapshot_record, tmp_path):
    """Launching from a snapshot fails if no gsm container exists on the volume."""
    from gsm.games.factorio import factorio

    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-nocontainer", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "snap-nocontainer"},
    ]}]
    mock_launch_deps.docker.find_gsm_container.return_value = None

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="snap-nocontainer", snapshot_id="snap-aws-nocontainer",
        server_name="fact-orig", server_id="srv-orig",
    ))

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(RuntimeError, match="No gsm container found"):
        provisioner.launch(game=factorio, region="us-east-1", from_snapshot="snap-nocontainer")


def test_launch_from_snapshot_rejects_env_overrides(mock_launch_deps, make_snapshot_record, tmp_path):
    """Snapshot restores cannot be combined with config changes."""
    from gsm.games.factorio import factorio

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(id="snap-env", snapshot_id="snap-aws-env"))

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="Cannot use"):
        provisioner.launch(game=factorio, from_snapshot="snap-env", env_overrides={"FOO": "bar"})


def test_launch_from_snapshot_rejects_uploads(mock_launch_deps, make_snapshot_record, tmp_path):
    """Snapshot restores cannot be combined with uploads."""
    from gsm.games.factorio import factorio

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(id="snap-up", snapshot_id="snap-aws-up"))

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="Cannot use"):
        provisioner.launch(game=factorio, from_snapshot="snap-up", uploads=[("/a", "/b")])


@patch("gsm.control.provisioner.wait_for_snapshot_complete")
@patch("gsm.control.provisioner.create_snapshot", return_value="snap-aws-meta")
@patch("gsm.control.provisioner.get_instance_root_volume_id", return_value="vol-meta")
def test_snapshot_captures_metadata(mock_vol, mock_create, mock_wait, make_server_record, tmp_path):
    """Snapshot captures config and rcon_password from the server record."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        config={"EULA": "TRUE", "RCON_PASSWORD": "mypass"},
        rcon_password="mypass",
    ))
    provisioner = Provisioner(state_dir=tmp_path)
    snap = provisioner.snapshot("srv-1")

    assert snap.config == {"EULA": "TRUE", "RCON_PASSWORD": "mypass"}
    assert snap.rcon_password == "mypass"
    # Verify it round-trips through state
    loaded = provisioner.snapshot_state.get(snap.id)
    assert loaded.config == snap.config
    assert loaded.rcon_password == "mypass"


@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot_restores_metadata(mock_ami_snap, mock_launch_deps, make_snapshot_record, tmp_path):
    """Restoring from a snapshot with metadata populates config/rcon_password."""
    from gsm.games.factorio import factorio

    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-meta", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "snap-meta"},
    ]}]
    mock_launch_deps.docker.find_gsm_container.return_value = "gsm-factorio-old12345"

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="snap-meta", snapshot_id="snap-aws-meta",
        server_name="fact-orig", server_id="srv-orig",
        config={"EULA": "TRUE", "RCON_PASSWORD": "origpass"},
        rcon_password="origpass",
    ))

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1", from_snapshot="snap-meta")

    assert record.config == {"EULA": "TRUE", "RCON_PASSWORD": "origpass"}
    assert record.rcon_password == "origpass"


@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot_reads_disk_fallback(mock_ami_snap, mock_launch_deps, make_snapshot_record, tmp_path):
    """Old snapshots without metadata fall back to reading /opt/gsm/metadata.json from disk."""
    import json
    from gsm.games.factorio import factorio

    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-old", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "snap-old"},
    ]}]
    mock_launch_deps.docker.find_gsm_container.return_value = "gsm-factorio-old12345"

    disk_metadata = json.dumps({
        "config": {"EULA": "TRUE", "SERVER_NAME": "disk-server"},
        "rcon_password": "diskpass",
    })
    mock_launch_deps.ssh.run.return_value = disk_metadata

    snap_state = SnapshotState(state_dir=tmp_path)
    # Old snapshot with no metadata fields (defaults to empty)
    snap_state.save(make_snapshot_record(
        id="snap-old", snapshot_id="snap-aws-old",
        server_name="fact-orig", server_id="srv-orig",
    ))

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1", from_snapshot="snap-old")

    assert record.config == {"EULA": "TRUE", "SERVER_NAME": "disk-server"}
    assert record.rcon_password == "diskpass"


def test_launch_writes_metadata_file(mock_launch_deps, tmp_path):
    """Normal launch writes /opt/gsm/metadata.json via SSH."""
    import json
    from gsm.games.factorio import factorio

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1")

    # Find the ssh.run call that writes metadata
    metadata_calls = [
        call for call in mock_launch_deps.ssh.run.call_args_list
        if "metadata.json" in str(call)
    ]
    assert len(metadata_calls) == 1
    call_arg = metadata_calls[0][0][0]
    assert "mkdir -p /opt/gsm" in call_arg
    # Extract the JSON from the heredoc and verify it
    json_str = call_arg.split("'GSMEOF'\n")[1].split("\nGSMEOF")[0]
    meta = json.loads(json_str)
    assert "config" in meta
    assert "rcon_password" in meta
