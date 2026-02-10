from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from gsm.cli import cli


@patch("gsm.cli.Provisioner")
def test_list_command(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.list_all.return_value = [make_server_record()]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "factorio" in result.output
    mock_prov.reconcile.assert_called_once()


@patch("gsm.cli.Provisioner")
def test_list_command_reconcile_failure(mock_prov_cls, make_server_record):
    """Reconcile failure shows warning but still lists cached servers."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.reconcile.side_effect = Exception("aws error")
    mock_prov.state.list_all.return_value = [make_server_record()]
    runner = CliRunner()
    result = runner.invoke(cli, ["list"])
    assert result.exit_code == 0
    assert "Warning" in result.output
    assert "factorio" in result.output


@patch("gsm.cli.Provisioner")
def test_info_command(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    runner = CliRunner()
    result = runner.invoke(cli, ["info", "srv-1"])
    assert result.exit_code == 0
    assert "54.1.2.3" in result.output
    mock_prov.reconcile.assert_called_once()


@patch("gsm.cli.RemoteDocker")
@patch("gsm.cli.Provisioner")
def test_logs_follow_flag(mock_prov_cls, mock_docker_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_docker = MagicMock()
    mock_docker_cls.return_value = mock_docker
    mock_docker.logs_follow.return_value = iter(["hello\n"])
    runner = CliRunner()
    result = runner.invoke(cli, ["logs", "-f", "srv-1"])
    assert result.exit_code == 0
    mock_docker.logs_follow.assert_called_once_with("gsm-factorio-srv-1", tail=None)


@patch("gsm.cli.Provisioner")
def test_launch_duplicate_name_error(mock_prov_cls):
    """CLI surfaces duplicate name error."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.launch.side_effect = ValueError("A server named 'my-server' already exists")
    runner = CliRunner()
    result = runner.invoke(cli, ["launch", "factorio", "--name", "my-server"])
    assert result.exit_code == 1
    assert "A server named 'my-server' already exists" in result.output


@patch("gsm.cli.Provisioner")
def test_stop_command_delegates_to_provisioner(mock_prov_cls, make_server_record):
    """Stop command delegates to provisioner.stop_container."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    runner = CliRunner()
    result = runner.invoke(cli, ["stop", "srv-1"])
    assert result.exit_code == 0
    mock_prov.stop_container.assert_called_once_with("srv-1")
    assert "stopped" in result.output.lower()


@patch("gsm.cli.Provisioner")
def test_destroy_command(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", "srv-1"], input="y\n")
    assert result.exit_code == 0
