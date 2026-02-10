from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from gsm.cli import (
    _complete_command,
    _complete_game,
    _complete_server,
    _complete_snapshot,
    cli,
)


def _make_server_record(name, id, game="factorio", status="running"):
    rec = MagicMock()
    rec.name = name
    rec.id = id
    rec.game = game
    rec.status = status
    return rec


def _make_snapshot_record(id, game="factorio", server_name="my-server"):
    rec = MagicMock()
    rec.id = id
    rec.game = game
    rec.server_name = server_name
    return rec


class TestCompleteServer:
    @patch("gsm.control.state.ServerState")
    def test_returns_server_names(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_server_record("alpha", "id-aaa"),
            _make_server_record("bravo", "id-bbb"),
        ]
        items = _complete_server(None, None, "")
        names = [i.value for i in items]
        assert "alpha" in names
        assert "bravo" in names

    @patch("gsm.control.state.ServerState")
    def test_filters_by_name_prefix(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_server_record("alpha", "id-aaa"),
            _make_server_record("bravo", "id-bbb"),
        ]
        items = _complete_server(None, None, "al")
        names = [i.value for i in items]
        assert names == ["alpha"]

    @patch("gsm.control.state.ServerState")
    def test_filters_by_id_prefix(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_server_record("alpha", "id-aaa"),
            _make_server_record("bravo", "id-bbb"),
        ]
        items = _complete_server(None, None, "id-b")
        names = [i.value for i in items]
        assert names == ["bravo"]

    @patch("gsm.control.state.ServerState")
    def test_empty_state(self, mock_cls):
        mock_cls.return_value.list_all.return_value = []
        items = _complete_server(None, None, "")
        assert items == []

    @patch("gsm.control.state.ServerState")
    def test_help_text(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_server_record("alpha", "id-aaa", game="factorio", status="paused"),
        ]
        items = _complete_server(None, None, "")
        assert items[0].help == "factorio - paused"


class TestCompleteGame:
    def test_returns_game_names(self):
        items = _complete_game(None, None, "")
        names = [i.value for i in items]
        assert "factorio" in names

    def test_filters_by_prefix(self):
        items = _complete_game(None, None, "fact")
        names = [i.value for i in items]
        assert "factorio" in names

    def test_no_match(self):
        items = _complete_game(None, None, "zzz")
        assert items == []


class TestCompleteSnapshot:
    @patch("gsm.control.state.SnapshotState")
    def test_returns_snapshot_ids(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_snapshot_record("snap-001", game="factorio", server_name="my-factorio"),
            _make_snapshot_record("snap-002", game="factorio", server_name="mc-srv"),
        ]
        items = _complete_snapshot(None, None, "")
        ids = [i.value for i in items]
        assert "snap-001" in ids
        assert "snap-002" in ids

    @patch("gsm.control.state.SnapshotState")
    def test_filters_by_prefix(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_snapshot_record("snap-001"),
            _make_snapshot_record("snap-002"),
        ]
        items = _complete_snapshot(None, None, "snap-002")
        ids = [i.value for i in items]
        assert ids == ["snap-002"]

    @patch("gsm.control.state.SnapshotState")
    def test_help_text(self, mock_cls):
        mock_cls.return_value.list_all.return_value = [
            _make_snapshot_record("snap-001", game="factorio", server_name="my-factorio"),
        ]
        items = _complete_snapshot(None, None, "")
        assert items[0].help == "factorio - my-factorio"


class TestCompleteCommand:
    def test_returns_command_names(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            ctx = MagicMock()
            items = _complete_command(ctx, None, "")
            names = [i.value for i in items]
            assert "launch" in names
            assert "info" in names
            assert "destroy" in names
            assert "completion" in names

    def test_filters_by_prefix(self):
        ctx = MagicMock()
        items = _complete_command(ctx, None, "la")
        names = [i.value for i in items]
        assert "launch" in names
        assert "info" not in names


class TestCompletionCommand:
    def test_bash_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "bash"])
        assert result.exit_code == 0
        assert "_GSMC_COMPLETE" in result.output

    def test_zsh_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "zsh"])
        assert result.exit_code == 0
        assert "_GSMC_COMPLETE" in result.output

    def test_fish_output(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "fish"])
        assert result.exit_code == 0
        assert "_GSMC_COMPLETE" in result.output

    def test_invalid_shell(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["completion", "powershell"])
        assert result.exit_code != 0
