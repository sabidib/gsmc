from unittest.mock import patch, MagicMock
from click.testing import CliRunner
from gsm.cli import cli


@patch("gsm.cli.Provisioner")
def test_pause_command(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()

    runner = CliRunner()
    result = runner.invoke(cli, ["pause", "mc-test"])
    assert result.exit_code == 0
    assert "paused" in result.output.lower()
    mock_prov.pause.assert_called_once_with("srv-1")


@patch("gsm.cli.Provisioner")
def test_pause_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["pause", "nope"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()


@patch("gsm.cli.Provisioner")
def test_resume_command(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    record = make_server_record(status="paused")
    mock_prov.state.get_by_name_or_id.return_value = record
    resumed = make_server_record(status="running", public_ip="54.9.8.7")
    mock_prov.resume.return_value = resumed

    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "mc-test"])
    assert result.exit_code == 0
    assert "resumed" in result.output.lower()
    assert "54.9.8.7" in result.output
    mock_prov.resume.assert_called_once_with("srv-1")


@patch("gsm.cli.Provisioner")
def test_resume_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "nope"])
    assert result.exit_code == 1


@patch("gsm.cli.Provisioner")
def test_snapshot_command(mock_prov_cls, make_server_record, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_prov.snapshot.return_value = make_snapshot_record()

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot", "mc-test"])
    assert result.exit_code == 0
    assert "snapshot created" in result.output.lower()
    assert "snap-1" in result.output
    mock_prov.snapshot.assert_called_once_with("srv-1")


@patch("gsm.cli.Provisioner")
def test_snapshots_command(mock_prov_cls, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.list_snapshots.return_value = [make_snapshot_record()]

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshots"])
    assert result.exit_code == 0
    assert "snap-1" in result.output


@patch("gsm.cli.Provisioner")
def test_snapshots_empty(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.list_snapshots.return_value = []

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshots"])
    assert result.exit_code == 0
    assert "no snapshots" in result.output.lower()


@patch("gsm.cli.Provisioner")
def test_snapshot_delete_command(mock_prov_cls, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.snapshot_state.get.return_value = make_snapshot_record()

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot-delete", "snap-1", "--yes"])
    assert result.exit_code == 0
    assert "deleted" in result.output.lower()
    mock_prov.delete_snapshot.assert_called_once_with("snap-1")


@patch("gsm.cli.Provisioner")
def test_snapshot_delete_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.snapshot_state.get.return_value = None

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot-delete", "nope", "--yes"])
    assert result.exit_code == 1
    assert "not found" in result.output.lower()
