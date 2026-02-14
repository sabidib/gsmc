import os
import sys

import click
import halo
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from click.shell_completion import CompletionItem

from gsm.control.provisioner import Provisioner
from gsm.control.docker import RemoteDocker
from gsm.games.registry import get_game, list_games

console = Console()

PROGRESS_MODE = "steps"  # "steps", "inline", or "plain"


class StepProgress:
    """Step-by-step progress display.

    Modes:
        "steps"   — halo bouncingBar spinner, checkmark/cross per step on new lines
        "inline"  — single-line replacement (old console.status behavior)
        "plain"   — just print each message, no spinner/ANSI (for non-TTY / debug)
    """

    def __init__(self, mode="steps"):
        self._mode = mode
        self._spinner = None
        self._last_message = None

    def update(self, message):
        if self._mode == "steps":
            if self._spinner:
                self._spinner.succeed()
            self._spinner = halo.Halo(text=message, spinner="bouncingBar")
            self._spinner.start()
        elif self._mode == "inline":
            print(f"\r\033[K{message}", end="", flush=True)
            self._last_message = message
        else:
            print(message)

    def finish(self):
        if self._mode == "steps":
            if self._spinner:
                self._spinner.succeed()
                self._spinner = None
        elif self._mode == "inline":
            if self._last_message:
                print()
                self._last_message = None

    def fail(self, message=None):
        if self._mode == "steps":
            if self._spinner:
                self._spinner.fail(message)
                self._spinner = None
        elif self._mode == "inline":
            print(f"\r\033[K{message or 'Failed'}")
        else:
            print(message or "Failed")


def _progress_mode(ctx):
    debug = ctx.obj.get("debug", False) if ctx.obj else False
    if debug:
        return "plain"
    if not sys.stderr.isatty():
        return "plain"
    return PROGRESS_MODE


def _complete_server(ctx, param, incomplete):
    from gsm.control.state import ServerState
    state = ServerState()
    return [
        CompletionItem(r.name, help=f"{r.game} - {r.status}")
        for r in state.list_all()
        if r.name.startswith(incomplete) or r.id.startswith(incomplete)
    ]


def _complete_game(ctx, param, incomplete):
    _load_games()
    return [
        CompletionItem(g.name, help=g.display_name)
        for g in list_games()
        if g.name.startswith(incomplete)
    ]


def _complete_snapshot(ctx, param, incomplete):
    from gsm.control.state import SnapshotState
    state = SnapshotState()
    return [
        CompletionItem(s.id, help=f"{s.game} - {s.server_name}")
        for s in state.list_all()
        if s.id.startswith(incomplete)
    ]


def _complete_command(ctx, param, incomplete):
    return [
        CompletionItem(name, help=(cli.get_command(ctx, name).get_short_help_str(80) or ""))
        for name in cli.list_commands(ctx)
        if name.startswith(incomplete)
    ]


def _make_provisioner(ctx, **kwargs):
    """Create a Provisioner with debug wiring from the CLI context."""
    debug = ctx.obj.get("debug", False) if ctx.obj else False
    p = Provisioner(debug=debug, **kwargs)
    if debug:
        p.on_debug = lambda msg: console.log(f"[dim]{msg}[/]")
    return p


class HelpfulCommand(click.Command):
    """Show full help text when a command is invoked incorrectly."""

    def parse_args(self, ctx, args):
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            click.echo(ctx.get_help())
            click.echo()
            console.print(f"[bold red]Error:[/] {e.format_message()}")
            ctx.exit(2)


class HelpfulGroup(click.Group):
    command_class = HelpfulCommand


def _load_games():
    """Import all game modules to trigger registration."""
    import gsm.games.factorio  # noqa: F401

    from gsm.games.lgsm_catalog import register_lgsm_catalog
    register_lgsm_catalog()


@click.group(cls=HelpfulGroup)
@click.version_option(version="0.3.0", prog_name="gsmc")
@click.option("--debug", is_flag=True, help="Show SSH commands and output")
@click.pass_context
def cli(ctx, debug):
    """Game Server Maker - Launch game servers on AWS EC2 with Docker."""
    _load_games()
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug


@cli.command(context_settings={"ignore_unknown_options": True})
@click.argument("command", required=False, default=None, shell_complete=_complete_command)
@click.pass_context
def help(ctx, command):
    """Show help for a command."""
    if command:
        cmd = cli.get_command(ctx, command)
        if cmd is None:
            console.print(f"[red]Unknown command: {command}[/]")
            raise SystemExit(1)
        click.echo(cmd.get_help(ctx))
    else:
        click.echo(ctx.parent.get_help())


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion(shell):
    """Generate shell completion script."""
    from click.shell_completion import get_completion_class
    comp_cls = get_completion_class(shell)
    comp = comp_cls(cli, {}, "gsmc", "_GSMC_COMPLETE")
    click.echo(comp.source())


@cli.command()
def games():
    """List supported games."""
    all_games = list_games()
    if not all_games:
        click.echo("No games registered.")
        return

    table = Table(title="Supported Games")
    table.add_column("Name", style="cyan")
    table.add_column("Display Name", style="green")
    table.add_column("Type", style="blue")
    table.add_column("Image", style="yellow")
    table.add_column("Instance Type", style="magenta")
    table.add_column("Ports")

    for g in sorted(all_games, key=lambda x: x.name):
        ports = ", ".join(f"{p.port}/{p.protocol}" for p in g.ports)
        game_type = "LinuxGSM" if g.lgsm_server_code else "Docker"
        table.add_row(g.name, g.display_name, game_type, g.image, g.default_instance_type, ports)

    console.print(table)


@cli.command()
@click.argument("game_name", shell_complete=_complete_game)
@click.option("--instance-type", "-t", default=None, help="EC2 instance type")
@click.option("--region", "-r", default="us-east-1", help="AWS region")
@click.option("--name", "-n", default=None, help="Server name")
@click.option("--upload", "-u", multiple=True, help="Upload file LOCAL:REMOTE")
@click.option("--from-snapshot", default=None, help="Launch from a snapshot ID")
@click.option("--config", "-c", multiple=True, help="Config option KEY=VALUE")
@click.option("--config-file", default=None, type=click.Path(exists=True), help="Path to config file")
@click.option("--pin-ip", is_flag=True, help="Pin a static Elastic IP to this server")
@click.pass_context
def launch(ctx, game_name, instance_type, region, name, upload, from_snapshot, config, config_file, pin_ip):
    """Launch a game server."""
    game = get_game(game_name)
    if not game:
        console.print(f"[red]Unknown game: {game_name}[/]")
        raise SystemExit(1)

    # Snapshot restores reuse the existing container — no config changes allowed
    if from_snapshot and (upload or config or config_file):
        console.print("[red]--from-snapshot cannot be combined with -c, -u, or --config-file.[/]")
        console.print("Snapshot restores reuse the original container and config as-is.")
        raise SystemExit(1)

    # Route -c values based on game type
    lgsm_config_overrides = {}
    env_overrides = {}
    for c in config:
        if "=" not in c:
            console.print(f"[red]Invalid config format: {c} (expected KEY=VALUE)[/]")
            raise SystemExit(1)
        key, value = c.split("=", 1)
        if game.lgsm_server_code:
            lgsm_config_overrides[key] = value
        else:
            env_overrides[key] = value

    uploads = []
    for u in upload:
        if ":" not in u:
            console.print(f"[red]Invalid upload format: {u} (expected LOCAL:REMOTE)[/]")
            raise SystemExit(1)
        local_path, remote_path = u.split(":", 1)
        uploads.append((local_path, remote_path))

    provisioner = _make_provisioner(ctx)
    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        record = provisioner.launch(
            game=game, region=region, instance_type=instance_type,
            name=name, env_overrides=env_overrides or None,
            uploads=uploads or None, from_snapshot=from_snapshot,
            lgsm_config_overrides=lgsm_config_overrides or None,
            lgsm_config_file=config_file,
            pin_ip=pin_ip,
        )
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    result_lines = [
        f"[bold]ID:[/]         {record.id}",
        f"[bold]Name:[/]       {record.name}",
        f"[bold]IP:[/]         {record.public_ip}",
        f"[bold]Instance:[/]   {record.instance_id}",
        f"[bold]Connect:[/]    {record.connection_string}",
    ]
    if record.eip_public_ip:
        result_lines.append(f"[bold]Pinned IP:[/]  {record.eip_public_ip}")
    if record.rcon_password:
        result_lines.append(f"[bold]RCON Pass:[/]   {record.rcon_password}")
    for key in game.password_keys:
        value = record.config.get(key, "")
        if value:
            label = key.replace("_", " ").title()
            result_lines.append(f"[bold]{label}:[/]   {value}")
    console.print(Panel("\n".join(result_lines), title="[green]Server Launched[/]", border_style="green"))


@cli.command("list")
def list_servers():
    """List all running servers."""
    provisioner = Provisioner()
    provisioner.auto_reconcile()
    records = provisioner.state.list_all()
    if not records:
        console.print("No servers running.")
        return

    has_rcon = any(r.rcon_password for r in records)
    has_eip = any(r.eip_public_ip for r in records)

    table = Table(title="Game Servers")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Game", style="yellow")
    table.add_column("IP", style="magenta")
    table.add_column("Region")
    table.add_column("Status")
    if has_eip:
        table.add_column("Pinned IP", style="blue")
    if has_rcon:
        table.add_column("RCON Password", style="dim")

    for r in records:
        row = [r.id, r.name, r.game, r.public_ip, r.region, r.status]
        if has_eip:
            row.append(r.eip_public_ip)
        if has_rcon:
            row.append(r.rcon_password)
        table.add_row(*row)

    console.print(table)


@cli.command()
@click.argument("server", shell_complete=_complete_server)
def info(server):
    """Show details for a server."""
    provisioner = Provisioner()
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    console.print(f"[bold]Server: {record.name}[/]")
    console.print(f"  ID:              {record.id}")
    console.print(f"  Game:            {record.game}")
    console.print(f"  Instance ID:     {record.instance_id}")
    console.print(f"  Region:          {record.region}")
    console.print(f"  Public IP:       {record.public_ip}")
    console.print(f"  Status:          {record.status}")
    console.print(f"  Container:       {record.container_name}")
    console.print(f"  Security Group:  {record.security_group_id}")
    console.print(f"  Launch Time:     {record.launch_time}")
    console.print(f"  Connect:         {record.connection_string}")
    if record.eip_allocation_id:
        console.print(f"  Pinned IP:       {record.eip_public_ip}")
        console.print(f"  Allocation ID:   {record.eip_allocation_id}")
    if record.rcon_password:
        console.print(f"  RCON Password:   {record.rcon_password}")
    if record.ports:
        console.print("  Ports:")
        for port_spec, port_num in record.ports.items():
            console.print(f"    {port_spec} -> {port_num}")

    if record.config:
        console.print("  Config:")
        for key, value in record.config.items():
            console.print(f"    {key} = {value}")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.option("--all", "destroy_all", is_flag=True, help="Destroy all servers")
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def destroy(ctx, server, destroy_all, yes):
    """Destroy a server."""
    provisioner = _make_provisioner(ctx)

    if destroy_all:
        if not yes:
            click.confirm("Destroy ALL servers?", abort=True)
        progress = StepProgress(mode=_progress_mode(ctx))
        progress.update("Destroying all servers...")
        try:
            provisioner.destroy_all()
            progress.finish()
        except KeyboardInterrupt:
            progress.fail("Interrupted")
            console.print("\n[yellow]Interrupted.[/]")
            raise SystemExit(130)
        except Exception as e:
            progress.fail(str(e))
            console.print(f"[bold red]Error:[/] {e}")
            raise SystemExit(1)
        console.print("[green]All servers destroyed.[/]")
        return

    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    if not yes:
        click.confirm(f"Destroy server {record.name} ({record.id})?", abort=True)

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        provisioner.destroy(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    console.print(f"[green]Server {record.name} destroyed.[/]")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.option("--tail", "-n", default=None, type=int, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output")
@click.pass_context
def logs(ctx, server, tail, follow):
    """Show server container logs."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    ssh = None
    progress = StepProgress(mode=_progress_mode(ctx))
    progress.update("Connecting to server...")
    try:
        ssh = provisioner.get_ssh_client(record.id)
        docker = RemoteDocker(ssh)
        container_name = provisioner._resolve_container(record.id, docker)
        if not follow:
            exit_code, output = docker.logs(container_name, tail=tail)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)

    if follow:
        try:
            for chunk in docker.logs_follow(container_name, tail=tail):
                sys.stdout.write(chunk)
                sys.stdout.flush()
        except KeyboardInterrupt:
            pass
        finally:
            if ssh:
                ssh.close()
        return

    if ssh:
        ssh.close()

    if exit_code != 0:
        console.print(f"[red]Failed to get logs: {output}[/]")
        raise SystemExit(1)
    console.print(output)


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.pass_context
def stop(ctx, server):
    """Stop a server container (keeps instance running)."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)
    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        provisioner.stop_container(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    console.print(f"[green]Server {record.name} stopped.[/]")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
def ssh(server):
    """SSH into a server instance."""
    from gsm.control.ssh import ensure_key_pair

    provisioner = Provisioner()
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    key_path = ensure_key_pair(record.region)
    os.execvp("ssh", [
        "ssh", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=no",
        f"ec2-user@{record.public_ip}",
    ])


@cli.command("exec")
@click.argument("server", shell_complete=_complete_server)
@click.argument("command", nargs=-1, required=True)
@click.pass_context
def exec_cmd(ctx, server, command):
    """Execute a command in the server container."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    ssh_client = None
    progress = StepProgress(mode=_progress_mode(ctx))
    progress.update("Connecting to server...")
    try:
        ssh_client = provisioner.get_ssh_client(record.id)
        docker = RemoteDocker(ssh_client)
        container_name = provisioner._resolve_container(record.id, docker)
        cmd_str = " ".join(command)
        exit_code, output = docker.exec(container_name, cmd_str)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    finally:
        if ssh_client:
            ssh_client.close()
    if output:
        console.print(output)
    if exit_code != 0:
        raise SystemExit(exit_code)


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.argument("command", nargs=-1, required=True)
def rcon(server, command):
    """Send an RCON command to the server."""
    from rcon.source import Client as RconClient

    provisioner = Provisioner()
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    game = get_game(record.game)
    if not game or not game.rcon_port:
        console.print(f"[red]Game {record.game} does not support RCON[/]")
        raise SystemExit(1)

    rcon_password = record.rcon_password
    if not rcon_password and game.rcon_password_key:
        rcon_password = record.config.get(game.rcon_password_key, "")
    if not rcon_password:
        console.print("[red]No RCON password found. Set one via -c KEY=VALUE.[/]")
        raise SystemExit(1)
    cmd_str = " ".join(command)

    with RconClient(record.public_ip, game.rcon_port, passwd=rcon_password) as client:
        response = client.run(cmd_str)
        console.print(response)


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.argument("local_path")
@click.argument("container_path")
@click.pass_context
def upload(ctx, server, local_path, container_path):
    """Upload a file to the server container."""
    import uuid as _uuid

    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    ssh_client = None
    progress = StepProgress(mode=_progress_mode(ctx))
    progress.update("Connecting to server...")
    try:
        ssh_client = provisioner.get_ssh_client(record.id)
        docker = RemoteDocker(ssh_client)
        container_name = provisioner._resolve_container(record.id, docker)

        remote_tmp = f"/tmp/{_uuid.uuid4().hex[:8]}"
        ssh_client.upload_file(local_path, remote_tmp)
        docker.cp_to(container_name, remote_tmp, container_path)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    finally:
        if ssh_client:
            ssh_client.close()
    console.print(f"[green]Uploaded {local_path} to {container_path}[/]")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.argument("container_path")
@click.argument("local_path")
@click.pass_context
def download(ctx, server, container_path, local_path):
    """Download a file from the server container."""
    import uuid as _uuid

    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    ssh_client = None
    progress = StepProgress(mode=_progress_mode(ctx))
    progress.update("Connecting to server...")
    try:
        ssh_client = provisioner.get_ssh_client(record.id)
        docker = RemoteDocker(ssh_client)
        container_name = provisioner._resolve_container(record.id, docker)

        remote_tmp = f"/tmp/{_uuid.uuid4().hex[:8]}"
        docker.cp_from(container_name, container_path, remote_tmp)
        ssh_client.download_file(remote_tmp, local_path)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    finally:
        if ssh_client:
            ssh_client.close()
    console.print(f"[green]Downloaded {container_path} to {local_path}[/]")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.pass_context
def pause(ctx, server):
    """Pause a server (stop instance, keep data)."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        provisioner.pause(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    console.print(f"[green]Server {record.name} paused.[/]")
    if record.eip_allocation_id:
        console.print(
            f"[yellow]Note:[/] Elastic IP {record.eip_public_ip} is still allocated "
            f"(~$3.65/month while server is paused)."
        )


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.pass_context
def resume(ctx, server):
    """Resume a paused server."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        updated = provisioner.resume(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    result_text = (
        f"[bold]Name:[/]       {updated.name}\n"
        f"[bold]IP:[/]         {updated.public_ip}\n"
        f"[bold]Connect:[/]    {updated.connection_string}"
    )
    console.print(Panel(result_text, title="[green]Server Resumed[/]", border_style="green"))


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.pass_context
def pin(ctx, server):
    """Pin a static Elastic IP to a server."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        updated = provisioner.pin_ip(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    result_text = (
        f"[bold]Server:[/]     {updated.name}\n"
        f"[bold]Pinned IP:[/]  {updated.eip_public_ip}\n"
        f"[bold]Connect:[/]    {updated.connection_string}\n"
        f"[dim]EIP is free while server is running. ~$3.65/month while paused.[/]"
    )
    console.print(Panel(result_text, title="[green]Elastic IP Pinned[/]", border_style="green"))


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def unpin(ctx, server, yes):
    """Remove the pinned Elastic IP from a server."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    if not yes:
        click.confirm(
            f"Unpin Elastic IP {record.eip_public_ip} from {record.name}? "
            f"The server will get a new IP.",
            abort=True,
        )

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        updated = provisioner.unpin_ip(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    console.print(f"[green]Elastic IP unpinned from {updated.name}.[/]")
    if updated.public_ip:
        console.print(f"New IP: {updated.public_ip}")


@cli.command()
@click.option("--cleanup", is_flag=True, help="Prompt to release orphaned EIPs")
@click.pass_context
def eips(ctx, cleanup):
    """List all GSM-managed Elastic IPs."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    try:
        eip_list = provisioner.list_eips()
    except Exception as e:
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)

    if not eip_list:
        console.print("No GSM-managed Elastic IPs found.")
        return

    table = Table(title="Elastic IPs")
    table.add_column("Allocation ID", style="cyan")
    table.add_column("Public IP", style="green")
    table.add_column("Server", style="yellow")
    table.add_column("Region")
    table.add_column("Status")

    for eip in eip_list:
        status = "associated" if eip["associated"] else "unassociated"
        server = eip["server_name"] or eip["server_id"] or "unknown"
        table.add_row(eip["allocation_id"], eip["public_ip"], server, eip["region"], status)

    console.print(table)

    if cleanup:
        orphaned = [e for e in eip_list if not e["server_name"]]
        if not orphaned:
            console.print("[green]No orphaned EIPs found.[/]")
            return
        for eip in orphaned:
            if click.confirm(f"Release orphaned EIP {eip['public_ip']} ({eip['allocation_id']})?"):
                try:
                    provisioner.cleanup_eip(eip["allocation_id"], eip["region"])
                    console.print(f"[green]Released {eip['public_ip']}[/]")
                except Exception as e:
                    console.print(f"[red]Failed to release {eip['public_ip']}: {e}[/]")


@cli.command()
@click.argument("server", shell_complete=_complete_server)
@click.pass_context
def snapshot(ctx, server):
    """Create a snapshot of a server."""
    provisioner = _make_provisioner(ctx)
    provisioner.auto_reconcile()
    record = provisioner.state.get_by_name_or_id(server)
    if not record:
        console.print(f"[red]Server not found: {server}[/]")
        raise SystemExit(1)

    progress = StepProgress(mode=_progress_mode(ctx))
    provisioner.on_status = progress.update
    try:
        snap = provisioner.snapshot(record.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    result_text = (
        f"[bold]ID:[/]          {snap.id}\n"
        f"[bold]Snapshot:[/]    {snap.snapshot_id}\n"
        f"[bold]Game:[/]        {snap.game}\n"
        f"[bold]Server:[/]      {snap.server_name}\n"
        f"[bold]Created:[/]     {snap.created_at}"
    )
    console.print(Panel(result_text, title="[green]Snapshot Created[/]", border_style="green"))


@cli.command()
def snapshots():
    """List all snapshots."""
    provisioner = Provisioner()
    provisioner.auto_reconcile()
    snaps = provisioner.list_snapshots()
    if not snaps:
        console.print("No snapshots.")
        return

    table = Table(title="Snapshots")
    table.add_column("ID", style="cyan")
    table.add_column("Snapshot ID", style="green")
    table.add_column("Game", style="yellow")
    table.add_column("Server", style="magenta")
    table.add_column("Region")
    table.add_column("Status")
    table.add_column("Created")

    for s in snaps:
        table.add_row(s.id, s.snapshot_id, s.game, s.server_name, s.region, s.status, s.created_at)

    console.print(table)


@cli.command("snapshot-delete")
@click.argument("snapshot_id", shell_complete=_complete_snapshot)
@click.option("--yes", "-y", is_flag=True, help="Skip confirmation")
@click.pass_context
def snapshot_delete(ctx, snapshot_id, yes):
    """Delete a snapshot."""
    provisioner = Provisioner()
    provisioner.auto_reconcile()
    snap = provisioner.snapshot_state.get(snapshot_id)
    if not snap:
        console.print(f"[red]Snapshot not found: {snapshot_id}[/]")
        raise SystemExit(1)

    if not yes:
        click.confirm(f"Delete snapshot {snap.id} ({snap.snapshot_id})?", abort=True)

    progress = StepProgress(mode=_progress_mode(ctx))
    progress.update("Deleting snapshot...")
    try:
        provisioner.delete_snapshot(snap.id)
        progress.finish()
    except KeyboardInterrupt:
        progress.fail("Interrupted")
        console.print("\n[yellow]Interrupted.[/]")
        raise SystemExit(130)
    except Exception as e:
        progress.fail(str(e))
        console.print(f"[bold red]Error:[/] {e}")
        raise SystemExit(1)
    console.print(f"[green]Snapshot {snap.id} deleted.[/]")


def _generate_lgsm_config_file_content(game) -> str:
    """Generate a LinuxGSM .cfg file with defaults and commented options."""
    lines = [
        f"# {game.display_name} - LinuxGSM Configuration",
        f"# Usage: gsmc launch {game.name} --config-file <this-file>",
        "",
    ]

    # Active defaults from the catalog
    if game.defaults:
        lines.append("# Defaults")
        for key, value in game.defaults.items():
            lines.append(f'{key}="{value}"')
        lines.append("")

    # Additional options from synced JSON (commented out)
    extra_options = {
        k: v for k, v in game.config_options.items()
        if k not in game.defaults
    }
    if extra_options:
        lines.append("# Other options (uncomment to customize)")
        for key, opt in extra_options.items():
            desc = f"  # {opt['description']}" if opt.get("description") else ""
            lines.append(f'# {key}="{opt["default"]}"{desc}')
        lines.append("")

    return "\n".join(lines) + "\n"


def _generate_env_file_content(game) -> str:
    """Generate a .env config file for a Docker game."""
    lines = [
        f"# {game.display_name} - Configuration",
        f"# Usage: gsmc launch {game.name} --config-file <this-file>",
        "",
    ]

    if game.defaults:
        for key, value in game.defaults.items():
            lines.append(f"{key}={value}")
        lines.append("")

    return "\n".join(lines) + "\n"


@cli.command()
@click.argument("game_name", shell_complete=_complete_game)
@click.option("--init", is_flag=True, help="Generate a local config file")
@click.option("-o", "--output", default=None, help="Output path (default: <game>.cfg)")
def config(game_name, init, output):
    """Show or generate configuration for a game."""
    game = get_game(game_name)
    if not game:
        console.print(f"[red]Unknown game: {game_name}[/]")
        raise SystemExit(1)

    if init:
        if game.lgsm_server_code:
            content = _generate_lgsm_config_file_content(game)
            out_path = output or f"{game_name}.cfg"
        else:
            content = _generate_env_file_content(game)
            out_path = output or f"{game_name}.cfg"
        with open(out_path, "w") as f:
            f.write(content)
        console.print(f"[green]Config file written to {out_path}[/]")
        console.print(f"Launch with: gsmc launch {game_name} --config-file {out_path}")
        return

    # Display mode
    if game.lgsm_server_code:
        # LinuxGSM game
        if game.defaults:
            table = Table(title=f"{game.display_name} - Defaults")
            table.add_column("Key", style="cyan")
            table.add_column("Value", style="green")
            for key, value in game.defaults.items():
                table.add_row(key, value)
            console.print(table)

        extra_options = {
            k: v for k, v in game.config_options.items()
            if k not in game.defaults
        }
        if extra_options:
            opt_table = Table(title="Other Options")
            opt_table.add_column("Key", style="cyan")
            opt_table.add_column("Default", style="yellow")
            opt_table.add_column("Description", style="dim")
            for key, opt in extra_options.items():
                opt_table.add_row(key, opt["default"], opt.get("description", ""))
            console.print(opt_table)

        console.print(
            f"\nUsage: gsmc launch {game_name} -c key=value"
            f"\n       gsmc config {game_name} --init  (generate config file)"
        )
    else:
        # Docker game
        if game.defaults:
            table = Table(title=f"{game.display_name} - Config Options")
            table.add_column("Key", style="cyan")
            table.add_column("Default", style="green")
            for key, value in game.defaults.items():
                table.add_row(key, value)
            console.print(table)

        console.print(
            f"\nUsage: gsmc launch {game_name} -c KEY=VALUE"
            f"\n       gsmc config {game_name} --init  (generate config file)"
        )


@cli.command()
@click.option("--list", "list_all", is_flag=True, help="List all LinuxGSM games")
@click.option("--add", "add_code", metavar="SERVER_CODE", help="Add a game to the catalog")
@click.option("--all", "sync_everything", is_flag=True, help="Add all LinuxGSM games and sync configs")
def sync(list_all, add_code, sync_everything):
    """Sync LinuxGSM game configs from upstream."""
    from gsm.games.lgsm_sync import (
        add_all_games, add_game_to_catalog, fetch_serverlist,
        get_catalog_server_codes, load_catalog, save_catalog, sync_all_configs,
    )

    if list_all:
        serverlist = fetch_serverlist()
        catalog = load_catalog()
        catalog_codes = set(get_catalog_server_codes(catalog).keys())

        table = Table(title="LinuxGSM Games")
        table.add_column("Short Name", style="cyan")
        table.add_column("Server Code", style="green")
        table.add_column("Game", style="yellow")
        table.add_column("In Catalog", style="magenta")

        for row in serverlist:
            code = row["gameservername"]
            in_cat = "*" if code in catalog_codes else ""
            table.add_row(row["shortname"], code, row["gamename"], in_cat)

        console.print(table)
        console.print(f"\n{len(serverlist)} games total, {len(catalog_codes)} in catalog")
        return

    if sync_everything:
        catalog = load_catalog()
        added, skipped = add_all_games(catalog, console)
        save_catalog(catalog)
        console.print(f"\nAdded {added} games, skipped {skipped} (no config)")
        console.print(f"Catalog now has {len(catalog)} games total\n")
        console.print("Syncing config options for all catalog games...")
        synced = sync_all_configs(catalog, console)
        console.print(f"\n[green]Done. Synced {synced} games.[/]")
        return

    if add_code:
        catalog = load_catalog()
        console.print(f"Fetching config for {add_code}...")
        serverlist = fetch_serverlist()
        result = add_game_to_catalog(catalog, add_code, serverlist)
        if isinstance(result, str):
            console.print(f"[red]{result}[/]")
            raise SystemExit(1)
        gsm_name, entry = result
        save_catalog(catalog)
        console.print(f"[green]Added '{gsm_name}' to catalog[/]")
        console.print(f"  Game:     {entry['display_name']}")
        console.print(f"  Ports:    {len(entry['ports'])}")
        console.print(f"\nRun [cyan]gsmc sync[/] to sync config options")
        return

    # Default: sync config options for catalog games
    catalog = load_catalog()
    if not catalog:
        console.print("No games in catalog. Use [cyan]gsmc sync --add <server_code>[/] to add one.")
        return
    console.print("Syncing LinuxGSM configs for catalog games...")
    synced = sync_all_configs(catalog, console)
    console.print(f"\n[green]Done. Synced {synced} games.[/]")


@cli.command()
@click.option("--port", "-p", default=8080, help="API port")
@click.option("--host", default="127.0.0.1", help="API host")
def api(port, host):
    """Start the local REST API server."""
    from gsm.api import create_app
    import uvicorn

    app = create_app()
    console.print(f"[green]Starting API server on {host}:{port}[/]")
    uvicorn.run(app, host=host, port=port)
