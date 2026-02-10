from unittest.mock import MagicMock, patch
from pathlib import Path

from gsm.control.ssh import SSHClient, ensure_key_pair


def test_ssh_client_init():
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem", username="ec2-user")
    assert client.host == "1.2.3.4"
    assert client.username == "ec2-user"


@patch("gsm.control.ssh.paramiko.SSHClient")
def test_ssh_run_command(mock_paramiko_cls):
    mock_ssh = MagicMock()
    mock_paramiko_cls.return_value = mock_ssh
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"hello\n"
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem")
    client.connect()
    exit_code, output = client.run("echo hello")
    assert exit_code == 0
    assert output == "hello\n"


@patch("gsm.control.ssh.paramiko.SSHClient")
def test_ssh_run_captures_stderr(mock_paramiko_cls):
    mock_ssh = MagicMock()
    mock_paramiko_cls.return_value = mock_ssh
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b""
    mock_stdout.channel.recv_exit_status.return_value = 1
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b"Error: manifest unknown\n"
    mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem")
    client.connect()
    exit_code, output = client.run("docker pull bad-image")
    assert exit_code == 1
    assert "manifest unknown" in output


@patch("gsm.control.ssh.paramiko.SSHClient")
def test_ssh_run_combines_stdout_and_stderr(mock_paramiko_cls):
    mock_ssh = MagicMock()
    mock_paramiko_cls.return_value = mock_ssh
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"some output\n"
    mock_stdout.channel.recv_exit_status.return_value = 1
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b"some error\n"
    mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem")
    client.connect()
    exit_code, output = client.run("some command")
    assert exit_code == 1
    assert "some output" in output
    assert "some error" in output


@patch("gsm.control.ssh.paramiko.SSHClient")
def test_ssh_run_debug_callback(mock_paramiko_cls):
    mock_ssh = MagicMock()
    mock_paramiko_cls.return_value = mock_ssh
    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"ok\n"
    mock_stdout.channel.recv_exit_status.return_value = 0
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = b""
    mock_ssh.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)

    debug_messages = []
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem", on_debug=debug_messages.append)
    client.connect()
    client.run("echo ok")
    assert len(debug_messages) == 2
    assert "$ echo ok" in debug_messages[0]
    assert "exit=0" in debug_messages[1]


@patch("gsm.control.ssh.paramiko.SSHClient")
def test_ssh_connect_and_close(mock_paramiko_cls):
    mock_ssh = MagicMock()
    mock_paramiko_cls.return_value = mock_ssh
    client = SSHClient(host="1.2.3.4", key_path="/tmp/test.pem")
    client.connect()
    client.close()
    mock_ssh.connect.assert_called_once()
    mock_ssh.close.assert_called_once()


def test_ensure_key_pair_creates_key(tmp_path):
    key_dir = tmp_path / "keys"
    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2 = MagicMock()
        mock_boto3.client.return_value = mock_ec2
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}
        key_path = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert key_path.exists()
        assert key_path.name == "gsm-key.pem"
