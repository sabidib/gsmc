import boto3
from moto import mock_aws
import pytest

from gsm.aws.ec2 import (
    stop_instance,
    start_instance,
    wait_for_instance_stopped,
    get_instance_root_volume_id,
)

pytestmark = pytest.mark.uses_moto


def _launch_instance(ec2):
    resp = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
    )
    return resp["Instances"][0]["InstanceId"]


@mock_aws
def test_stop_instance():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    stop_instance("us-east-1", instance_id)
    state = ec2.describe_instances(InstanceIds=[instance_id])
    status = state["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert status in ("stopped", "stopping")


@mock_aws
def test_start_instance():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    ec2.stop_instances(InstanceIds=[instance_id])
    start_instance("us-east-1", instance_id)
    state = ec2.describe_instances(InstanceIds=[instance_id])
    status = state["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert status in ("running", "pending")


@mock_aws
def test_wait_for_instance_stopped():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    ec2.stop_instances(InstanceIds=[instance_id])
    # moto transitions instantly, so wait should succeed
    wait_for_instance_stopped("us-east-1", instance_id)
    state = ec2.describe_instances(InstanceIds=[instance_id])
    status = state["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert status == "stopped"


@mock_aws
def test_get_instance_root_volume_id():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    volume_id = get_instance_root_volume_id("us-east-1", instance_id)
    assert volume_id.startswith("vol-")
