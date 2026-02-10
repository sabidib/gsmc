import pytest

from gsm.control.provisioner import Provisioner
from gsm.games.lgsm_catalog import make_game


@pytest.fixture
def lgsm_rust():
    return make_game("lgsm-rust")


def test_lgsm_launch_adds_restart_policy(mock_launch_deps, tmp_path, lgsm_rust):
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=lgsm_rust, region="us-east-1")

    assert record.game == "lgsm-rust"
    assert record.status == "running"
    # LinuxGSM games with default config use create -> cp -> start flow
    mock_launch_deps.docker.create.assert_called_once()
    create_kwargs = mock_launch_deps.docker.create.call_args
    extra_args = create_kwargs.kwargs.get("extra_args")
    assert "--restart unless-stopped" in extra_args
    mock_launch_deps.docker.start.assert_called_once()


def test_non_lgsm_launch_no_restart_policy(mock_launch_deps, tmp_path):
    from gsm.games.factorio import factorio

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=factorio, region="us-east-1")

    mock_launch_deps.docker.run.assert_called_once()
    run_kwargs = mock_launch_deps.docker.run.call_args
    extra_args = run_kwargs.kwargs.get("extra_args")
    assert extra_args is None


def test_lgsm_launch_pulls_correct_image(mock_launch_deps, tmp_path, lgsm_rust):
    provisioner = Provisioner(state_dir=tmp_path)
    provisioner.launch(game=lgsm_rust, region="us-east-1")

    mock_launch_deps.docker.pull.assert_called_once_with("gameservermanagers/gameserver:rust")
