from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from gsm.cli import cli


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0


def test_games_command():
    runner = CliRunner()
    result = runner.invoke(cli, ["games"])
    assert result.exit_code == 0


def test_games_command_shows_factorio():
    runner = CliRunner()
    result = runner.invoke(cli, ["games"])
    assert result.exit_code == 0
    assert "factorio" in result.output.lower()


@patch("gsm.cli.Provisioner")
def test_config_flag_works_for_docker_game(mock_prov_cls, make_server_record):
    """Verify -c routes to env_overrides for Docker games."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.launch.return_value = make_server_record()
    runner = CliRunner()
    result = runner.invoke(cli, ["launch", "factorio", "-c", "FOO=bar"])
    assert result.exit_code == 0
    call_kwargs = mock_prov.launch.call_args[1]
    assert call_kwargs["env_overrides"] == {"FOO": "bar"}
    assert call_kwargs["lgsm_config_overrides"] is None


@patch("gsm.cli.Provisioner")
def test_config_file_works_for_docker_game(mock_prov_cls, make_server_record, tmp_path):
    """Verify --config-file is accepted for Docker games."""
    cfg = tmp_path / "test.cfg"
    cfg.write_text("FOO=bar\n")
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.launch.return_value = make_server_record()
    runner = CliRunner()
    result = runner.invoke(cli, ["launch", "factorio", "--config-file", str(cfg)])
    assert result.exit_code == 0
    call_kwargs = mock_prov.launch.call_args[1]
    assert call_kwargs["lgsm_config_file"] == str(cfg)
