from unittest.mock import patch

from gsm.control.provisioner import Provisioner
from gsm.control.state import SnapshotState


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_updates_status_and_ip(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Known server gets status and IP updated from EC2."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record(public_ip="1.2.3.4"))

    mock_find.return_value = [{
        "instance_id": "i-test123",
        "state": "stopped",
        "public_ip": None,
        "gsm_id": "srv-1",
        "gsm_game": "factorio",
        "gsm_name": "fact-test",
    }]

    provisioner.reconcile()

    record = provisioner.state.get("srv-1")
    assert record.status == "paused"
    assert record.public_ip == ""


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_removes_terminated(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Server not in EC2 results gets removed from state."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record())

    mock_find.return_value = []  # Instance gone from EC2

    provisioner.reconcile()

    assert provisioner.state.get("srv-1") is None
    assert provisioner.state.list_all() == []


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_adopts_orphan(mock_find, mock_snaps, mock_eips, tmp_path):
    """Orphaned EC2 instance gets adopted into state."""
    provisioner = Provisioner(state_dir=tmp_path)

    mock_find.return_value = [{
        "instance_id": "i-orphan",
        "state": "running",
        "public_ip": "5.6.7.8",
        "gsm_id": "orphan-001",
        "gsm_game": "factorio",
        "gsm_name": "my-fact",
    }]

    provisioner.reconcile(extra_regions={"us-east-1"})

    record = provisioner.state.get("orphan-001")
    assert record is not None
    assert record.game == "factorio"
    assert record.name == "my-fact"
    assert record.instance_id == "i-orphan"
    assert record.public_ip == "5.6.7.8"
    assert record.status == "running"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_mixed_scenario(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Update + remove + adopt in a single reconcile call."""
    provisioner = Provisioner(state_dir=tmp_path)
    # Known server to be updated
    provisioner.state.save(make_server_record(
        id="srv-update", instance_id="i-update", public_ip="1.1.1.1",
    ))
    # Known server to be removed (terminated externally)
    provisioner.state.save(make_server_record(
        id="srv-remove", instance_id="i-remove", public_ip="2.2.2.2",
    ))

    mock_find.return_value = [
        # srv-update is now stopped
        {
            "instance_id": "i-update", "state": "stopped", "public_ip": None,
            "gsm_id": "srv-update", "gsm_game": "factorio", "gsm_name": "fact-1",
        },
        # srv-remove is NOT present (terminated)
        # orphan is new
        {
            "instance_id": "i-new", "state": "running", "public_ip": "9.9.9.9",
            "gsm_id": "srv-orphan", "gsm_game": "factorio", "gsm_name": "fact-orphan",
        },
    ]

    provisioner.reconcile()

    # Updated
    updated = provisioner.state.get("srv-update")
    assert updated.status == "paused"
    assert updated.public_ip == ""

    # Removed
    assert provisioner.state.get("srv-remove") is None

    # Adopted
    adopted = provisioner.state.get("srv-orphan")
    assert adopted is not None
    assert adopted.game == "factorio"
    assert adopted.public_ip == "9.9.9.9"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_multi_region(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Reconcile scans all regions from local records + extra_regions."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record(
        id="srv-west", region="us-west-2", instance_id="i-west",
    ))

    def find_by_region(region):
        if region == "us-west-2":
            return [{
                "instance_id": "i-west", "state": "running",
                "public_ip": "3.3.3.3", "gsm_id": "srv-west",
                "gsm_game": "factorio", "gsm_name": "fact-west",
            }]
        elif region == "eu-west-1":
            return [{
                "instance_id": "i-eu", "state": "running",
                "public_ip": "4.4.4.4", "gsm_id": "srv-eu",
                "gsm_game": "factorio", "gsm_name": "fact-eu",
            }]
        return []

    mock_find.side_effect = find_by_region

    provisioner.reconcile(extra_regions={"eu-west-1"})

    # Both regions scanned
    assert mock_find.call_count == 2
    regions_called = {call.args[0] for call in mock_find.call_args_list}
    assert regions_called == {"us-west-2", "eu-west-1"}

    # us-west-2 server updated
    west = provisioner.state.get("srv-west")
    assert west is not None
    assert west.public_ip == "3.3.3.3"

    # eu-west-1 orphan adopted
    eu = provisioner.state.get("srv-eu")
    assert eu is not None
    assert eu.region == "eu-west-1"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_preserves_stopped_status(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Stopped status (container off, instance running) is preserved during reconcile."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.state.save(make_server_record(
        id="srv-stopped", instance_id="i-stopped", status="stopped",
    ))

    mock_find.return_value = [{
        "instance_id": "i-stopped", "state": "running",
        "public_ip": "1.2.3.4", "gsm_id": "srv-stopped",
        "gsm_game": "factorio", "gsm_name": "fact-test",
    }]

    provisioner.reconcile()

    record = provisioner.state.get("srv-stopped")
    assert record is not None
    assert record.status == "stopped"


# ── Snapshot reconciliation tests ──


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
def test_reconcile_removes_ghost_snapshots(mock_snaps, mock_find, mock_eips, make_snapshot_record, tmp_path):
    """Local snapshot with no matching AWS snapshot is removed."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.snapshot_state.save(make_snapshot_record(
        id="ghost-1", snapshot_id="snap-gone",
    ))

    provisioner.reconcile(extra_regions={"us-east-1"})

    assert provisioner.snapshot_state.get("ghost-1") is None


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots")
def test_reconcile_keeps_valid_snapshots(mock_snaps, mock_find, mock_eips, make_snapshot_record, tmp_path):
    """Local snapshot that exists in AWS survives reconcile."""
    mock_snaps.return_value = [{
        "SnapshotId": "snap-valid",
        "Tags": [{"Key": "gsm:id", "Value": "srv-1"}],
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.snapshot_state.save(make_snapshot_record(
        id="valid-1", snapshot_id="snap-valid",
    ))

    provisioner.reconcile(extra_regions={"us-east-1"})

    assert provisioner.snapshot_state.get("valid-1") is not None


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots")
def test_reconcile_adopts_orphan_snapshots(mock_snaps, mock_find, mock_eips, tmp_path):
    """AWS snapshot with gsm tags but no local record gets adopted."""
    mock_snaps.return_value = [{
        "SnapshotId": "snap-orphan",
        "Tags": [
            {"Key": "gsm:id", "Value": "srv-orphan"},
            {"Key": "gsm:game", "Value": "factorio"},
            {"Key": "gsm:name", "Value": "fact-1"},
            {"Key": "gsm:snapshot-id", "Value": "adopted-1"},
        ],
    }]

    provisioner = Provisioner(state_dir=tmp_path)

    provisioner.reconcile(extra_regions={"us-east-1"})

    snap = provisioner.snapshot_state.get("adopted-1")
    assert snap is not None
    assert snap.snapshot_id == "snap-orphan"
    assert snap.game == "factorio"
    assert snap.server_name == "fact-1"
    assert snap.status == "completed"
