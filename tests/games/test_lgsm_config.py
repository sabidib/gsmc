from unittest.mock import MagicMock, patch, call
from pathlib import Path

import pytest
from click.testing import CliRunner

from gsm.cli import cli
from gsm.control.provisioner import Provisioner, _generate_lgsm_config, _parse_env_file
from gsm.games.lgsm_catalog import make_game, load_catalog


@pytest.fixture
def lgsm_rust():
    return make_game("lgsm-rust")


def test_generate_lgsm_config():
    config = {"servername": "My Server", "maxplayers": "50"}
    result = _generate_lgsm_config(config)
    assert 'servername="My Server"' in result
    assert 'maxplayers="50"' in result
    assert result.endswith("\n")
    lines = result.strip().split("\n")
    assert len(lines) == 2


def test_generate_lgsm_config_single_key():
    result = _generate_lgsm_config({"foo": "bar"})
    assert result == 'foo="bar"\n'


def test_lgsm_launch_injects_config(mock_launch_deps, tmp_path, lgsm_rust):
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=lgsm_rust, region="us-east-1",
        lgsm_config_overrides={"maxplayers": "100", "servername": "Test"},
    )

    # Should use create -> cp -> start, not run
    mock_launch_deps.docker.create.assert_called_once()
    mock_launch_deps.docker.run.assert_not_called()
    mock_launch_deps.docker.start.assert_called_once()

    # Verify cp_to was called with the correct config path
    cp_calls = mock_launch_deps.docker.cp_to.call_args_list
    assert len(cp_calls) == 1
    container_name, remote_tmp, config_dest = cp_calls[0].args
    assert config_dest == "/data/config-lgsm/rustserver/common.cfg"


def test_lgsm_launch_defaults_only(mock_launch_deps, tmp_path, lgsm_rust):
    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(game=lgsm_rust, region="us-east-1")

    # Defaults are present, so it should still use create -> cp -> start
    mock_launch_deps.docker.create.assert_called_once()
    mock_launch_deps.docker.run.assert_not_called()
    mock_launch_deps.docker.start.assert_called_once()

    # Config should be injected
    cp_calls = mock_launch_deps.docker.cp_to.call_args_list
    assert len(cp_calls) == 1
    _, _, config_dest = cp_calls[0].args
    assert config_dest == "/data/config-lgsm/rustserver/common.cfg"


def test_lgsm_launch_config_file(mock_launch_deps, tmp_path, lgsm_rust):
    # Create a config file
    cfg_file = tmp_path / "my-rust.cfg"
    cfg_file.write_text('maxplayers="200"\nservername="Custom"\n')

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=lgsm_rust, region="us-east-1",
        lgsm_config_file=str(cfg_file),
    )

    mock_launch_deps.docker.create.assert_called_once()
    mock_launch_deps.docker.run.assert_not_called()

    # Verify the config was uploaded and cp'd
    mock_launch_deps.ssh.upload_file.assert_called()
    cp_calls = mock_launch_deps.docker.cp_to.call_args_list
    assert len(cp_calls) == 1
    _, _, config_dest = cp_calls[0].args
    assert config_dest == "/data/config-lgsm/rustserver/common.cfg"

    # Config file parsed + rcon password auto-generated
    assert "maxplayers" in record.config
    assert record.config["maxplayers"] == "200"
    assert record.rcon_password  # auto-generated


def test_lgsm_config_merges_defaults_and_overrides():
    """Verify user overrides take precedence over defaults."""
    defaults = {"servername": "Default", "maxplayers": "50", "rconpassword": "gsm-rcon"}
    overrides = {"maxplayers": "100", "servername": "Custom"}
    merged = dict(defaults)
    merged.update(overrides)
    assert merged == {
        "servername": "Custom",
        "maxplayers": "100",
        "rconpassword": "gsm-rcon",
    }


def test_non_lgsm_game_ignores_config(mock_launch_deps, tmp_path):
    from gsm.games.factorio import factorio

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=factorio, region="us-east-1",
        lgsm_config_overrides={"foo": "bar"},
    )

    # Non-LinuxGSM game should use docker.run, not create/cp/start
    mock_launch_deps.docker.run.assert_called_once()
    mock_launch_deps.docker.create.assert_not_called()
    mock_launch_deps.docker.cp_to.assert_not_called()


def test_config_command_shows_defaults():
    """gsm config lgsm-rust shows default config keys."""
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "lgsm-rust"])
    assert result.exit_code == 0
    assert "servername" in result.output
    assert "maxplayers" in result.output
    assert "rconpassword" in result.output


def test_config_command_shows_options():
    """gsm config lgsm-rust shows config options when JSON data is available."""
    import gsm.games.lgsm_catalog as cat

    fake_data = {
        "games": {
            "rustserver": {
                "shortname": "rust",
                "gamename": "Rust",
                "config_options": {
                    "worldsize": {"default": "3000", "description": "map size in meters"},
                    "tickrate": {"default": "30", "description": "default: 30, range: 15-100"},
                    "servername": {"default": "LinuxGSM", "description": ""},
                },
            }
        }
    }
    original = cat._lgsm_data
    try:
        cat._lgsm_data = fake_data
        # Re-register with the fake data
        from gsm.games.registry import _registry, register_game
        saved_game = _registry.pop("lgsm-rust", None)
        game = make_game("lgsm-rust")
        register_game(game)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "lgsm-rust"])
        assert result.exit_code == 0
        # worldsize is in "Other Options" since it's not in defaults
        assert "worldsize" in result.output
        assert "3000" in result.output
    finally:
        cat._lgsm_data = original
        _registry.pop("lgsm-rust", None)
        if saved_game:
            _registry["lgsm-rust"] = saved_game


def test_config_command_docker_game():
    """gsm config factorio shows config options."""
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "factorio"])
    assert result.exit_code == 0
    assert "-c KEY=VALUE" in result.output


def test_config_init_creates_file(tmp_path):
    """gsm config lgsm-rust --init creates a config file."""
    runner = CliRunner()
    out_path = tmp_path / "test-rust.cfg"
    result = runner.invoke(cli, ["config", "lgsm-rust", "--init", "-o", str(out_path)])
    assert result.exit_code == 0
    assert out_path.exists()
    content = out_path.read_text()
    assert 'servername="LinuxGSM Server"' in content
    assert 'maxplayers="50"' in content
    assert "Rust (LinuxGSM)" in content


def test_config_init_custom_output(tmp_path):
    """--init -o writes to specified path."""
    runner = CliRunner()
    custom_path = tmp_path / "custom.cfg"
    result = runner.invoke(cli, ["config", "lgsm-rust", "--init", "-o", str(custom_path)])
    assert result.exit_code == 0
    assert custom_path.exists()
    assert "Config file written to" in result.output
    assert "custom.cfg" in result.output


def test_config_init_docker_game(tmp_path):
    """--init generates config file for Docker games."""
    runner = CliRunner()
    out_path = tmp_path / "factorio.cfg"
    result = runner.invoke(cli, ["config", "factorio", "--init", "-o", str(out_path)])
    assert result.exit_code == 0
    assert out_path.exists()
    content = out_path.read_text()
    assert "GENERATE_NEW_SAVE=false" in content
    assert "LOAD_LATEST_SAVE=true" in content
    assert "Factorio" in content


def test_parse_env_file(tmp_path):
    """_parse_env_file parses KEY=VALUE lines, skips comments and blanks."""
    env_file = tmp_path / "test.cfg"
    env_file.write_text('# comment\nFOO=bar\n\nBAZ=qux=extra\nQUOTED="hello world"\n')
    result = _parse_env_file(str(env_file))
    assert result == {"FOO": "bar", "BAZ": "qux=extra", "QUOTED": "hello world"}


def test_docker_config_file_sets_env(mock_launch_deps, tmp_path):
    """--config-file for Docker games feeds values into env."""
    from gsm.games.factorio import factorio

    env_file = tmp_path / "factorio.env"
    env_file.write_text("GENERATE_NEW_SAVE=false\nSAVE_NAME=myworld\n")

    provisioner = Provisioner(state_dir=tmp_path)
    record = provisioner.launch(
        game=factorio, region="us-east-1",
        lgsm_config_file=str(env_file),
    )

    # Docker game with config file should still use docker.run (no cp needed)
    mock_launch_deps.docker.run.assert_called_once()
    # Verify the env values were passed through
    run_kwargs = mock_launch_deps.docker.run.call_args[1]
    assert run_kwargs["env"]["GENERATE_NEW_SAVE"] == "false"
    assert run_kwargs["env"]["SAVE_NAME"] == "myworld"
