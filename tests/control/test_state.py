from gsm.control.state import ServerState, ServerRecord, SnapshotState, SnapshotRecord


def test_server_record_creation():
    record = ServerRecord(
        id="abc-123", game="factorio", name="my-server",
        instance_id="i-0123456789abcdef0", region="us-east-1",
        public_ip="1.2.3.4", ports={"34197/udp": 34197, "27015/tcp": 27015},
        status="running", security_group_id="sg-12345",
    )
    assert record.id == "abc-123"
    assert record.connection_string == "1.2.3.4:34197"


def test_save_and_load_server(tmp_path):
    state = ServerState(state_dir=tmp_path)
    record = ServerRecord(
        id="test-1", game="factorio", name="test-mc", instance_id="i-abc",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    )
    state.save(record)
    loaded = state.get("test-1")
    assert loaded is not None
    assert loaded.game == "factorio"


def test_list_servers(tmp_path):
    state = ServerState(state_dir=tmp_path)
    for i in range(3):
        state.save(ServerRecord(
            id=f"test-{i}", game="factorio", name=f"mc-{i}", instance_id=f"i-{i}",
            region="us-east-1", public_ip=f"1.2.3.{i}", ports={"34197/udp": 34197},
            status="running", security_group_id="sg-123",
        ))
    assert len(state.list_all()) == 3


def test_delete_server(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="del-1", game="factorio", name="fact-server", instance_id="i-del",
        region="us-west-2", public_ip="5.6.7.8", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-456",
    ))
    state.delete("del-1")
    assert state.get("del-1") is None


def test_get_by_name(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="name-1", game="factorio", name="my-mc", instance_id="i-name",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    ))
    found = state.get_by_name_or_id("my-mc")
    assert found is not None and found.id == "name-1"


def test_get_by_partial_id(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="abcdef-123456", game="factorio", name="fact-1", instance_id="i-abc",
        region="us-east-1", public_ip="1.1.1.1", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-789",
    ))
    found = state.get_by_name_or_id("abcdef")
    assert found is not None and found.id == "abcdef-123456"


def test_update_status(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="upd-1", game="factorio", name="fact-upd", instance_id="i-upd",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    ))
    state.update_status("upd-1", "stopped")
    assert state.get("upd-1").status == "stopped"


def test_update_field(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="field-1", game="factorio", name="fact-field", instance_id="i-field",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    ))
    state.update_field("field-1", "public_ip", "9.8.7.6")
    assert state.get("field-1").public_ip == "9.8.7.6"


def test_name_exists_true(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="ne-1", game="factorio", name="my-server", instance_id="i-ne",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    ))
    assert state.name_exists("my-server") is True


def test_name_exists_false(tmp_path):
    state = ServerState(state_dir=tmp_path)
    state.save(ServerRecord(
        id="ne-2", game="factorio", name="other-server", instance_id="i-ne2",
        region="us-east-1", public_ip="1.2.3.4", ports={"34197/udp": 34197},
        status="running", security_group_id="sg-123",
    ))
    assert state.name_exists("nonexistent") is False


def test_server_record_backward_compat_no_eip_fields(tmp_path):
    """Old servers.json without EIP fields loads with defaults."""
    import json
    state = ServerState(state_dir=tmp_path)
    old_data = {
        "srv-old": {
            "id": "srv-old", "game": "factorio", "name": "fact-old",
            "instance_id": "i-old", "region": "us-east-1",
            "public_ip": "1.2.3.4", "ports": {"34197/udp": 34197},
            "status": "running", "security_group_id": "sg-old",
            "launch_time": "2025-01-01T00:00:00+00:00",
            "container_name": "gsm-factorio-srv-old",
            "rcon_password": "", "config": {},
        }
    }
    (tmp_path / "servers.json").write_text(json.dumps(old_data))
    loaded = state.get("srv-old")
    assert loaded is not None
    assert loaded.eip_allocation_id == ""
    assert loaded.eip_public_ip == ""


def test_snapshot_record_creation():
    record = SnapshotRecord(
        id="snap-rec-1", snapshot_id="snap-abc123", game="factorio",
        server_name="my-mc", server_id="srv-123", region="us-east-1",
        status="completed",
    )
    assert record.id == "snap-rec-1"
    assert record.snapshot_id == "snap-abc123"
    assert record.created_at != ""


def test_snapshot_save_and_load(tmp_path):
    state = SnapshotState(state_dir=tmp_path)
    record = SnapshotRecord(
        id="snap-1", snapshot_id="snap-aws1", game="factorio",
        server_name="fact-srv", server_id="srv-1", region="us-west-2",
        status="completed",
    )
    state.save(record)
    loaded = state.get("snap-1")
    assert loaded is not None
    assert loaded.snapshot_id == "snap-aws1"
    assert loaded.game == "factorio"


def test_snapshot_list(tmp_path):
    state = SnapshotState(state_dir=tmp_path)
    for i in range(3):
        state.save(SnapshotRecord(
            id=f"snap-{i}", snapshot_id=f"snap-aws{i}", game="factorio",
            server_name=f"mc-{i}", server_id=f"srv-{i}", region="us-east-1",
            status="completed",
        ))
    assert len(state.list_all()) == 3


def test_snapshot_delete(tmp_path):
    state = SnapshotState(state_dir=tmp_path)
    state.save(SnapshotRecord(
        id="snap-del", snapshot_id="snap-awsdel", game="factorio",
        server_name="fact-del", server_id="srv-del", region="us-east-1",
        status="completed",
    ))
    state.delete("snap-del")
    assert state.get("snap-del") is None


def test_snapshot_record_with_metadata(tmp_path):
    """SnapshotRecord with env/lgsm_config/rcon_password round-trips through save/load."""
    state = SnapshotState(state_dir=tmp_path)
    record = SnapshotRecord(
        id="snap-meta", snapshot_id="snap-aws-meta", game="factorio",
        server_name="fact-meta", server_id="srv-meta", region="us-east-1",
        status="completed",
        config={"EULA": "TRUE", "RCON_PASSWORD": "secret123", "ip": "0.0.0.0", "port": "27015"},
        rcon_password="secret123",
    )
    state.save(record)
    loaded = state.get("snap-meta")
    assert loaded is not None
    assert loaded.config == {"EULA": "TRUE", "RCON_PASSWORD": "secret123", "ip": "0.0.0.0", "port": "27015"}
    assert loaded.rcon_password == "secret123"


def test_snapshot_record_backward_compat(tmp_path):
    """Old snapshots without metadata fields load with defaults."""
    import json
    state = SnapshotState(state_dir=tmp_path)
    # Simulate an old snapshot record without metadata fields
    old_data = {
        "snap-old": {
            "id": "snap-old", "snapshot_id": "snap-aws-old", "game": "factorio",
            "server_name": "fact-old", "server_id": "srv-old", "region": "us-west-2",
            "status": "completed", "created_at": "2025-01-01T00:00:00+00:00",
        }
    }
    (tmp_path / "snapshots.json").write_text(json.dumps(old_data))
    loaded = state.get("snap-old")
    assert loaded is not None
    assert loaded.config == {}
    assert loaded.rcon_password == ""
