import boto3
from moto import mock_aws
import pytest

from gsm.aws.ec2 import launch_instance, terminate_instance, find_gsm_instances

pytestmark = pytest.mark.uses_moto


@mock_aws
def test_launch_instance():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock="10.0.1.0/24")
    subnet_id = subnet["Subnet"]["SubnetId"]
    sg = ec2.create_security_group(GroupName="test-sg", Description="test", VpcId=vpc_id)
    sg_id = sg["GroupId"]
    instance_id = launch_instance(
        region="us-east-1", ami_id="ami-12345678", instance_type="t3.medium",
        key_name="gsm-key", security_group_id=sg_id, subnet_id=subnet_id,
        game_name="factorio", server_id="test-123", server_name="my-fact",
    )
    assert instance_id.startswith("i-")
    instances = ec2.describe_instances(InstanceIds=[instance_id])
    tags = {t["Key"]: t["Value"] for t in instances["Reservations"][0]["Instances"][0].get("Tags", [])}
    assert tags["gsm:game"] == "factorio"


def test_launch_instance_passes_block_device_mappings():
    """launch_instance passes BlockDeviceMappings with correct disk size."""
    from unittest.mock import patch, MagicMock

    mock_client = MagicMock()
    mock_client.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-mock123"}],
    }
    with patch("gsm.aws.ec2.boto3.client", return_value=mock_client):
        instance_id = launch_instance(
            region="us-east-1", ami_id="ami-12345678", instance_type="t3.medium",
            key_name="gsm-key", security_group_id="sg-123",
            game_name="factorio", server_id="test-456", server_name="my-fact2",
            disk_gb=150,
        )

    assert instance_id == "i-mock123"
    call_kwargs = mock_client.run_instances.call_args.kwargs
    assert call_kwargs["BlockDeviceMappings"] == [{
        "DeviceName": "/dev/xvda",
        "Ebs": {"VolumeSize": 150, "VolumeType": "gp3"},
    }]


def test_launch_instance_default_disk_size():
    """launch_instance defaults to 100 GB disk."""
    from unittest.mock import patch, MagicMock

    mock_client = MagicMock()
    mock_client.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-mock456"}],
    }
    with patch("gsm.aws.ec2.boto3.client", return_value=mock_client):
        launch_instance(
            region="us-east-1", ami_id="ami-12345678", instance_type="t3.medium",
            key_name="gsm-key", security_group_id="sg-123",
        )

    call_kwargs = mock_client.run_instances.call_args.kwargs
    assert call_kwargs["BlockDeviceMappings"][0]["Ebs"]["VolumeSize"] == 100


@mock_aws
def test_terminate_instance():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro")
    instance_id = resp["Instances"][0]["InstanceId"]
    terminate_instance("us-east-1", instance_id)
    state = ec2.describe_instances(InstanceIds=[instance_id])
    status = state["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert status in ("terminated", "shutting-down")


@mock_aws
def test_find_gsm_instances():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "gsm:id", "Value": "srv-001"},
                {"Key": "gsm:game", "Value": "factorio"},
                {"Key": "gsm:name", "Value": "my-fact"},
            ],
        }],
    )
    results = find_gsm_instances("us-east-1")
    assert len(results) == 1
    assert results[0]["gsm_id"] == "srv-001"
    assert results[0]["gsm_game"] == "factorio"
    assert results[0]["gsm_name"] == "my-fact"
    assert results[0]["state"] == "running"


@mock_aws
def test_find_gsm_instances_empty_region():
    results = find_gsm_instances("us-west-2")
    assert results == []


@mock_aws
def test_find_gsm_instances_skips_terminated():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    resp = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1, InstanceType="t3.micro",
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "gsm:id", "Value": "srv-term"},
                {"Key": "gsm:game", "Value": "factorio"},
                {"Key": "gsm:name", "Value": "my-factorio"},
            ],
        }],
    )
    instance_id = resp["Instances"][0]["InstanceId"]
    ec2.terminate_instances(InstanceIds=[instance_id])
    results = find_gsm_instances("us-east-1")
    assert all(r["gsm_id"] != "srv-term" for r in results)
