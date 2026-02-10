from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from gsm.cli import cli


@patch("gsm.cli.Provisioner")
def test_launch_keyboard_interrupt_shows_interrupted(mock_prov_cls, make_server_record):
    """KeyboardInterrupt during launch shows 'Interrupted.' and exits 130."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.launch.side_effect = KeyboardInterrupt

    runner = CliRunner()
    result = runner.invoke(cli, ["launch", "factorio"])
    assert result.exit_code == 130
    assert "Interrupted." in result.output


@patch("gsm.cli.Provisioner")
def test_pause_keyboard_interrupt_shows_interrupted(mock_prov_cls, make_server_record):
    """KeyboardInterrupt during pause shows 'Interrupted.' and exits 130."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_prov.pause.side_effect = KeyboardInterrupt

    runner = CliRunner()
    result = runner.invoke(cli, ["pause", "mc-test"])
    assert result.exit_code == 130
    assert "Interrupted." in result.output


@patch("gsm.cli.Provisioner")
def test_resume_keyboard_interrupt_shows_interrupted(mock_prov_cls, make_server_record):
    """KeyboardInterrupt during resume shows 'Interrupted.' and exits 130."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record(status="paused")
    mock_prov.resume.side_effect = KeyboardInterrupt

    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "mc-test"])
    assert result.exit_code == 130
    assert "Interrupted." in result.output


@patch("gsm.cli.Provisioner")
def test_destroy_keyboard_interrupt_shows_interrupted(mock_prov_cls, make_server_record):
    """KeyboardInterrupt during destroy shows 'Interrupted.' and exits 130."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_prov.destroy.side_effect = KeyboardInterrupt

    runner = CliRunner()
    result = runner.invoke(cli, ["destroy", "-y", "mc-test"])
    assert result.exit_code == 130
    assert "Interrupted." in result.output


@patch("gsm.cli.Provisioner")
def test_snapshot_keyboard_interrupt_shows_interrupted(mock_prov_cls, make_server_record):
    """KeyboardInterrupt during snapshot shows 'Interrupted.' and exits 130."""
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_prov.snapshot.side_effect = KeyboardInterrupt

    runner = CliRunner()
    result = runner.invoke(cli, ["snapshot", "mc-test"])
    assert result.exit_code == 130
    assert "Interrupted." in result.output
