import pytest
from unittest.mock import patch

from gsm.control.provisioner import Provisioner
from gsm.control.state import ServerState
from gsm.games.factorio import factorio
from gsm.games.registry import GameDefinition, GamePort


def test_launch_server(mock_launch_deps, tmp_path):
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1")

    assert record.game == "factorio"
    assert record.public_ip == "54.1.2.3"
    assert record.instance_id == "i-test123"
    assert record.status == "running"
    mock_launch_deps.docker.wait_for_docker.assert_called_once()
    mock_launch_deps.docker.pull.assert_called_once_with("factoriotools/factorio")
    mock_launch_deps.docker.run.assert_called_once()


def test_launch_generates_random_rcon_password(mock_launch_deps, tmp_path):
    """Launching a game with rcon_password_key generates a random password."""
    _game_with_rcon_pw = GameDefinition(
        name="test-rcon-game", display_name="Test RCON Game",
        image="test/rcon-game",
        ports=[GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")],
        defaults={"EULA": "TRUE"}, default_instance_type="t3.medium", min_ram_gb=2,
        volumes=["/data"], data_paths={"world": "/data/world"},
        rcon_port=25575, rcon_password_key="RCON_PASSWORD",
    )
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=_game_with_rcon_pw, region="us-east-1")

    assert record.rcon_password != ""
    assert len(record.rcon_password) > 10  # token_urlsafe(16) is ~22 chars

    # Verify the password was passed as an env var to Docker
    docker_run_call = mock_launch_deps.docker.run
    if docker_run_call.called:
        call_kwargs = docker_run_call.call_args
        env = call_kwargs.kwargs.get("env", {})
        if env:
            assert env.get("RCON_PASSWORD") == record.rcon_password


def test_launch_uses_user_provided_rcon_password(mock_launch_deps, tmp_path):
    """User-provided RCON password via env_overrides is used instead of random."""
    _game_with_rcon_pw = GameDefinition(
        name="test-rcon-game2", display_name="Test RCON Game",
        image="test/rcon-game",
        ports=[GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")],
        defaults={"EULA": "TRUE"}, default_instance_type="t3.medium", min_ram_gb=2,
        volumes=["/data"], data_paths={"world": "/data/world"},
        rcon_port=25575, rcon_password_key="RCON_PASSWORD",
    )
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=_game_with_rcon_pw, region="us-east-1",
        env_overrides={"RCON_PASSWORD": "my-custom-pw"},
    )

    assert record.rcon_password == "my-custom-pw"


def test_launch_no_rcon_password_for_game_without_rcon(mock_launch_deps, tmp_path):
    """Games without rcon_password_key get no rcon password stored."""
    _game_no_rcon = GameDefinition(
        name="test-no-rcon", display_name="Test No RCON",
        image="test/no-rcon",
        ports=[GamePort(port=27015, protocol="udp")],
        defaults={}, default_instance_type="t3.medium", min_ram_gb=1,
        volumes=["/data"], data_paths={"game": "/data"},
    )

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=_game_no_rcon, region="us-east-1")

    assert record.rcon_password == ""


def test_rcon_password_persisted_in_state(mock_launch_deps, tmp_path):
    """RCON password survives save/load round-trip through state."""
    _game_with_rcon_pw = GameDefinition(
        name="test-rcon-game3", display_name="Test RCON Game",
        image="test/rcon-game",
        ports=[GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")],
        defaults={"EULA": "TRUE"}, default_instance_type="t3.medium", min_ram_gb=2,
        volumes=["/data"], data_paths={"world": "/data/world"},
        rcon_port=25575, rcon_password_key="RCON_PASSWORD",
    )
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=_game_with_rcon_pw, region="us-east-1")

    loaded = provisioner.state.get(record.id)
    assert loaded is not None
    assert loaded.rcon_password == record.rcon_password
    assert loaded.rcon_password != ""


def test_launch_passes_disk_gb(mock_launch_deps, tmp_path):
    """launch() forwards game.disk_gb to launch_instance()."""
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=factorio, region="us-east-1")

    mock_launch_deps.mocks["launch_instance"].assert_called_once()
    call_kwargs = mock_launch_deps.mocks["launch_instance"].call_args
    assert call_kwargs.kwargs.get("disk_gb") == factorio.disk_gb


def test_launch_saves_state_early(mock_launch_deps, tmp_path):
    """State is saved with status=launching before Docker setup."""
    saved_states = []

    provisioner = Provisioner(state_dir=tmp_path)
    original_save = provisioner.state.save

    def capture_save(record):
        saved_states.append(record.status)
        original_save(record)

    provisioner.state.save = capture_save
    provisioner.launch(game=factorio, region="us-east-1")

    assert saved_states[0] == "launching"
    assert saved_states[-1] == "running"


@patch("gsm.control.provisioner.terminate_instance")
def test_launch_keyboard_interrupt_cleans_up(mock_terminate, mock_launch_deps, tmp_path):
    """KeyboardInterrupt during launch terminates instance and removes state."""
    mock_launch_deps.docker.wait_for_docker.side_effect = KeyboardInterrupt

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(KeyboardInterrupt):
        provisioner.launch(game=factorio, region="us-east-1")

    mock_terminate.assert_called_once()
    # State should be cleaned up since terminate succeeded
    assert provisioner.state.list_all() == []


def test_launch_failure_keeps_state_when_terminate_fails(mock_launch_deps, tmp_path):
    """If terminate fails during cleanup, original error propagates and state is preserved."""
    mock_launch_deps.docker.wait_for_docker.side_effect = RuntimeError("ssh broke")

    from gsm.control import provisioner as prov_module
    with patch.object(prov_module, "terminate_instance", side_effect=Exception("aws down")):
        provisioner = Provisioner(state_dir=tmp_path)
        with pytest.raises(RuntimeError, match="ssh broke"):
            provisioner.launch(game=factorio, region="us-east-1")

    # State should be preserved with launching status
    records = provisioner.state.list_all()
    assert len(records) == 1
    assert records[0].status == "launching"


@patch("gsm.control.provisioner.find_gsm_eips", return_value=[])
@patch("gsm.control.provisioner.aws_list_snapshots", return_value=[])
@patch("gsm.control.provisioner.find_gsm_instances")
def test_launch_duplicate_name_raises(mock_find, mock_snaps, mock_eips, make_server_record, tmp_path):
    """Launching with a name that already exists raises ValueError."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(
        id="existing-1", name="my-server", instance_id="i-exist",
        public_ip="1.2.3.4",
    ))

    # Reconcile must see the instance so it doesn't delete the record
    mock_find.return_value = [{
        "instance_id": "i-exist", "state": "running", "public_ip": "1.2.3.4",
        "gsm_id": "existing-1", "gsm_game": "factorio", "gsm_name": "my-server",
    }]

    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="A server named 'my-server' already exists"):
        provisioner.launch(game=factorio, region="us-east-1", name="my-server")


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_server(mock_terminate, make_server_record, tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="destroy-1", name="mc-1", instance_id="i-destroy"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("destroy-1")):
        provisioner.destroy("destroy-1")
    mock_terminate.assert_called_once_with("us-east-1", "i-destroy")
    assert state.get("destroy-1") is None


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_already_terminated(mock_terminate, make_server_record, make_client_error, tmp_path):
    """Destroy succeeds when terminate raises InvalidInstanceID.NotFound."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="gone-1", name="mc-gone", instance_id="i-gone"))
    mock_terminate.side_effect = make_client_error("InvalidInstanceID.NotFound")
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("gone-1")):
        provisioner.destroy("gone-1")  # Should not raise
    assert state.get("gone-1") is None


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_other_client_error_propagates(mock_terminate, make_server_record, make_client_error, tmp_path):
    """Destroy propagates non-NotFound ClientErrors."""
    from botocore.exceptions import ClientError
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="err-1", name="mc-err", instance_id="i-err"))
    mock_terminate.side_effect = make_client_error("UnauthorizedOperation")
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=state.get("err-1")):
        with pytest.raises(ClientError):
            provisioner.destroy("err-1")
    # State NOT deleted because error was not NotFound
    assert state.get("err-1") is not None


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_refresh_returns_none(mock_terminate, make_server_record, tmp_path):
    """Destroy succeeds when _refresh_record returns None (already gone)."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="ref-1", name="mc-ref", instance_id="i-ref"))
    provisioner = Provisioner(state_dir=tmp_path)
    with patch.object(provisioner, "_refresh_record", return_value=None):
        provisioner.destroy("ref-1")  # Should not raise
    mock_terminate.assert_not_called()


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_all_continues_on_failure(mock_terminate, make_server_record, make_client_error, tmp_path):
    """destroy_all continues when one server fails and raises summary."""
    state = ServerState(state_dir=tmp_path)
    for i in range(3):
        state.save(make_server_record(
            id=f"da-{i}", name=f"mc-{i}", instance_id=f"i-da-{i}", public_ip=f"1.2.3.{i}",
        ))

    def terminate_effect(region, instance_id):
        if instance_id == "i-da-1":
            raise make_client_error("UnauthorizedOperation")

    mock_terminate.side_effect = terminate_effect
    provisioner = Provisioner(state_dir=tmp_path)

    def refresh_side_effect(server_id):
        return provisioner.state.get(server_id)

    with patch.object(provisioner, "_refresh_record", side_effect=refresh_side_effect):
        with pytest.raises(RuntimeError, match="Failed to destroy 1 server"):
            provisioner.destroy_all()

    # 1st and 3rd should be deleted, 2nd should remain
    assert state.get("da-0") is None
    assert state.get("da-1") is not None
    assert state.get("da-2") is None


@patch("gsm.control.provisioner.terminate_instance")
def test_destroy_all_all_terminated_externally(mock_terminate, make_server_record, tmp_path):
    """destroy_all succeeds when all servers already terminated."""
    state = ServerState(state_dir=tmp_path)
    for i in range(2):
        state.save(make_server_record(
            id=f"ext-{i}", name=f"mc-{i}", instance_id=f"i-ext-{i}", public_ip=f"1.2.3.{i}",
        ))
    provisioner = Provisioner(state_dir=tmp_path)

    def refresh_and_delete(server_id):
        """Simulate _refresh_record deleting state and returning None."""
        provisioner.state.delete(server_id)
        return None

    with patch.object(provisioner, "_refresh_record", side_effect=refresh_and_delete):
        provisioner.destroy_all()  # Should not raise
    assert len(state.list_all()) == 0


# ── required_config validation ──

_lgsm_game_with_steamuser = GameDefinition(
    name="lgsm-testgame",
    display_name="Test Game (LinuxGSM)",
    image="gameservermanagers/gameserver:testgame",
    ports=[GamePort(port=27015, protocol="udp")],
    defaults={"servername": "Test"},
    default_instance_type="t3.medium",
    min_ram_gb=2,
    volumes=["/data"],
    data_paths={"config": "/data/lgsm/config-lgsm"},
    lgsm_server_code="testgameserver",
    required_config=("steamuser",),
)


def test_launch_raises_when_required_config_missing(tmp_path):
    """launch() raises ValueError before AWS calls when required config is missing."""
    provisioner = Provisioner(state_dir=tmp_path)
    with pytest.raises(ValueError, match="Missing required config key.*steamuser") as exc_info:
        provisioner.launch(game=_lgsm_game_with_steamuser, region="us-east-1")
    msg = str(exc_info.value)
    assert "--config steamuser=VALUE" in msg
    assert "gsm config lgsm-testgame --init" in msg
    assert "--config-file lgsm-testgame.cfg" in msg


def test_launch_succeeds_with_required_config_provided(mock_launch_deps, tmp_path):
    """launch() proceeds when required config is provided via overrides."""
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=_lgsm_game_with_steamuser, region="us-east-1",
        lgsm_config_overrides={"steamuser": "myaccount"},
    )
    assert record.status == "running"
    assert record.config["steamuser"] == "myaccount"


@patch("gsm.control.provisioner.register_ami_from_snapshot", return_value="ami-restored")
def test_launch_from_snapshot_skips_required_config(mock_ami_snap, mock_launch_deps, make_snapshot_record, tmp_path):
    """launch() with --from-snapshot skips required_config validation."""
    from gsm.control.state import SnapshotState

    mock_launch_deps.mocks["aws_list_snapshots"].return_value = [{"SnapshotId": "snap-aws-req", "Tags": [
        {"Key": "gsm:id", "Value": "srv-orig"},
        {"Key": "gsm:snapshot-id", "Value": "snap-req"},
    ]}]

    snap_state = SnapshotState(state_dir=tmp_path)
    snap_state.save(make_snapshot_record(
        id="snap-req", snapshot_id="snap-aws-req",
        server_name="test-orig", server_id="srv-orig",
    ))

    mock_launch_deps.docker.find_gsm_container.return_value = "gsm-lgsm-testgame-old"

    provisioner = Provisioner(state_dir=tmp_path)
    # Should NOT raise despite missing required config
    record = provisioner.launch(
        game=_lgsm_game_with_steamuser, region="us-east-1",
        from_snapshot="snap-req",
    )
    assert record.status == "running"


# ── Pause state timing + SSH cleanup tests ──


def test_pause_updates_status_before_waiter(mock_remote_deps, make_server_record, tmp_path):
    """pause() updates status to 'paused' right after stop_instance, before waiter."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="p-1", name="mc-p1", instance_id="i-p1"))
    provisioner = Provisioner(state_dir=tmp_path)

    # Make the waiter raise to simulate Ctrl+C during wait
    mock_remote_deps.mocks["wait_for_instance_stopped"].side_effect = Exception("interrupted")

    with patch.object(provisioner, "_refresh_record", return_value=state.get("p-1")):
        provisioner.pause("p-1")

    # Status should already be "paused" even though waiter failed
    assert state.get("p-1").status == "paused"


def test_pause_proceeds_after_keyboard_interrupt_during_container_stop(
    mock_remote_deps, make_server_record, tmp_path
):
    """pause() proceeds to stop_instance even if KeyboardInterrupt during container stop."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="p-2", name="mc-p2", instance_id="i-p2"))
    provisioner = Provisioner(state_dir=tmp_path)

    # Make SSH connect raise KeyboardInterrupt
    mock_remote_deps.ssh.connect.side_effect = KeyboardInterrupt

    with patch.object(provisioner, "_refresh_record", return_value=state.get("p-2")):
        provisioner.pause("p-2")

    # stop_instance should still have been called
    mock_remote_deps.mocks["stop_instance"].assert_called_once_with("us-east-1", "i-p2")
    assert state.get("p-2").status == "paused"


def test_stop_container_closes_ssh_on_exception(mock_remote_deps, make_server_record, tmp_path):
    """stop_container() closes SSH even when docker.stop raises."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="sc-1", name="mc-sc1", instance_id="i-sc1"))
    provisioner = Provisioner(state_dir=tmp_path)

    mock_remote_deps.docker.stop.side_effect = RuntimeError("docker broke")

    with patch.object(provisioner, "_refresh_record", return_value=state.get("sc-1")):
        with pytest.raises(RuntimeError, match="docker broke"):
            provisioner.stop_container("sc-1")

    mock_remote_deps.ssh.close.assert_called_once()


def test_resume_closes_ssh_on_exception(mock_remote_deps, make_server_record, tmp_path):
    """resume() closes SSH even when docker.start raises."""
    state = ServerState(state_dir=tmp_path)
    state.save(make_server_record(id="r-1", name="mc-r1", instance_id="i-r1", status="paused"))
    provisioner = Provisioner(state_dir=tmp_path)

    mock_remote_deps.docker.start.side_effect = RuntimeError("docker broke")

    with patch.object(provisioner, "_refresh_record", return_value=state.get("r-1")):
        with pytest.raises(RuntimeError, match="container failed to start"):
            provisioner.resume("r-1")

    mock_remote_deps.ssh.close.assert_called_once()
