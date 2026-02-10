from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from gsm.control.provisioner import Provisioner
from gsm.games.registry import get_game


class LaunchRequest(BaseModel):
    game: str
    instance_type: str | None = None
    region: str = "us-east-1"
    config: dict[str, str] | None = None
    name: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Game Server Maker API", version="0.1.0")
    provisioner = Provisioner()

    import gsm.games.factorio  # noqa: F401

    from gsm.games.lgsm_catalog import register_lgsm_catalog
    register_lgsm_catalog()

    @app.get("/servers")
    def list_servers():
        try:
            provisioner.reconcile(extra_regions={"us-east-1"})
        except Exception:
            pass
        return [asdict(s) for s in provisioner.state.list_all()]

    @app.get("/servers/{server_id}")
    def get_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        try:
            provisioner.reconcile(extra_regions={record.region})
        except Exception:
            pass
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server was terminated externally")
        return asdict(record)

    @app.post("/servers")
    def launch_server(req: LaunchRequest):
        game = get_game(req.game)
        if not game:
            raise HTTPException(status_code=400, detail=f"Unknown game: {req.game}")

        # Route config by game type
        env_overrides = None
        lgsm_config_overrides = None
        if req.config:
            if game.lgsm_server_code:
                lgsm_config_overrides = req.config
            else:
                env_overrides = req.config

        record = provisioner.launch(
            game=game, region=req.region, instance_type=req.instance_type,
            name=req.name, env_overrides=env_overrides,
            lgsm_config_overrides=lgsm_config_overrides,
        )
        return asdict(record)

    @app.delete("/servers/{server_id}")
    def destroy_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        provisioner.destroy(record.id)
        return {"status": "destroyed", "id": record.id}

    @app.post("/servers/{server_id}/pause")
    def pause_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        provisioner.pause(record.id)
        return {"status": "paused", "id": record.id}

    @app.post("/servers/{server_id}/stop")
    def stop_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        provisioner.stop_container(record.id)
        return {"status": "stopped", "id": record.id}

    @app.post("/servers/{server_id}/resume")
    def resume_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        updated = provisioner.resume(record.id)
        return asdict(updated)

    @app.post("/servers/{server_id}/pin")
    def pin_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        updated = provisioner.pin_ip(record.id)
        return asdict(updated)

    @app.post("/servers/{server_id}/unpin")
    def unpin_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        updated = provisioner.unpin_ip(record.id)
        return asdict(updated)

    @app.post("/servers/{server_id}/snapshot")
    def snapshot_server(server_id: str):
        record = provisioner.state.get_by_name_or_id(server_id)
        if not record:
            raise HTTPException(status_code=404, detail="Server not found")
        snap = provisioner.snapshot(record.id)
        return asdict(snap)

    @app.get("/snapshots")
    def list_snapshots():
        return [asdict(s) for s in provisioner.list_snapshots()]

    @app.delete("/snapshots/{snapshot_id}")
    def delete_snapshot(snapshot_id: str):
        snap = provisioner.snapshot_state.get(snapshot_id)
        if not snap:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        provisioner.delete_snapshot(snap.id)
        return {"status": "deleted", "id": snap.id}

    return app
