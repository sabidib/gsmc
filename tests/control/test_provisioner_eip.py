import pytest
from unittest.mock import patch, MagicMock

from gsm.control.provisioner import Provisioner
from gsm.control.state import ServerState
from gsm.games.factorio import factorio


# ── pin_ip tests ──


@patch("gsm.control.provisioner.associate_eip", return_value="eipassoc-123")
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-abc", "52.10.20.30"))
def test_pin_ip_running_server(mock_alloc, mock_assoc, make_server_record, tmp_path):
    """Pin on a running server allocates + associates and updates state."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="running"))
    provisioner = Provisioner(state_dir=tmp_path)

    result = provisioner.pin_ip("srv-1")

    mock_alloc.assert_called_once_with("us-east-1", "srv-1")
    mock_assoc.assert_called_once_with("us-east-1", "eipalloc-abc", "i-test123")
    assert result.eip_allocation_id == "eipalloc-abc"
    assert result.eip_public_ip == "52.10.20.30"
    assert result.public_ip == "52.10.20.30"


@patch("gsm.control.provisioner.associate_eip")
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-def", "52.10.20.31"))
def test_pin_ip_paused_server(mock_alloc, mock_assoc, make_server_record, tmp_path):
    """Pin on a paused server allocates only, no associate."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)

    result = provisioner.pin_ip("srv-1")

    mock_alloc.assert_called_once_with("us-east-1", "srv-1")
    mock_assoc.assert_not_called()
    assert result.eip_allocation_id == "eipalloc-def"
    assert result.eip_public_ip == "52.10.20.31"


@patch("gsm.control.provisioner.allocate_eip")
def test_pin_ip_already_pinned(mock_alloc, make_server_record, tmp_path):
    """Pin raises ValueError when server already has an EIP."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(eip_allocation_id="eipalloc-old", eip_public_ip="52.0.0.1"))
    provisioner = Provisioner(state_dir=tmp_path)

    with pytest.raises(ValueError, match="already has a pinned IP"):
        provisioner.pin_ip("srv-1")

    mock_alloc.assert_not_called()


@patch("gsm.control.provisioner.release_eip")
@patch("gsm.control.provisioner.associate_eip", side_effect=Exception("association failed"))
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-rollback", "52.10.20.32"))
def test_pin_ip_associate_failure_releases(mock_alloc, mock_assoc, mock_release, make_server_record, tmp_path):
    """If association fails, the allocated EIP is released (rollback)."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="running"))
    provisioner = Provisioner(state_dir=tmp_path)

    with pytest.raises(Exception, match="association failed"):
        provisioner.pin_ip("srv-1")

    mock_release.assert_called_once_with("us-east-1", "eipalloc-rollback")
    # State should not have EIP fields set
    record = state.get("srv-1")
    assert record.eip_allocation_id == ""


# ── unpin_ip tests ──


@patch("gsm.control.provisioner.get_instance_public_ip", return_value="54.99.88.77")
@patch("gsm.control.provisioner.release_eip")
@patch("gsm.control.provisioner.disassociate_eip")
def test_unpin_ip_running_server(mock_disassoc, mock_release, mock_get_ip, make_server_record, tmp_path):
    """Unpin on a running server disassociates, releases, and gets new ephemeral IP."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        status="running",
        eip_allocation_id="eipalloc-unpin",
        eip_public_ip="52.10.20.30",
    ))
    provisioner = Provisioner(state_dir=tmp_path)

    result = provisioner.unpin_ip("srv-1")

    mock_disassoc.assert_called_once_with("us-east-1", "eipalloc-unpin")
    mock_release.assert_called_once_with("us-east-1", "eipalloc-unpin")
    assert result.eip_allocation_id == ""
    assert result.eip_public_ip == ""
    assert result.public_ip == "54.99.88.77"


@patch("gsm.control.provisioner.get_instance_public_ip")
@patch("gsm.control.provisioner.release_eip")
@patch("gsm.control.provisioner.disassociate_eip")
def test_unpin_ip_paused_server(mock_disassoc, mock_release, mock_get_ip, make_server_record, tmp_path):
    """Unpin on a paused server disassociates + releases, no IP lookup."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        status="paused",
        eip_allocation_id="eipalloc-paused",
        eip_public_ip="52.10.20.31",
    ))
    provisioner = Provisioner(state_dir=tmp_path)

    result = provisioner.unpin_ip("srv-1")

    mock_disassoc.assert_called_once()
    mock_release.assert_called_once()
    mock_get_ip.assert_not_called()
    assert result.eip_allocation_id == ""


def test_unpin_ip_not_pinned(make_server_record, tmp_path):
    """Unpin raises ValueError when server has no EIP."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)

    with pytest.raises(ValueError, match="does not have a pinned IP"):
        provisioner.unpin_ip("srv-1")


# ── resume with EIP ──


def test_resume_with_eip(mock_remote_deps, make_server_record, tmp_path):
    """Resume associates EIP and uses eip_public_ip instead of ephemeral IP."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        status="paused",
        eip_allocation_id="eipalloc-resume",
        eip_public_ip="52.10.20.40",
    ))

    with patch("gsm.control.provisioner.associate_eip", return_value="eipassoc-r") as mock_assoc:
        provisioner = Provisioner(state_dir=tmp_path)
        with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
            record = provisioner.resume("srv-1")

    mock_assoc.assert_called_once_with("us-east-1", "eipalloc-resume", "i-test123")
    assert record.public_ip == "52.10.20.40"
    # get_instance_public_ip should NOT have been called for the IP
    mock_remote_deps.mocks["get_instance_public_ip"].assert_not_called()


def test_resume_without_eip(mock_remote_deps, make_server_record, tmp_path):
    """Resume without EIP uses ephemeral IP (regression test)."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))

    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        record = provisioner.resume("srv-1")

    mock_remote_deps.mocks["get_instance_public_ip"].assert_called_once()
    assert record.public_ip == "54.9.8.7"


# ── destroy with EIP ──


@patch("gsm.control.provisioner.terminate_instance")
@patch("gsm.control.provisioner.release_eip")
@patch("gsm.control.provisioner.disassociate_eip")
def test_destroy_with_eip(mock_disassoc, mock_release, mock_terminate, make_server_record, tmp_path):
    """Destroy releases EIP before terminating instance."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        eip_allocation_id="eipalloc-destroy",
        eip_public_ip="52.10.20.50",
    ))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.destroy("srv-1")

    mock_disassoc.assert_called_once_with("us-east-1", "eipalloc-destroy")
    mock_release.assert_called_once_with("us-east-1", "eipalloc-destroy")
    mock_terminate.assert_called_once()
    assert state.get("srv-1") is None


@patch("gsm.control.provisioner.terminate_instance")
@patch("gsm.control.provisioner.release_eip", side_effect=Exception("release failed"))
@patch("gsm.control.provisioner.disassociate_eip")
def test_destroy_eip_release_failure_still_terminates(
    mock_disassoc, mock_release, mock_terminate, make_server_record, tmp_path,
):
    """EIP release failure doesn't prevent instance termination."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        eip_allocation_id="eipalloc-fail",
        eip_public_ip="52.10.20.51",
    ))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.destroy("srv-1")

    mock_terminate.assert_called_once()
    assert state.get("srv-1") is None


# ── launch with --pin-ip ──


@patch("gsm.control.provisioner.associate_eip", return_value="eipassoc-launch")
@patch("gsm.control.provisioner.allocate_eip", return_value=("eipalloc-launch", "52.10.20.60"))
def test_launch_with_pin_ip(mock_alloc, mock_assoc, mock_launch_deps, tmp_path):
    """launch(pin_ip=True) allocates and associates an EIP."""
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1", pin_ip=True)

    mock_alloc.assert_called_once()
    mock_assoc.assert_called_once()
    assert record.eip_allocation_id == "eipalloc-launch"
    assert record.eip_public_ip == "52.10.20.60"
    assert record.public_ip == "52.10.20.60"


# ── reconcile with stale EIP ──


@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_reconcile_clears_stale_eip(mock_find, mock_eips, mock_snaps, make_server_record, tmp_path):
    """Reconcile clears EIP fields when EIP no longer exists in AWS."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        eip_allocation_id="eipalloc-stale",
        eip_public_ip="52.10.20.70",
    ))

    mock_find.return_value = [{
        "instance_id": "i-test123", "state": "running", "public_ip": "54.1.2.3",
        "gsm_id": "srv-1", "gsm_game": "factorio", "gsm_name": "fact-test",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.reconcile()

    record = state.get("srv-1")
    assert record.eip_allocation_id == ""
    assert record.eip_public_ip == ""
    assert record.public_ip == "54.1.2.3"
