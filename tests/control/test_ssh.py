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


def _mock_boto3_clients(mock_boto3):
    """Set up mock boto3 to return separate EC2 and SSM clients."""
    mock_ec2 = MagicMock()
    mock_ssm = MagicMock()

    def client_factory(service="ec2", **kwargs):
        if service == "ssm":
            return mock_ssm
        return mock_ec2

    mock_boto3.client.side_effect = client_factory
    return mock_ec2, mock_ssm


def test_ensure_key_pair_creates_key(tmp_path):
    """No local key, no SSM key → generates new key and stores in SSM."""
    key_dir = tmp_path / "keys"
    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        # SSM has no key
        mock_ssm.get_parameter.side_effect = Exception("ParameterNotFound")
        mock_ssm.put_parameter.return_value = {}
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        key_path = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert key_path.exists()
        assert key_path.name == "gsm-key.pem"
        # Key was uploaded to SSM
        mock_ssm.put_parameter.assert_called_once()


def test_ensure_key_pair_fetches_from_ssm(tmp_path):
    """No local key, SSM has key → fetches from SSM instead of generating."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir(parents=True)
    # Create a "remote" key to put in SSM
    import paramiko as _paramiko
    import io
    remote_key = _paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    remote_key.write_private_key(buf)
    ssm_key_pem = buf.getvalue()

    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": ssm_key_pem}
        }
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        key_path = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert key_path.exists()
        assert key_path.read_text() == ssm_key_pem


def test_ensure_key_pair_ssm_unavailable_falls_back(tmp_path):
    """No local key, SSM fails → generates locally without SSM."""
    key_dir = tmp_path / "keys"
    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        # SSM completely unavailable
        mock_ssm.get_parameter.side_effect = Exception("AccessDenied")
        mock_ssm.put_parameter.side_effect = Exception("AccessDenied")
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        key_path = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert key_path.exists()
        assert key_path.name == "gsm-key.pem"


def test_ensure_key_pair_local_key_exists_uploads_to_ssm(tmp_path):
    """Local key exists, SSM empty → uploads local key to SSM."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir(parents=True)
    key_path = key_dir / "gsm-key.pem"

    import paramiko as _paramiko
    key = _paramiko.RSAKey.generate(2048)
    key.write_private_key_file(str(key_path))

    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        # SSM has no key
        mock_ssm.get_parameter.side_effect = Exception("ParameterNotFound")
        mock_ssm.put_parameter.return_value = {}
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        result = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert result == key_path
        # SSM should be checked and local key uploaded
        mock_ssm.get_parameter.assert_called_once()
        mock_ssm.put_parameter.assert_called_once()
        # EC2 key pair should be imported
        mock_ec2.import_key_pair.assert_called_once()


def test_ensure_key_pair_ssm_overrides_local_key(tmp_path):
    """Local key exists, SSM has different key → SSM wins."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir(parents=True)
    key_path = key_dir / "gsm-key.pem"

    import paramiko as _paramiko
    import io

    # Local key
    local_key = _paramiko.RSAKey.generate(2048)
    local_key.write_private_key_file(str(key_path))
    old_content = key_path.read_text()

    # Different key in SSM
    ssm_key = _paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    ssm_key.write_private_key(buf)
    ssm_pem = buf.getvalue()

    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        mock_ssm.get_parameter.return_value = {
            "Parameter": {"Value": ssm_pem}
        }
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        ensure_key_pair("us-east-1", key_dir=key_dir)
        # Local key should be overwritten with SSM key
        assert key_path.read_text() == ssm_pem
        assert key_path.read_text() != old_content


def test_ensure_key_pair_skips_reimport_when_fingerprint_matches(tmp_path):
    """Local key exists, EC2 key pair matches → no delete+import."""
    key_dir = tmp_path / "keys"
    key_dir.mkdir(parents=True)
    key_path = key_dir / "gsm-key.pem"

    import paramiko as _paramiko
    key = _paramiko.RSAKey.generate(2048)
    key.write_private_key_file(str(key_path))

    from gsm.control.ssh import _compute_fingerprint
    local_fp = _compute_fingerprint(key_path)

    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        # EC2 key pair exists and fingerprint matches
        mock_ec2.describe_key_pairs.return_value = {
            "KeyPairs": [{"KeyFingerprint": local_fp}]
        }

        result = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert result == key_path
        # Should NOT have deleted or imported
        mock_ec2.delete_key_pair.assert_not_called()
        mock_ec2.import_key_pair.assert_not_called()


def test_ensure_key_pair_race_condition_converges(tmp_path):
    """Two machines race to store key — loser fetches winner's key."""
    key_dir = tmp_path / "keys"

    # Create the "winner's" key that will be in SSM after the race
    import paramiko as _paramiko
    import io
    winner_key = _paramiko.RSAKey.generate(2048)
    buf = io.StringIO()
    winner_key.write_private_key(buf)
    winner_pem = buf.getvalue()

    with patch("gsm.control.ssh.boto3") as mock_boto3:
        mock_ec2, mock_ssm = _mock_boto3_clients(mock_boto3)
        # First get_parameter: no key yet
        # Second get_parameter (after failed put): winner's key is there
        mock_ssm.get_parameter.side_effect = [
            Exception("ParameterNotFound"),
            {"Parameter": {"Value": winner_pem}},
        ]
        # put_parameter fails (another machine stored first)
        mock_ssm.put_parameter.side_effect = Exception("ParameterAlreadyExists")
        # EC2 has no key pair
        mock_ec2.describe_key_pairs.side_effect = Exception("not found")
        mock_ec2.import_key_pair.return_value = {}

        key_path = ensure_key_pair("us-east-1", key_dir=key_dir)
        assert key_path.exists()
        # Should have the winner's key
        assert key_path.read_text() == winner_pem
