import shlex
import time

from gsm.control.ssh import SSHClient
from gsm.games.registry import GamePort

DOCKER = "sudo docker"


class RemoteDocker:
    def __init__(self, ssh: SSHClient):
        self.ssh = ssh

    def wait_for_docker(self, retries: int = 30, delay: int = 5) -> None:
        for attempt in range(retries):
            exit_code, _ = self.ssh.run(f"{DOCKER} info > /dev/null 2>&1")
            if exit_code == 0:
                return
            if attempt == retries - 1:
                raise RuntimeError("Docker did not become available")
            time.sleep(delay)

    def pull(self, image: str) -> None:
        exit_code, output = self.ssh.run(f"{DOCKER} pull {shlex.quote(image)}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to pull image {image}: {output}")

    def _build_docker_args(
        self, container_name: str, image: str, ports: list[GamePort],
        env: dict[str, str], volumes: list[str], extra_args: list[str] | None = None,
    ) -> str:
        parts = [f"--name {shlex.quote(container_name)}"]
        for port in ports:
            parts.append(f"-p {port.docker_publish()}")
        for key, value in env.items():
            parts.append(f"-e {shlex.quote(f'{key}={value}')}")
        for i, vol in enumerate(volumes):
            volume_name = f"{container_name}-data-{i}"
            parts.append(f"-v {shlex.quote(f'{volume_name}:{vol}')}")
        if extra_args:
            parts.extend(extra_args)
        parts.append(shlex.quote(image))
        return " ".join(parts)

    def run(self, container_name: str, image: str, ports: list[GamePort],
            env: dict[str, str], volumes: list[str], extra_args: list[str] | None = None) -> None:
        args = self._build_docker_args(container_name, image, ports, env, volumes, extra_args)
        cmd = f"{DOCKER} run -d {args}"
        exit_code, output = self.ssh.run(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to start container: {output}")

    def create(self, container_name: str, image: str, ports: list[GamePort],
               env: dict[str, str], volumes: list[str], extra_args: list[str] | None = None) -> None:
        args = self._build_docker_args(container_name, image, ports, env, volumes, extra_args)
        cmd = f"{DOCKER} create {args}"
        exit_code, output = self.ssh.run(cmd)
        if exit_code != 0:
            raise RuntimeError(f"Failed to create container: {output}")

    def start(self, container_name: str) -> None:
        exit_code, output = self.ssh.run(f"{DOCKER} start {shlex.quote(container_name)}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to start container: {output}")

    def stop(self, container_name: str) -> None:
        exit_code, output = self.ssh.run(f"{DOCKER} stop {shlex.quote(container_name)}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to stop container: {output}")

    def rm(self, container_name: str) -> None:
        exit_code, output = self.ssh.run(f"{DOCKER} rm {shlex.quote(container_name)}")
        if exit_code != 0:
            raise RuntimeError(f"Failed to remove container: {output}")

    def _ensure_container_dir(self, container_name: str, path: str) -> None:
        """Create a directory tree inside a container (works even if stopped)."""
        import posixpath
        stripped = posixpath.normpath(path).lstrip("/")
        if not stripped:
            return
        staging = "/tmp/_gsm_mkdir"
        self.ssh.run(
            f"rm -rf {staging} && "
            f"mkdir -p {staging}/{shlex.quote(stripped)} && "
            f"tar -cf - -C {staging} . | "
            f"{DOCKER} cp - {shlex.quote(f'{container_name}:/')} && "
            f"rm -rf {staging}"
        )

    def cp_to(self, container_name: str, src: str, dest: str) -> None:
        import posixpath
        parent = posixpath.dirname(dest)
        if parent and parent != "/":
            self._ensure_container_dir(container_name, parent)
        exit_code, output = self.ssh.run(
            f"{DOCKER} cp {shlex.quote(src)} {shlex.quote(f'{container_name}:{dest}')}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to copy file: {output}")

    def cp_from(self, container_name: str, src: str, dest: str) -> None:
        exit_code, output = self.ssh.run(
            f"{DOCKER} cp {shlex.quote(f'{container_name}:{src}')} {shlex.quote(dest)}"
        )
        if exit_code != 0:
            raise RuntimeError(f"Failed to copy file: {output}")

    def find_gsm_container(self) -> str | None:
        """Find a gsm-managed container (running or stopped). Returns name or None."""
        exit_code, output = self.ssh.run(
            f"{DOCKER} ps -a --filter name=gsm- --format '{{{{.Names}}}}'"
        )
        if exit_code != 0 or not output.strip():
            return None
        return output.strip().split("\n")[0]

    def is_running(self, container_name: str) -> bool:
        exit_code, output = self.ssh.run(
            f"{DOCKER} inspect --format='{{{{.State.Running}}}}' {shlex.quote(container_name)}"
        )
        return exit_code == 0 and output.strip() == "true"

    def exec(self, container_name: str, command: str) -> tuple[int, str]:
        return self.ssh.run(f"{DOCKER} exec {shlex.quote(container_name)} {command}")

    def logs(self, container_name: str, tail: int | None = None) -> tuple[int, str]:
        cmd = f"{DOCKER} logs {shlex.quote(container_name)}"
        if tail:
            cmd += f" --tail {tail}"
        return self.ssh.run(cmd)

    def logs_follow(self, container_name: str, tail: int | None = None):
        cmd = f"{DOCKER} logs -f {shlex.quote(container_name)}"
        if tail:
            cmd += f" --tail {tail}"
        yield from self.ssh.run_streaming(cmd)
