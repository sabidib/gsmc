import pytest
from unittest.mock import patch

from gsm.control.provisioner import Provisioner
from gsm.control.state import ServerState


def test_pause_server(mock_remote_deps, make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.pause("srv-1")

    mock_remote_deps.docker.stop.assert_called_once_with("gsm-factorio-srv-1")
    mock_remote_deps.mocks["stop_instance"].assert_called_once_with("us-east-1", "i-test123")
    mock_remote_deps.mocks["wait_for_instance_stopped"].assert_called_once_with("us-east-1", "i-test123")
    assert state.get("srv-1").status == "paused"


def test_pause_proceeds_if_ssh_fails(mock_remote_deps, make_server_record, tmp_path):
    mock_remote_deps.mocks["SSHClient"].side_effect = Exception("SSH connection failed")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.pause("srv-1")

    mock_remote_deps.mocks["stop_instance"].assert_called_once()
    assert state.get("srv-1").status == "paused"


def test_pause_already_paused(make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(ValueError, match="already paused"):
            provisioner.pause("srv-1")


def test_pause_not_found(tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        provisioner.pause("nonexistent")


def test_pause_instance_terminated_externally(mock_remote_deps, make_server_record, make_client_error, tmp_path):
    """Pause raises RuntimeError and deletes state when instance was terminated."""
    mock_remote_deps.mocks["SSHClient"].side_effect = Exception("no ssh")
    mock_remote_deps.mocks["stop_instance"].side_effect = make_client_error("InvalidInstanceID.NotFound")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(RuntimeError, match="terminated externally"):
            provisioner.pause("srv-1")

    assert state.get("srv-1") is None


def test_pause_instance_already_stopped(mock_remote_deps, make_server_record, make_client_error, tmp_path):
    """Pause succeeds when instance is already stopped (IncorrectInstanceState)."""
    mock_remote_deps.mocks["SSHClient"].side_effect = Exception("no ssh")
    mock_remote_deps.mocks["stop_instance"].side_effect = make_client_error("IncorrectInstanceState")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.pause("srv-1")  # Should not raise

    assert state.get("srv-1").status == "paused"


def test_pause_waiter_timeout_still_updates_state(mock_remote_deps, make_server_record, tmp_path):
    """State is updated to paused even if waiter times out."""
    mock_remote_deps.mocks["SSHClient"].side_effect = Exception("no ssh")
    mock_remote_deps.mocks["wait_for_instance_stopped"].side_effect = Exception("waiter timeout")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.pause("srv-1")

    assert state.get("srv-1").status == "paused"


def test_resume_server(mock_remote_deps, make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        record = provisioner.resume("srv-1")

    mock_remote_deps.mocks["start_instance"].assert_called_once_with("us-east-1", "i-test123")
    mock_remote_deps.mocks["wait_for_instance_running"].assert_called_once_with("us-east-1", "i-test123")
    mock_remote_deps.mocks["update_ssh_cidr"].assert_called_once_with(
        "us-east-1", "sg-test123", "54.1.2.3/32", "10.0.0.1/32",
    )
    mock_remote_deps.docker.start.assert_called_once_with("gsm-factorio-srv-1")
    assert record.status == "running"
    assert record.public_ip == "54.9.8.7"


def test_resume_not_paused(make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="running"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(ValueError, match="not paused or stopped"):
            provisioner.resume("srv-1")


def test_resume_not_found(tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        provisioner.resume("nonexistent")


def test_resume_from_stopped(mock_remote_deps, make_server_record, tmp_path):
    """Resume from stopped skips EC2 start, just restarts container."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="stopped"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        record = provisioner.resume("srv-1")

    mock_remote_deps.mocks["start_instance"].assert_not_called()
    mock_remote_deps.docker.start.assert_called_once_with("gsm-factorio-srv-1")
    assert record.status == "running"


def test_resume_from_stopped_ssh_failure(mock_remote_deps, make_server_record, tmp_path):
    """Resume from stopped with SSH failure keeps state as stopped."""
    mock_remote_deps.ssh.connect.side_effect = Exception("SSH failed")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="stopped"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(Exception, match="SSH failed"):
            provisioner.resume("srv-1")

    assert state.get("srv-1").status == "stopped"


def test_resume_instance_terminated_externally(mock_remote_deps, make_server_record, make_client_error, tmp_path):
    """Resume raises RuntimeError and deletes state when instance was terminated."""
    mock_remote_deps.mocks["start_instance"].side_effect = make_client_error("InvalidInstanceID.NotFound")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(RuntimeError, match="terminated externally"):
            provisioner.resume("srv-1")

    assert state.get("srv-1") is None


def test_resume_docker_failure_state_is_running(mock_remote_deps, make_server_record, tmp_path):
    """If Docker fails during resume, state is 'running' (accurate) and actionable error raised."""
    mock_remote_deps.ssh.connect.side_effect = Exception("SSH broke")

    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(RuntimeError, match="container failed to start"):
            provisioner.resume("srv-1")

    assert state.get("srv-1").status == "running"


# ── stop_container tests ──


def test_stop_container(mock_remote_deps, make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record())
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        provisioner.stop_container("srv-1")

    mock_remote_deps.docker.stop.assert_called_once_with("gsm-factorio-srv-1")
    assert state.get("srv-1").status == "stopped"


def test_stop_container_not_running(make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("srv-1")):
        with pytest.raises(ValueError, match="not running"):
            provisioner.stop_container("srv-1")


def test_stop_container_not_found(tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="not found"):
        provisioner.stop_container("nonexistent")
