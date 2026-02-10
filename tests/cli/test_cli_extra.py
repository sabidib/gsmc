from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from gsm.cli import cli


@patch("gsm.cli.Provisioner")
@patch("gsm.cli.RemoteDocker")
def test_exec_command(mock_docker_cls, mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_ssh = MagicMock()
    mock_prov.get_ssh_client.return_value = mock_ssh
    mock_docker = MagicMock()
    mock_docker_cls.return_value = mock_docker
    mock_docker.exec.return_value = (0, "command output")
    runner = CliRunner()
    result = runner.invoke(cli, ["exec", "srv-1", "ls", "/data"])
    assert result.exit_code == 0
    assert "command output" in result.output


@patch("gsm.cli.Provisioner")
@patch("gsm.cli.RemoteDocker")
def test_upload_command(mock_docker_cls, mock_prov_cls, make_server_record, tmp_path):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_ssh = MagicMock()
    mock_prov.get_ssh_client.return_value = mock_ssh
    mock_docker = MagicMock()
    mock_docker_cls.return_value = mock_docker
    test_file = tmp_path / "test.txt"
    test_file.write_text("hello")
    runner = CliRunner()
    result = runner.invoke(cli, ["upload", "srv-1", str(test_file), "/data/test.txt"])
    assert result.exit_code == 0
