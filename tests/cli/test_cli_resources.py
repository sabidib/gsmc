from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from gsm.cli import cli


@patch("gsm.cli._make_provisioner")
def test_resources_command_paid_only(mock_make_prov):
    mock_prov = MagicMock()
    mock_make_prov.return_value = mock_prov
    mock_prov.list_all_resources.return_value = {
        "instances": [{"instance_id": "i-abc123", "state": "running", "gsm_game": "factorio", "gsm_name": "my-server", "public_ip": "1.2.3.4", "region": "us-east-1"}],
        "eips": [],
        "snapshots": [],
        "amis": [],
    }
    runner = CliRunner()
    result = runner.invoke(cli, ["resources"])
    assert result.exit_code == 0
    assert "EC2 Instances" in result.output
    assert "i-abc123" in result.output
    assert "1 resource(s) found" in result.output
    mock_prov.list_all_resources.assert_called_once_with(include_free=False)


@patch("gsm.cli._make_provisioner")
def test_resources_command_all_flag(mock_make_prov):
    mock_prov = MagicMock()
    mock_make_prov.return_value = mock_prov
    mock_prov.list_all_resources.return_value = {
        "instances": [],
        "eips": [],
        "snapshots": [],
        "amis": [],
        "security_groups": [{"group_id": "sg-123", "group_name": "gsm-factorio-sg", "vpc_id": "vpc-1", "region": "us-east-1"}],
        "key_pairs": [{"key_name": "gsm-key", "key_pair_id": "key-1", "region": "us-east-1"}],
        "ssm_parameters": [{"name": "/gsmc/active-regions", "type": "String", "value": "us-east-1"}],
    }
    runner = CliRunner()
    result = runner.invoke(cli, ["resources", "--all"])
    assert result.exit_code == 0
    assert "Security Groups" in result.output
    assert "Key Pairs" in result.output
    assert "SSM Parameters" in result.output
    assert "3 resource(s) found" in result.output
    mock_prov.list_all_resources.assert_called_once_with(include_free=True)


@patch("gsm.cli._make_provisioner")
def test_resources_command_empty(mock_make_prov):
    mock_prov = MagicMock()
    mock_make_prov.return_value = mock_prov
    mock_prov.list_all_resources.return_value = {
        "instances": [],
        "eips": [],
        "snapshots": [],
        "amis": [],
    }
    runner = CliRunner()
    result = runner.invoke(cli, ["resources"])
    assert result.exit_code == 0
    assert "No GSM resources found" in result.output


@patch("gsm.cli._make_provisioner")
def test_resources_command_error(mock_make_prov):
    mock_prov = MagicMock()
    mock_make_prov.return_value = mock_prov
    mock_prov.list_all_resources.side_effect = Exception("AWS error")
    runner = CliRunner()
    result = runner.invoke(cli, ["resources"])
    assert result.exit_code == 1
    assert "AWS error" in result.output


@patch("gsm.cli._make_provisioner")
def test_resources_command_multiple_types(mock_make_prov):
    """resources command shows multiple tables and correct total."""
    mock_prov = MagicMock()
    mock_make_prov.return_value = mock_prov
    mock_prov.list_all_resources.return_value = {
        "instances": [{"instance_id": "i-1", "state": "running", "gsm_game": "factorio", "gsm_name": "s1", "public_ip": "1.1.1.1", "region": "us-east-1"}],
        "eips": [{"allocation_id": "eipalloc-1", "public_ip": "2.2.2.2", "server_id": "srv-1", "associated": True, "region": "us-east-1"}],
        "snapshots": [{"snapshot_id": "snap-1", "state": "completed", "size_gb": 100, "server_id": "srv-1", "region": "us-east-1", "description": "test"}],
        "amis": [{"image_id": "ami-1", "name": "gsm-restore", "state": "available", "region": "us-east-1", "creation_date": "2025-01-01"}],
    }
    runner = CliRunner()
    result = runner.invoke(cli, ["resources"])
    assert result.exit_code == 0
    assert "EC2 Instances" in result.output
    assert "Elastic IPs" in result.output
    assert "EBS Snapshots" in result.output
    assert "AMIs" in result.output
    assert "4 resource(s) found" in result.output
