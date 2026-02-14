import uuid

import boto3
from botocore.exceptions import ClientError

from gsm.aws.ami import get_latest_al2023_ami
from gsm.aws.ec2 import (
    find_gsm_instances,
    launch_instance,
    terminate_instance,
    wait_for_instance_running,
    get_instance_public_ip,
    stop_instance,
    start_instance,
    wait_for_instance_stopped,
    set_instance_tag,
    delete_instance_tag,
)
from gsm.aws.security_groups import get_or_create_security_group
from gsm.control.docker import RemoteDocker
from gsm.control.ssh import SSHClient, ensure_key_pair, KEY_NAME
from gsm.aws.ebs import (
    create_snapshot,
    wait_for_snapshot_complete,
    delete_snapshot as aws_delete_snapshot,
    find_amis_using_snapshot,
    register_ami_from_snapshot,
    deregister_ami,
    list_snapshots as aws_list_snapshots,
)
from gsm.aws.eip import (
    allocate_eip,
    associate_eip,
    disassociate_eip,
    release_eip,
    find_gsm_eips,
)
from gsm.aws.ec2 import get_instance_root_volume_id
from gsm.control.state import ServerState, ServerRecord, SnapshotState, SnapshotRecord
from gsm.games.registry import GameDefinition


def _generate_lgsm_config(config: dict[str, str]) -> str:
    """Generate LinuxGSM common.cfg content."""
    return "\n".join(f'{k}="{v}"' for k, v in config.items()) + "\n"


def _parse_lgsm_config(path: str) -> dict[str, str]:
    """Parse a LinuxGSM common.cfg file into a dict."""
    import re
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(\w+)="(.*)"', line)
            if m:
                config[m.group(1)] = m.group(2)
    return config


def _parse_env_file(path: str) -> dict[str, str]:
    """Parse a config file (KEY=VALUE or KEY="VALUE" per line) into a dict."""
    config = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip()
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            config[key.strip()] = value
    return config


def _is_client_error(exc: ClientError, code: str) -> bool:
    return exc.response["Error"]["Code"] == code


def _parse_ports_tag(tag: str) -> dict[str, int]:
    """Parse compact ports tag like '27015/udp,34197/udp' into {port_spec: port_num}."""
    if not tag:
        return {}
    result = {}
    for entry in tag.split(","):
        entry = entry.strip()
        if "/" not in entry:
            continue
        port_str = entry.split("/")[0]
        try:
            result[entry] = int(port_str)
        except ValueError:
            continue
    return result


def get_default_vpc_and_subnet(region: str) -> tuple[str, str]:
    """Find the default VPC and a subnet in it."""
    ec2 = boto3.client("ec2", region_name=region)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])
    if not vpcs["Vpcs"]:
        raise RuntimeError(f"No default VPC found in region {region}")
    vpc_id = vpcs["Vpcs"][0]["VpcId"]

    subnets = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )
    if not subnets["Subnets"]:
        raise RuntimeError(f"No subnets found in default VPC {vpc_id}")
    subnet_id = subnets["Subnets"][0]["SubnetId"]
    return vpc_id, subnet_id


class Provisioner:
    EC2_STATE_MAP = {
        "pending": "launching",
        "running": "running",
        "stopping": "paused",
        "stopped": "paused",
    }

    SSM_ACTIVE_REGIONS_PARAM = "/gsmc/active-regions"

    def __init__(self, state_dir=None, on_status=None, debug=False, on_debug=None):
        kwargs = {}
        if state_dir is not None:
            kwargs["state_dir"] = state_dir
        self.state = ServerState(**kwargs)
        self.snapshot_state = SnapshotState(**kwargs)
        self.on_status = on_status
        self.debug = debug
        self.on_debug = on_debug

    def _notify(self, message: str) -> None:
        if self.on_status:
            self.on_status(message)

    def _debug_callback(self, message: str) -> None:
        if self.debug and self.on_debug:
            self.on_debug(message)

    def _get_active_regions(self) -> set[str]:
        """Read active regions from SSM Parameter Store."""
        from gsm.control.ssh import SSM_REGION
        ssm = boto3.client("ssm", region_name=SSM_REGION)
        try:
            response = ssm.get_parameter(Name=self.SSM_ACTIVE_REGIONS_PARAM)
            value = response["Parameter"]["Value"]
            return {r.strip() for r in value.split(",") if r.strip()}
        except ClientError as e:
            if _is_client_error(e, "ParameterNotFound"):
                return set()
            raise

    def _add_active_region(self, region: str) -> None:
        """Add a region to the SSM active-regions set (idempotent)."""
        current = self._get_active_regions()
        if region in current:
            return
        current.add(region)
        from gsm.control.ssh import SSM_REGION
        ssm = boto3.client("ssm", region_name=SSM_REGION)
        ssm.put_parameter(
            Name=self.SSM_ACTIVE_REGIONS_PARAM,
            Value=",".join(sorted(current)),
            Type="String",
            Overwrite=True,
        )

    def _remove_active_region(self, region: str) -> None:
        """Remove a region if no local servers remain in it."""
        remaining = [r for r in self.state.list_all() if r.region == region]
        if remaining:
            return
        current = self._get_active_regions()
        if region not in current:
            return
        current.discard(region)
        from gsm.control.ssh import SSM_REGION
        ssm = boto3.client("ssm", region_name=SSM_REGION)
        if current:
            ssm.put_parameter(
                Name=self.SSM_ACTIVE_REGIONS_PARAM,
                Value=",".join(sorted(current)),
                Type="String",
                Overwrite=True,
            )
        else:
            try:
                ssm.delete_parameter(Name=self.SSM_ACTIVE_REGIONS_PARAM)
            except ClientError as e:
                if not _is_client_error(e, "ParameterNotFound"):
                    raise

    def auto_reconcile(self) -> None:
        """Run reconcile if the TTL file is stale or missing. Best-effort."""
        try:
            import time
            ttl_file = self.state.state_dir / ".last_reconcile"
            if ttl_file.exists():
                last = float(ttl_file.read_text().strip())
                if time.time() - last < 30:
                    return
            self.reconcile()
        except Exception:
            pass

    def _write_metadata_file(self, ssh, record: ServerRecord) -> None:
        """Write server metadata to /opt/gsm/metadata.json on the EC2 host."""
        import json as _json
        metadata = _json.dumps({
            "config": record.config,
            "rcon_password": record.rcon_password,
        })
        ssh.run(f"mkdir -p /opt/gsm && cat > /opt/gsm/metadata.json << 'GSMEOF'\n{metadata}\nGSMEOF")

    def _read_metadata_file(self, ssh) -> dict:
        """Read server metadata from /opt/gsm/metadata.json on the EC2 host."""
        import json as _json
        try:
            result = ssh.run("cat /opt/gsm/metadata.json")
            return _json.loads(result)
        except Exception:
            return {}

    def _refresh_record(self, server_id: str) -> ServerRecord | None:
        """Refresh a single server's state from EC2. Returns updated record or None if gone."""
        record = self.state.get(server_id)
        if not record:
            return None
        try:
            ec2 = boto3.client("ec2", region_name=record.region)
            response = ec2.describe_instances(InstanceIds=[record.instance_id])
            reservations = response.get("Reservations", [])
            if not reservations or not reservations[0].get("Instances"):
                self.state.delete(server_id)
                return None
            instance = reservations[0]["Instances"][0]
            state = instance["State"]["Name"]
            if state in ("terminated", "shutting-down"):
                self.state.delete(server_id)
                return None
            tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
            new_status = self.EC2_STATE_MAP.get(state, record.status)
            # Respect container-stopped tag and local state
            if new_status == "running":
                if record.status == "stopped" or tags.get("gsm:container-stopped") == "true":
                    new_status = "stopped"
            new_ip = instance.get("PublicIpAddress") or ""
            if new_status != record.status:
                self.state.update_status(server_id, new_status)
            if new_ip != record.public_ip:
                self.state.update_field(server_id, "public_ip", new_ip)
            # Sync tag-backed fields (cross-machine changes)
            tag_eip = tags.get("gsm:eip-alloc-id", "")
            if tag_eip != record.eip_allocation_id:
                self.state.update_field(server_id, "eip_allocation_id", tag_eip)
                if tag_eip:
                    try:
                        for addr in find_gsm_eips(record.region):
                            if addr["AllocationId"] == tag_eip:
                                self.state.update_field(server_id, "eip_public_ip", addr.get("PublicIp", ""))
                                break
                    except Exception:
                        pass
                else:
                    self.state.update_field(server_id, "eip_public_ip", "")
            tag_cn = tags.get("gsm:container-name", "")
            if tag_cn and tag_cn != record.container_name:
                self.state.update_field(server_id, "container_name", tag_cn)
            tag_sg = tags.get("gsm:sg-id", "")
            if tag_sg and tag_sg != record.security_group_id:
                self.state.update_field(server_id, "security_group_id", tag_sg)
            tag_rcon = tags.get("gsm:rcon-password", "")
            if tag_rcon and tag_rcon != record.rcon_password:
                self.state.update_field(server_id, "rcon_password", tag_rcon)
            tag_ports = tags.get("gsm:ports", "")
            if tag_ports:
                parsed = _parse_ports_tag(tag_ports)
                if parsed != record.ports:
                    self.state.update_field(server_id, "ports", parsed)
            return self.state.get(server_id)
        except ClientError as e:
            if _is_client_error(e, "InvalidInstanceID.NotFound"):
                self.state.delete(server_id)
                return None
            raise

    def launch(
        self,
        game: GameDefinition,
        region: str = "us-east-1",
        instance_type: str | None = None,
        name: str | None = None,
        env_overrides: dict[str, str] | None = None,
        uploads: list[tuple[str, str]] | None = None,
        from_snapshot: str | None = None,
        lgsm_config_overrides: dict[str, str] | None = None,
        lgsm_config_file: str | None = None,
        pin_ip: bool = False,
    ) -> ServerRecord:
        """Full launch flow: AMI, SG, EC2, SSH, Docker."""
        try:
            self.reconcile()
        except Exception:
            pass

        server_id = uuid.uuid4().hex[:12]
        instance_type = instance_type or game.default_instance_type
        name = name or f"{game.name}-{server_id[:6]}"

        if self.state.name_exists(name):
            raise ValueError(f"A server named '{name}' already exists")

        # Safety net: check EC2 tags across active regions for duplicate names
        try:
            for check_region in self._get_active_regions() | {region}:
                for inst in find_gsm_instances(check_region):
                    if inst.get("gsm_name") == name:
                        raise ValueError(f"A server named '{name}' already exists (found in {check_region})")
        except ValueError:
            raise
        except Exception:
            pass  # Best-effort; don't block launch on network errors

        # Snapshot restores reuse the existing container as-is
        if from_snapshot and (env_overrides or uploads or lgsm_config_overrides or lgsm_config_file):
            raise ValueError(
                "Cannot use -c/--config, -u/--upload, or --config-file with --from-snapshot. "
                "Snapshot restores reuse the original container and config."
            )

        # Build environment
        if game.lgsm_server_code:
            env = {}
        else:
            env = dict(game.defaults)
        if not game.lgsm_server_code and lgsm_config_file:
            # --config-file for Docker games: parse .env file into env overrides
            env.update(_parse_env_file(lgsm_config_file))
        if env_overrides:
            env.update(env_overrides)

        # Generate random RCON password if game uses one and none was provided
        import secrets
        rcon_password = None
        if game.rcon_password_key and not game.lgsm_server_code:
            if game.rcon_password_key not in env:
                env[game.rcon_password_key] = secrets.token_urlsafe(16)
            rcon_password = env[game.rcon_password_key]

        # Auto-generate non-RCON password env vars
        for key in game.password_keys:
            if key not in (env_overrides or {}):
                env[key] = secrets.token_urlsafe(16)

        # Validate required config keys before any AWS calls
        # Skip when restoring from snapshot — config is already on disk.
        if game.required_config and not from_snapshot:
            if game.lgsm_server_code:
                if lgsm_config_file:
                    provided = _parse_lgsm_config(lgsm_config_file)
                else:
                    provided = dict(game.defaults)
                    if lgsm_config_overrides:
                        provided.update(lgsm_config_overrides)
            else:
                provided = dict(game.defaults)
                if lgsm_config_file:
                    provided.update(_parse_env_file(lgsm_config_file))
                if env_overrides:
                    provided.update(env_overrides)
            missing = [k for k in game.required_config if k not in provided]
            if missing:
                config_flags = " ".join(f"--config {k}=VALUE" for k in missing)
                raise ValueError(
                    f"Missing required config key(s): {', '.join(missing)}.\n"
                    f"Provide inline:  gsm launch {game.name} {config_flags}\n"
                    f"Or generate a config file, edit it, and launch with:\n"
                    f"  gsm config {game.name} --init\n"
                    f"  gsm launch {game.name} --config-file {game.name}.cfg"
                )

        # Get default VPC and subnet
        self._notify("Finding default VPC")
        vpc_id, subnet_id = get_default_vpc_and_subnet(region)

        # Ensure SSH key pair
        self._notify("Ensuring SSH key pair")
        key_path = ensure_key_pair(region)

        # Get AMI (from snapshot or latest AL2023)
        if from_snapshot:
            self._notify("Restoring from snapshot")
            snap_record = self.snapshot_state.get(from_snapshot)
            if not snap_record:
                raise ValueError(f"Snapshot {from_snapshot} not found")
            ami_name = f"gsm-restore-{server_id}"
            ami_id = register_ami_from_snapshot(
                region, snap_record.snapshot_id, ami_name,
                description=f"GSM restore from snapshot {from_snapshot}",
            )
        else:
            self._notify("Getting AMI")
            ami_id = get_latest_al2023_ami(region)

        restore_ami_id = ami_id if from_snapshot else None

        # Create/get security group
        self._notify("Creating security group")
        sg_id = get_or_create_security_group(
            region=region, game_name=game.name, ports=game.ports,
            vpc_id=vpc_id,
        )

        # Compute container_name and ports early so they're available for initial save
        container_name = f"gsm-{game.name}-{server_id[:8]}"
        ports = {f"{p.port}/{p.protocol}": p.port for p in game.ports}
        ports_tag = ",".join(sorted(ports.keys()))

        # Generate launch_time before EC2 call so it can be tagged
        from datetime import datetime, timezone
        launch_time = datetime.now(timezone.utc).isoformat()

        # Launch instance
        self._notify("Launching instance")
        instance_id = launch_instance(
            region=region, ami_id=ami_id, instance_type=instance_type,
            key_name=KEY_NAME, security_group_id=sg_id, subnet_id=subnet_id,
            game_name=game.name, server_id=server_id, server_name=name,
            disk_gb=game.disk_gb,
            ports_tag=ports_tag,
            rcon_password=rcon_password or "",
            container_name="" if from_snapshot else container_name,
            launch_time=launch_time,
        )

        # Track region immediately so other machines can discover this instance
        try:
            self._add_active_region(region)
        except Exception:
            pass

        # Save initial record so the instance is always tracked
        initial_record = ServerRecord(
            id=server_id, game=game.name, name=name,
            instance_id=instance_id, region=region,
            public_ip="", ports=ports,
            status="launching", security_group_id=sg_id,
            container_name=container_name,
            rcon_password="",
            config={},
            launch_time=launch_time,
        )
        self.state.save(initial_record)

        # Everything after this point must clean up the instance on failure
        ssh = None
        try:
            # Wait for instance and get IP
            self._notify("Waiting for instance to start")
            wait_for_instance_running(region, instance_id)
            self._notify("Getting instance IP")
            public_ip = get_instance_public_ip(region, instance_id)

            # SSH connect
            self._notify("Connecting via SSH")
            ssh = SSHClient(host=public_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
            ssh.connect()

            # Docker setup
            docker = RemoteDocker(ssh)
            self._notify("Waiting for Docker")
            docker.wait_for_docker()
            final_lgsm_config = {}

            if from_snapshot:
                # Restore metadata from snapshot record, fall back to disk
                if snap_record.config or snap_record.rcon_password:
                    if game.lgsm_server_code:
                        final_lgsm_config = dict(snap_record.config)
                    else:
                        env = dict(snap_record.config)
                    rcon_password = snap_record.rcon_password
                else:
                    disk_meta = self._read_metadata_file(ssh)
                    config_data = disk_meta.get("config", {})
                    if game.lgsm_server_code:
                        final_lgsm_config = config_data
                    else:
                        env = config_data
                    rcon_password = disk_meta.get("rcon_password", "")

                # Snapshot restore: reuse the existing container from the snapshot
                self._notify("Finding container from snapshot")
                old_name = docker.find_gsm_container()
                if not old_name:
                    raise RuntimeError(
                        "No gsm container found on the snapshot volume. "
                        "The snapshot may not have been created by gsm."
                    )
                container_name = old_name
                # Update container-name tag since snapshot may have different name
                try:
                    set_instance_tag(region, instance_id, "gsm:container-name", container_name)
                except Exception:
                    pass
                self._notify("Starting restored container")
                docker.start(container_name)
            else:
                self._notify(f"Pulling image {game.image}")
                docker.pull(game.image)

                extra_args = list(game.extra_docker_args) if game.extra_docker_args else []
                if game.lgsm_server_code:
                    extra_args.append("--restart unless-stopped")

                # Build LinuxGSM config injection if applicable
                lgsm_config_path = None
                final_lgsm_config = {}
                if game.lgsm_server_code:
                    if lgsm_config_file:
                        lgsm_config_path = lgsm_config_file
                        final_lgsm_config = _parse_lgsm_config(lgsm_config_file)
                    else:
                        final_lgsm_config = dict(game.defaults)
                        if lgsm_config_overrides:
                            final_lgsm_config.update(lgsm_config_overrides)

                    # Auto-generate LinuxGSM RCON password
                    if game.rcon_password_key:
                        if lgsm_config_file:
                            # For --config-file: use password from file, or auto-gen if missing
                            if game.rcon_password_key not in final_lgsm_config:
                                final_lgsm_config[game.rcon_password_key] = secrets.token_urlsafe(16)
                        elif game.rcon_password_key not in (lgsm_config_overrides or {}):
                            final_lgsm_config[game.rcon_password_key] = secrets.token_urlsafe(16)
                        rcon_password = final_lgsm_config[game.rcon_password_key]

                    if final_lgsm_config:
                        import tempfile
                        tmp = tempfile.NamedTemporaryFile(
                            mode="w", suffix=".cfg", delete=False,
                        )
                        tmp.write(_generate_lgsm_config(final_lgsm_config))
                        tmp.close()
                        lgsm_config_path = tmp.name

                needs_cp = bool(uploads) or bool(lgsm_config_path)

                if needs_cp:
                    # Create container, copy files, then start
                    self._notify("Creating container")
                    docker.create(
                        container_name=container_name, image=game.image,
                        ports=game.ports, env=env, volumes=game.volumes,
                        extra_args=extra_args or None,
                    )
                    if uploads:
                        self._notify("Uploading files")
                        for local_path, container_path in uploads:
                            remote_tmp = f"/tmp/{uuid.uuid4().hex[:8]}"
                            ssh.upload_file(local_path, remote_tmp)
                            docker.cp_to(container_name, remote_tmp, container_path)
                    if lgsm_config_path:
                        self._notify("Uploading LinuxGSM config")
                        remote_tmp = f"/tmp/{uuid.uuid4().hex[:8]}"
                        ssh.upload_file(lgsm_config_path, remote_tmp)
                        config_base = game.data_paths.get("config", "/data/config-lgsm")
                        config_dest = f"{config_base}/{game.lgsm_server_code}/common.cfg"
                        docker.cp_to(container_name, remote_tmp, config_dest)
                    self._notify("Starting container")
                    docker.start(container_name)
                else:
                    self._notify("Starting container")
                    docker.run(
                        container_name=container_name, image=game.image,
                        ports=game.ports, env=env, volumes=game.volumes,
                        extra_args=extra_args or None,
                    )
        except BaseException as original_error:
            # Clean up the instance so we don't leave orphans
            self._notify("Cleaning up after failure")
            if ssh:
                ssh.close()
                ssh = None
            if restore_ami_id:
                try:
                    deregister_ami(region, restore_ami_id)
                except Exception:
                    pass
            try:
                terminate_instance(region, instance_id)
            except Exception:
                # Terminate failed — keep state entry so user can discover it
                raise original_error from original_error
            # Terminate succeeded — remove the state entry
            self.state.delete(server_id)
            raise

        # Save final state with full metadata
        record = ServerRecord(
            id=server_id, game=game.name, name=name,
            instance_id=instance_id, region=region,
            public_ip=public_ip, ports=ports,
            status="running", security_group_id=sg_id,
            container_name=container_name,
            rcon_password=rcon_password or "",
            config=final_lgsm_config if game.lgsm_server_code else env,
            launch_time=initial_record.launch_time,
        )
        self.state.save(record)

        # Update rcon_password tag if determined late (LinuxGSM games)
        if game.lgsm_server_code and rcon_password:
            try:
                set_instance_tag(region, instance_id, "gsm:rcon-password", rcon_password)
            except Exception:
                pass

        # Write metadata file to disk for snapshot recovery (needs open SSH)
        try:
            self._write_metadata_file(ssh, record)
        except Exception:
            pass
        if ssh:
            ssh.close()

        # Pin a static Elastic IP if requested
        if pin_ip:
            try:
                alloc_id, eip_ip = allocate_eip(region, server_id)
                try:
                    associate_eip(region, alloc_id, instance_id)
                except Exception:
                    release_eip(region, alloc_id)
                    raise
                record.eip_allocation_id = alloc_id
                record.eip_public_ip = eip_ip
                record.public_ip = eip_ip
                self.state.save(record)
                try:
                    set_instance_tag(region, instance_id, "gsm:eip-alloc-id", alloc_id)
                except Exception:
                    pass
            except Exception:
                raise

        if restore_ami_id:
            try:
                deregister_ami(region, restore_ami_id)
            except Exception:
                pass

        return record

    def destroy(self, server_id: str) -> None:
        """Terminate instance and delete state."""
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        refreshed = self._refresh_record(server_id)
        if not refreshed:
            # Instance already gone, state already cleaned up by _refresh_record
            return
        # Release EIP before terminating
        if refreshed.eip_allocation_id:
            try:
                disassociate_eip(refreshed.region, refreshed.eip_allocation_id)
            except Exception:
                pass  # AWS auto-disassociates on termination
            try:
                release_eip(refreshed.region, refreshed.eip_allocation_id)
            except Exception:
                pass  # EIP may leak; use 'gsmc eips --cleanup' to find it

        self._notify("Terminating instance")
        try:
            terminate_instance(refreshed.region, refreshed.instance_id)
        except ClientError as e:
            if not _is_client_error(e, "InvalidInstanceID.NotFound"):
                raise
        destroyed_region = refreshed.region
        self.state.delete(server_id)

        try:
            self._remove_active_region(destroyed_region)
        except Exception:
            pass

    def destroy_all(self) -> None:
        """Terminate all servers (including cross-machine)."""
        try:
            self.reconcile()
        except Exception:
            pass
        errors = []
        for record in self.state.list_all():
            try:
                self.destroy(record.id)
            except Exception as e:
                errors.append(f"{record.name}: {e}")
        if errors:
            raise RuntimeError(f"Failed to destroy {len(errors)} server(s): {'; '.join(errors)}")

    def get_ssh_client(self, server_id: str) -> SSHClient:
        """Get an SSH client connected to a running server."""
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        key_path = ensure_key_pair(record.region)
        ssh = SSHClient(host=record.public_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
        ssh.connect()
        return ssh

    def _resolve_container(self, server_id: str, docker: RemoteDocker) -> str:
        """Verify the container name exists on the host, or discover the actual one.

        If the expected container is missing, finds the real GSM container and
        updates both local state and the EC2 tag for cross-machine sync.
        """
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if docker.container_exists(record.container_name):
            return record.container_name
        # Container not found — try to discover the actual one
        actual = docker.find_gsm_container()
        if actual:
            self.state.update_field(server_id, "container_name", actual)
            try:
                set_instance_tag(record.region, record.instance_id, "gsm:container-name", actual)
            except Exception:
                pass
            return actual
        raise RuntimeError(
            f"Container {record.container_name} not found on the server "
            f"and no GSM container discovered"
        )

    def pause(self, server_id: str) -> None:
        """Stop Docker container then stop EC2 instance."""
        record = self._refresh_record(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if record.status == "paused":
            raise ValueError(f"Server {server_id} is already paused")

        # Try to gracefully stop the container via SSH
        self._notify("Stopping container")
        ssh = None
        try:
            key_path = ensure_key_pair(record.region)
            ssh = SSHClient(host=record.public_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
            ssh.connect()
            docker = RemoteDocker(ssh)
            docker.stop(record.container_name)
        except (Exception, KeyboardInterrupt):
            pass  # Proceed to stop instance even if SSH/Docker fails or is interrupted
        finally:
            if ssh:
                ssh.close()

        self._notify("Stopping instance")
        stop_issued = False
        try:
            stop_instance(record.region, record.instance_id)
            stop_issued = True
        except ClientError as e:
            if _is_client_error(e, "InvalidInstanceID.NotFound"):
                self.state.delete(server_id)
                raise RuntimeError(f"Server {server_id} was terminated externally") from e
            if _is_client_error(e, "IncorrectInstanceState"):
                self.state.update_status(server_id, "paused")
                return
            raise
        finally:
            if stop_issued:
                self.state.update_status(server_id, "paused")

        self._notify("Waiting for instance to stop")
        try:
            wait_for_instance_stopped(record.region, record.instance_id)
        except Exception:
            pass

    def resume(self, server_id: str) -> ServerRecord:
        """Start EC2 instance, update IP/SG, restart Docker container."""
        record = self._refresh_record(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if record.status not in ("paused", "stopped"):
            raise ValueError(f"Server {server_id} is not paused or stopped (status: {record.status})")

        if record.status == "stopped":
            return self._resume_container(server_id, record)

        # Full resume from paused: start EC2 instance
        self._notify("Starting instance")
        try:
            start_instance(record.region, record.instance_id)
        except ClientError as e:
            if _is_client_error(e, "InvalidInstanceID.NotFound"):
                self.state.delete(server_id)
                raise RuntimeError(f"Server {server_id} was terminated externally") from e
            raise

        self._notify("Waiting for instance to start")
        wait_for_instance_running(record.region, record.instance_id)

        if record.eip_allocation_id:
            self._notify("Associating Elastic IP")
            associate_eip(record.region, record.eip_allocation_id, record.instance_id)
            new_ip = record.eip_public_ip
        else:
            self._notify("Getting new IP")
            new_ip = get_instance_public_ip(record.region, record.instance_id)
        self.state.update_field(server_id, "public_ip", new_ip)
        # Instance IS running — update state now, before Docker
        self.state.update_status(server_id, "running")

        self._notify("Connecting via SSH")
        ssh = None
        try:
            key_path = ensure_key_pair(record.region)
            ssh = SSHClient(host=new_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
            ssh.connect()
            docker = RemoteDocker(ssh)
            container_name = self._resolve_container(server_id, docker)
            self._notify("Starting container")
            docker.start(container_name)
            try:
                delete_instance_tag(record.region, record.instance_id, "gsm:container-stopped")
            except Exception:
                pass
        except BaseException as e:
            raise RuntimeError(
                f"Instance is running (IP: {new_ip}) but container failed to start: {e}. "
                f"Try 'gsm resume {server_id}' again or 'gsm ssh {server_id}' to debug."
            ) from e
        finally:
            if ssh:
                ssh.close()

        return self.state.get(server_id)

    def _resume_container(self, server_id: str, record: ServerRecord) -> ServerRecord:
        """Resume a stopped container on an already-running instance."""
        self._notify("Connecting via SSH")
        key_path = ensure_key_pair(record.region)
        ssh = SSHClient(host=record.public_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
        ssh.connect()
        try:
            docker = RemoteDocker(ssh)
            container_name = self._resolve_container(server_id, docker)
            self._notify("Starting container")
            docker.start(container_name)
            self.state.update_status(server_id, "running")
            try:
                delete_instance_tag(record.region, record.instance_id, "gsm:container-stopped")
            except Exception:
                pass
            return self.state.get(server_id)
        finally:
            ssh.close()

    def stop_container(self, server_id: str) -> None:
        """Stop the Docker container but keep the EC2 instance running."""
        record = self._refresh_record(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if record.status != "running":
            raise ValueError(f"Server {server_id} is not running (status: {record.status})")
        self._notify("Connecting via SSH")
        key_path = ensure_key_pair(record.region)
        ssh = SSHClient(host=record.public_ip, key_path=str(key_path), on_debug=self._debug_callback if self.debug else None)
        ssh.connect()
        try:
            docker = RemoteDocker(ssh)
            container_name = self._resolve_container(server_id, docker)
            self._notify("Stopping container")
            docker.stop(container_name)
            self.state.update_status(server_id, "stopped")
            try:
                set_instance_tag(record.region, record.instance_id, "gsm:container-stopped", "true")
            except Exception:
                pass
        finally:
            ssh.close()

    def pin_ip(self, server_id: str) -> ServerRecord:
        """Allocate and associate an Elastic IP to a server."""
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if record.eip_allocation_id:
            raise ValueError(f"Server {record.name} already has a pinned IP ({record.eip_public_ip})")

        self._notify("Allocating Elastic IP")
        alloc_id, eip_ip = allocate_eip(record.region, server_id)

        # Associate if the EC2 instance is running
        if record.status in ("running", "stopped"):
            try:
                self._notify("Associating Elastic IP")
                associate_eip(record.region, alloc_id, record.instance_id)
            except BaseException:
                release_eip(record.region, alloc_id)
                raise
            self.state.update_field(server_id, "public_ip", eip_ip)

        self.state.update_field(server_id, "eip_allocation_id", alloc_id)
        self.state.update_field(server_id, "eip_public_ip", eip_ip)
        try:
            set_instance_tag(record.region, record.instance_id, "gsm:eip-alloc-id", alloc_id)
        except Exception:
            pass
        return self.state.get(server_id)

    def unpin_ip(self, server_id: str) -> ServerRecord:
        """Remove the Elastic IP from a server."""
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")
        if not record.eip_allocation_id:
            raise ValueError(f"Server {record.name} does not have a pinned IP")

        self._notify("Releasing Elastic IP")
        disassociate_eip(record.region, record.eip_allocation_id)
        release_eip(record.region, record.eip_allocation_id)

        self.state.update_field(server_id, "eip_allocation_id", "")
        self.state.update_field(server_id, "eip_public_ip", "")
        try:
            delete_instance_tag(record.region, record.instance_id, "gsm:eip-alloc-id")
        except Exception:
            pass

        # Get the new ephemeral IP if server is running
        if record.status in ("running", "stopped"):
            new_ip = get_instance_public_ip(record.region, record.instance_id)
            self.state.update_field(server_id, "public_ip", new_ip)

        return self.state.get(server_id)

    def list_eips(self) -> list[dict]:
        """List all GSM-managed EIPs across regions."""
        regions = set()
        local_records = self.state.list_all()
        for r in local_records:
            regions.add(r.region)
        try:
            regions.update(self._get_active_regions())
        except Exception:
            pass
        if not regions:
            regions.add("us-east-1")

        # Build lookup from server_id to name
        name_by_id = {r.id: r.name for r in local_records}

        results = []
        for region in regions:
            for addr in find_gsm_eips(region):
                tags = {t["Key"]: t["Value"] for t in addr.get("Tags", [])}
                gsm_id = tags.get("gsm:id", "")
                results.append({
                    "allocation_id": addr["AllocationId"],
                    "public_ip": addr.get("PublicIp", ""),
                    "region": region,
                    "server_id": gsm_id,
                    "server_name": name_by_id.get(gsm_id, ""),
                    "associated": bool(addr.get("AssociationId")),
                })
        return results

    def cleanup_eip(self, allocation_id: str, region: str) -> None:
        """Release a single EIP by allocation ID."""
        release_eip(region, allocation_id)

    def snapshot(self, server_id: str) -> SnapshotRecord:
        """Create an EBS snapshot of the server's root volume."""
        record = self.state.get(server_id)
        if not record:
            raise ValueError(f"Server {server_id} not found")

        self._notify("Getting root volume")
        volume_id = get_instance_root_volume_id(record.region, record.instance_id)
        snap_id = uuid.uuid4().hex[:12]
        tags = {
            "gsm:id": record.id,
            "gsm:game": record.game,
            "gsm:name": record.name,
            "gsm:snapshot-id": snap_id,
        }
        self._notify("Creating snapshot")
        aws_snapshot_id = create_snapshot(
            record.region, volume_id,
            description=f"GSM snapshot of {record.name} ({record.game})",
            tags=tags,
        )
        self._notify("Waiting for snapshot to complete")
        wait_for_snapshot_complete(record.region, aws_snapshot_id)

        snap_record = SnapshotRecord(
            id=snap_id, snapshot_id=aws_snapshot_id, game=record.game,
            server_name=record.name, server_id=record.id,
            region=record.region, status="completed",
            config=record.config,
            rcon_password=record.rcon_password,
        )
        self.snapshot_state.save(snap_record)
        return snap_record

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete a snapshot from AWS and local state."""
        snap_record = self.snapshot_state.get(snapshot_id)
        if not snap_record:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        # Deregister any AMIs backed by this snapshot (leftover restore AMIs)
        for ami_id in find_amis_using_snapshot(snap_record.region, snap_record.snapshot_id):
            self._notify(f"Deregistering AMI {ami_id}")
            deregister_ami(snap_record.region, ami_id)

        aws_delete_snapshot(snap_record.region, snap_record.snapshot_id)
        self.snapshot_state.delete(snapshot_id)

    def list_snapshots(self) -> list[SnapshotRecord]:
        """List all snapshot records."""
        return self.snapshot_state.list_all()

    def reconcile(self, extra_regions: set[str] | None = None) -> None:
        """Sync local state with EC2 reality."""
        # Collect regions from local records + extra_regions + SSM active-regions
        regions = set()
        local_records = self.state.list_all()
        for r in local_records:
            regions.add(r.region)
        if extra_regions:
            regions.update(extra_regions)
        try:
            regions.update(self._get_active_regions())
        except Exception:
            pass
        if not regions:
            regions.add("us-east-1")

        # Query EC2 for all GSM instances across regions
        ec2_by_gsm_id: dict[str, dict] = {}
        for region in regions:
            for inst in find_gsm_instances(region):
                inst["region"] = region
                ec2_by_gsm_id[inst["gsm_id"]] = inst

        # Build EIP lookup for resolving eip_alloc_id -> public IP
        eip_by_alloc: dict[str, str] = {}
        for region in regions:
            for addr in find_gsm_eips(region):
                eip_by_alloc[addr["AllocationId"]] = addr.get("PublicIp", "")

        # Update or remove local records
        local_ids = {r.id for r in local_records}
        for record in local_records:
            if record.id in ec2_by_gsm_id:
                inst = ec2_by_gsm_id[record.id]
                new_status = self.EC2_STATE_MAP.get(inst["state"], record.status)
                # Preserve "stopped" if local state already knows, OR if EC2 tag says so
                if new_status == "running":
                    if record.status == "stopped" or inst.get("gsm_container_stopped") == "true":
                        new_status = "stopped"
                if new_status != record.status:
                    self.state.update_status(record.id, new_status)
                new_ip = inst.get("public_ip") or ""
                if new_ip != record.public_ip:
                    self.state.update_field(record.id, "public_ip", new_ip)
                # Sync tag-backed fields from EC2 (covers cross-machine changes)
                tag_sg = inst.get("gsm_sg_id", "")
                if tag_sg and tag_sg != record.security_group_id:
                    self.state.update_field(record.id, "security_group_id", tag_sg)
                tag_ports = _parse_ports_tag(inst.get("gsm_ports", ""))
                if tag_ports and tag_ports != record.ports:
                    self.state.update_field(record.id, "ports", tag_ports)
                tag_rcon = inst.get("gsm_rcon_password", "")
                if tag_rcon and tag_rcon != record.rcon_password:
                    self.state.update_field(record.id, "rcon_password", tag_rcon)
                tag_eip = inst.get("gsm_eip_alloc_id", "")
                if tag_eip != record.eip_allocation_id:
                    self.state.update_field(record.id, "eip_allocation_id", tag_eip)
                    eip_ip = eip_by_alloc.get(tag_eip, "") if tag_eip else ""
                    self.state.update_field(record.id, "eip_public_ip", eip_ip)
                tag_cn = inst.get("gsm_container_name", "")
                if tag_cn and tag_cn != record.container_name:
                    self.state.update_field(record.id, "container_name", tag_cn)
                tag_lt = inst.get("gsm_launch_time", "")
                if tag_lt and tag_lt != record.launch_time:
                    self.state.update_field(record.id, "launch_time", tag_lt)
            else:
                # Instance no longer exists in EC2
                self.state.delete(record.id)

        for gsm_id, inst in ec2_by_gsm_id.items():
            if gsm_id not in local_ids:
                status = self.EC2_STATE_MAP.get(inst["state"], "running")
                # Respect container-stopped tag for orphans too
                if status == "running" and inst.get("gsm_container_stopped") == "true":
                    status = "stopped"
                eip_alloc = inst.get("gsm_eip_alloc_id", "")
                eip_ip = eip_by_alloc.get(eip_alloc, "") if eip_alloc else ""
                # Use tagged container_name if available, otherwise ServerRecord
                # __post_init__ will generate the default
                cn_kwargs = {}
                tag_cn = inst.get("gsm_container_name", "")
                if tag_cn:
                    cn_kwargs["container_name"] = tag_cn
                tag_lt = inst.get("gsm_launch_time", "")
                if tag_lt:
                    cn_kwargs["launch_time"] = tag_lt
                orphan = ServerRecord(
                    id=gsm_id,
                    game=inst.get("gsm_game", ""),
                    name=inst.get("gsm_name", ""),
                    instance_id=inst["instance_id"],
                    region=inst["region"],
                    public_ip=inst.get("public_ip") or "",
                    ports=_parse_ports_tag(inst.get("gsm_ports", "")),
                    status=status,
                    security_group_id=inst.get("gsm_sg_id", ""),
                    rcon_password=inst.get("gsm_rcon_password", ""),
                    eip_allocation_id=eip_alloc,
                    eip_public_ip=eip_ip,
                    **cn_kwargs,
                )
                self.state.save(orphan)

        # Snapshot reconciliation
        snap_regions = regions.copy()
        for s in self.snapshot_state.list_all():
            snap_regions.add(s.region)

        aws_snaps: dict[str, dict] = {}
        for region in snap_regions:
            for snap in aws_list_snapshots(region):
                snap["_region"] = region
                aws_snaps[snap["SnapshotId"]] = snap

        # Remove local records for deleted AWS snapshots
        local_snaps = self.snapshot_state.list_all()
        for snap_record in local_snaps:
            if snap_record.snapshot_id not in aws_snaps:
                self.snapshot_state.delete(snap_record.id)

        # Adopt orphaned AWS snapshots
        local_aws_ids = {s.snapshot_id for s in local_snaps}
        for aws_id, snap_data in aws_snaps.items():
            if aws_id not in local_aws_ids:
                tags = {t["Key"]: t["Value"] for t in snap_data.get("Tags", [])}
                orphan = SnapshotRecord(
                    id=tags.get("gsm:snapshot-id", uuid.uuid4().hex[:12]),
                    snapshot_id=aws_id,
                    game=tags.get("gsm:game", ""),
                    server_name=tags.get("gsm:name", ""),
                    server_id=tags.get("gsm:id", ""),
                    region=snap_data["_region"],
                    status="completed",
                )
                self.snapshot_state.save(orphan)

        # EIP reconciliation: clear stale EIP references
        # Reuse eip_by_alloc built earlier to avoid redundant API calls
        aws_eip_alloc_ids = set(eip_by_alloc.keys())

        for record in self.state.list_all():
            if record.eip_allocation_id and record.eip_allocation_id not in aws_eip_alloc_ids:
                self.state.update_field(record.id, "eip_allocation_id", "")
                self.state.update_field(record.id, "eip_public_ip", "")
                # Update public_ip from EC2 data if available
                if record.id in ec2_by_gsm_id:
                    new_ip = ec2_by_gsm_id[record.id].get("public_ip") or ""
                    self.state.update_field(record.id, "public_ip", new_ip)

        # Write TTL file so auto_reconcile can skip redundant runs
        try:
            import time
            ttl_file = self.state.state_dir / ".last_reconcile"
            ttl_file.write_text(str(time.time()))
        except Exception:
            pass
