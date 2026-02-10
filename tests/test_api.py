from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@patch("gsm.api.Provisioner")
def test_list_servers(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.list_all.return_value = [make_server_record()]
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.get("/servers")
    assert response.status_code == 200
    assert len(response.json()) == 1


@patch("gsm.api.Provisioner")
def test_get_server(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.get("/servers/srv-1")
    assert response.status_code == 200
    assert response.json()["public_ip"] == "54.1.2.3"


@patch("gsm.api.Provisioner")
def test_get_server_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = None
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.get("/servers/nonexistent")
    assert response.status_code == 404


@patch("gsm.api.Provisioner")
def test_delete_server(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.delete("/servers/srv-1")
    assert response.status_code == 200
    mock_prov.destroy.assert_called_once_with("srv-1")


@patch("gsm.api.Provisioner")
def test_stop_server_api(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.post("/servers/srv-1/stop")
    assert response.status_code == 200
    assert response.json()["status"] == "stopped"
    mock_prov.stop_container.assert_called_once_with("srv-1")


@patch("gsm.api.Provisioner")
def test_pause_server(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.post("/servers/srv-1/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    mock_prov.pause.assert_called_once_with("srv-1")


@patch("gsm.api.Provisioner")
def test_pause_server_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = None
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.post("/servers/nope/pause")
    assert response.status_code == 404


@patch("gsm.api.Provisioner")
def test_resume_server(mock_prov_cls, make_server_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    record = make_server_record()
    mock_prov.state.get_by_name_or_id.return_value = record
    resumed = make_server_record(public_ip="54.9.8.7")
    mock_prov.resume.return_value = resumed
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.post("/servers/srv-1/resume")
    assert response.status_code == 200
    assert response.json()["public_ip"] == "54.9.8.7"
    mock_prov.resume.assert_called_once_with("srv-1")


@patch("gsm.api.Provisioner")
def test_snapshot_server(mock_prov_cls, make_server_record, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.state.get_by_name_or_id.return_value = make_server_record()
    mock_prov.snapshot.return_value = make_snapshot_record(snapshot_id="snap-aws-api")
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.post("/servers/srv-1/snapshot")
    assert response.status_code == 200
    assert response.json()["snapshot_id"] == "snap-aws-api"
    mock_prov.snapshot.assert_called_once_with("srv-1")


@patch("gsm.api.Provisioner")
def test_list_snapshots(mock_prov_cls, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.list_snapshots.return_value = [make_snapshot_record()]
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.get("/snapshots")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == "snap-1"


@patch("gsm.api.Provisioner")
def test_delete_snapshot(mock_prov_cls, make_snapshot_record):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.snapshot_state.get.return_value = make_snapshot_record()
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.delete("/snapshots/snap-1")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    mock_prov.delete_snapshot.assert_called_once_with("snap-1")


@patch("gsm.api.Provisioner")
def test_delete_snapshot_not_found(mock_prov_cls):
    mock_prov = MagicMock()
    mock_prov_cls.return_value = mock_prov
    mock_prov.snapshot_state.get.return_value = None
    from gsm.api import create_app
    app = create_app()
    client = TestClient(app)
    response = client.delete("/snapshots/nope")
    assert response.status_code == 404
