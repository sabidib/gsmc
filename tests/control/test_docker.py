from unittest.mock import MagicMock

from gsm.control.docker import RemoteDocker
from gsm.games.registry import GamePort


def make_mock_ssh():
    ssh = MagicMock()
    ssh.run.return_value = (0, "")
    return ssh


def test_pull_image():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    docker.pull("factoriotools/factorio")
    ssh.run.assert_called_with("sudo docker pull factoriotools/factorio")


def test_run_container():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    ports = [GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")]
    env = {"EULA": "TRUE", "TYPE": "VANILLA"}
    docker.run(container_name="gsm-factorio-abc", image="factoriotools/factorio",
               ports=ports, env=env, volumes=["/data"])
    call_args = ssh.run.call_args[0][0]
    assert "sudo docker run -d" in call_args
    assert "--name gsm-factorio-abc" in call_args
    assert "-p 25565:25565/tcp" in call_args
    assert "-e EULA=TRUE" in call_args
    assert "-v gsm-factorio-abc-data-0:/data" in call_args


def test_create_and_start_container():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    ports = [GamePort(port=25565, protocol="tcp")]
    env = {"EULA": "TRUE"}
    docker.create(container_name="gsm-fact-123", image="factoriotools/factorio",
                  ports=ports, env=env, volumes=["/data"])
    call_args = ssh.run.call_args[0][0]
    assert "sudo docker create" in call_args
    docker.start("gsm-fact-123")
    ssh.run.assert_called_with("sudo docker start gsm-fact-123")


def test_copy_to_container():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    docker.cp_to("gsm-fact-123", "/tmp/server.properties", "/data/server.properties")
    ssh.run.assert_called_with(
        "sudo docker cp /tmp/server.properties gsm-fact-123:/data/server.properties"
    )


def test_stop_container():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    docker.stop("gsm-fact-123")
    ssh.run.assert_called_with("sudo docker stop gsm-fact-123")


def test_remove_container():
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    docker.rm("gsm-fact-123")
    ssh.run.assert_called_with("sudo docker rm gsm-fact-123")


def test_is_running():
    ssh = make_mock_ssh()
    ssh.run.return_value = (0, "true\n")
    docker = RemoteDocker(ssh)
    assert docker.is_running("gsm-fact-123") is True


def test_wait_for_docker():
    ssh = make_mock_ssh()
    ssh.run.side_effect = [(1, ""), (1, ""), (0, "")]
    docker = RemoteDocker(ssh)
    docker.wait_for_docker(retries=3, delay=0)
    assert ssh.run.call_count == 3


def test_logs_follow():
    ssh = make_mock_ssh()
    ssh.run_streaming.return_value = iter(["line1\n", "line2\n"])
    docker = RemoteDocker(ssh)
    chunks = list(docker.logs_follow("gsm-fact-123"))
    ssh.run_streaming.assert_called_with("sudo docker logs -f gsm-fact-123")
    assert chunks == ["line1\n", "line2\n"]


def test_logs_follow_with_tail():
    ssh = make_mock_ssh()
    ssh.run_streaming.return_value = iter(["line1\n"])
    docker = RemoteDocker(ssh)
    list(docker.logs_follow("gsm-fact-123", tail=50))
    ssh.run_streaming.assert_called_with("sudo docker logs -f gsm-fact-123 --tail 50")


def test_find_gsm_container():
    ssh = make_mock_ssh()
    ssh.run.return_value = (0, "gsm-factorio-abc12345\n")
    docker = RemoteDocker(ssh)
    assert docker.find_gsm_container() == "gsm-factorio-abc12345"


def test_find_gsm_container_none():
    ssh = make_mock_ssh()
    ssh.run.return_value = (0, "")
    docker = RemoteDocker(ssh)
    assert docker.find_gsm_container() is None


def test_find_gsm_container_multiple_returns_first():
    ssh = make_mock_ssh()
    ssh.run.return_value = (0, "gsm-factorio-abc\ngsm-halo-def\n")
    docker = RemoteDocker(ssh)
    assert docker.find_gsm_container() == "gsm-factorio-abc"


def test_shlex_quoting_with_special_chars():
    """Verify shlex.quote kicks in for values with special characters."""
    ssh = make_mock_ssh()
    docker = RemoteDocker(ssh)
    ports = [GamePort(port=25565, protocol="tcp")]
    env = {"MSG": "hello world"}
    docker.run(container_name="my server", image="my image",
               ports=ports, env=env, volumes=["/data"])
    call_args = ssh.run.call_args[0][0]
    assert "--name 'my server'" in call_args
    assert "-e 'MSG=hello world'" in call_args
    assert "'my image'" in call_args
