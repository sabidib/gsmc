import hashlib
import time
from pathlib import Path

import boto3
import paramiko
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


DEFAULT_KEY_DIR = Path.home() / ".gsm" / "keys"
KEY_NAME = "gsm-key"
SSM_KEY_PARAM = "/gsmc/ssh-private-key"
SSM_REGION = "us-east-1"


class SSHClient:
    def __init__(self, host: str, key_path: str, username: str = "ec2-user", on_debug=None):
        self.host = host
        self.key_path = key_path
        self.username = username
        self.on_debug = on_debug
        self._client: paramiko.SSHClient | None = None

    def connect(self, retries: int = 12, delay: int = 10) -> None:
        for attempt in range(retries):
            try:
                self._client = paramiko.SSHClient()
                self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                self._client.connect(
                    hostname=self.host, username=self.username,
                    key_filename=self.key_path, timeout=10,
                    banner_timeout=30,
                )
                return
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(delay)

    def run_streaming(self, command: str):
        """Yield stdout chunks from a long-running command."""
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        _, stdout, _ = self._client.exec_command(command)
        channel = stdout.channel
        try:
            while not channel.exit_status_ready():
                if channel.recv_ready():
                    yield channel.recv(4096).decode()
            # Drain remaining data
            while channel.recv_ready():
                yield channel.recv(4096).decode()
        finally:
            channel.close()

    def run(self, command: str) -> tuple[int, str]:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        if self.on_debug:
            self.on_debug(f"$ {command}")
        _, stdout, stderr = self._client.exec_command(command)
        output = stdout.read().decode()
        err = stderr.read().decode()
        exit_code = stdout.channel.recv_exit_status()
        if err:
            output = output + err
        if self.on_debug:
            self.on_debug(f"  exit={exit_code}" + (f"\n  {output.strip()}" if output.strip() else ""))
        return exit_code, output

    def upload_file(self, local_path: str, remote_path: str) -> None:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        sftp = self._client.open_sftp()
        sftp.put(local_path, remote_path)
        sftp.close()

    def download_file(self, remote_path: str, local_path: str) -> None:
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        sftp = self._client.open_sftp()
        sftp.get(remote_path, local_path)
        sftp.close()

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None


def _fetch_key_from_ssm(key_path: Path) -> bool:
    """Try to download the shared SSH key from SSM Parameter Store.
    Returns True if the key was fetched and saved locally."""
    try:
        ssm = boto3.client("ssm", region_name=SSM_REGION)
        response = ssm.get_parameter(Name=SSM_KEY_PARAM, WithDecryption=True)
        key_path.write_text(response["Parameter"]["Value"])
        key_path.chmod(0o600)
        return True
    except Exception:
        return False


def _store_key_in_ssm(key_path: Path) -> bool:
    """Upload the SSH private key to SSM Parameter Store.
    Returns True if stored successfully, False otherwise."""
    try:
        ssm = boto3.client("ssm", region_name=SSM_REGION)
        ssm.put_parameter(
            Name=SSM_KEY_PARAM,
            Value=key_path.read_text(),
            Type="SecureString",
        )
        return True
    except Exception:
        return False


def _compute_fingerprint(key_path: Path) -> str:
    """Compute the MD5 fingerprint AWS uses for imported key pairs."""
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    pub_der = key.key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    digest = hashlib.md5(pub_der).hexdigest()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def ensure_key_pair(region: str, key_dir: Path = DEFAULT_KEY_DIR) -> Path:
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / f"{KEY_NAME}.pem"

    # SSM is the source of truth — always check it first.
    # This ensures all machines converge on the same key, even if
    # a local key already exists from before SSM was introduced.
    if _fetch_key_from_ssm(key_path):
        pass  # Got the shared key from SSM
    elif not key_path.exists():
        # No SSM key and no local key — generate one
        key = paramiko.RSAKey.generate(4096)
        key.write_private_key_file(str(key_path))
        key_path.chmod(0o600)

    # Ensure local key is in SSM (first machine to run stores it).
    # If store fails, another machine may have raced us — fetch theirs.
    if not _store_key_in_ssm(key_path):
        _fetch_key_from_ssm(key_path)

    # Ensure the EC2 key pair in this region matches the local key
    public_key = _get_public_key_from_private(key_path)
    local_fp = _compute_fingerprint(key_path)
    ec2 = boto3.client("ec2", region_name=region)
    try:
        existing = ec2.describe_key_pairs(KeyNames=[KEY_NAME])
        if existing["KeyPairs"][0]["KeyFingerprint"] == local_fp:
            return key_path
        ec2.delete_key_pair(KeyName=KEY_NAME)
    except Exception:
        pass
    ec2.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=public_key)

    return key_path


def _get_public_key_from_private(key_path: Path) -> bytes:
    key = paramiko.RSAKey.from_private_key_file(str(key_path))
    return f"{key.get_name()} {key.get_base64()}".encode()
