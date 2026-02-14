import boto3
from moto import mock_aws
import pytest

from gsm.aws.security_groups import find_gsm_security_groups
from gsm.aws.ec2 import find_gsm_key_pairs
from gsm.aws.ebs import find_gsm_amis, create_snapshot

pytestmark = pytest.mark.uses_moto


# ── find_gsm_security_groups ──


@mock_aws
def test_find_gsm_security_groups_returns_tagged():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    sg = ec2.create_security_group(
        GroupName="gsm-factorio-sg",
        Description="test",
        VpcId=vpc_id,
        TagSpecifications=[{
            "ResourceType": "security-group",
            "Tags": [{"Key": "gsm:id", "Value": "factorio"}],
        }],
    )
    # Also create an untagged SG to ensure filtering works
    ec2.create_security_group(GroupName="other-sg", Description="other", VpcId=vpc_id)

    results = find_gsm_security_groups("us-east-1")
    assert len(results) == 1
    assert results[0]["group_id"] == sg["GroupId"]
    assert results[0]["group_name"] == "gsm-factorio-sg"
    assert results[0]["vpc_id"] == vpc_id


@mock_aws
def test_find_gsm_security_groups_empty():
    results = find_gsm_security_groups("us-east-1")
    assert results == []


# ── find_gsm_key_pairs ──


@mock_aws
def test_find_gsm_key_pairs_returns_matching():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    kp = ec2.create_key_pair(KeyName="gsm-key")
    # Also create an unrelated key pair
    ec2.create_key_pair(KeyName="other-key")

    results = find_gsm_key_pairs("us-east-1")
    assert len(results) == 1
    assert results[0]["key_name"] == "gsm-key"
    assert results[0]["key_pair_id"]


@mock_aws
def test_find_gsm_key_pairs_empty():
    results = find_gsm_key_pairs("us-east-1")
    assert results == []


# ── find_gsm_amis ──


@mock_aws
def test_find_gsm_amis_returns_matching():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vol = ec2.create_volume(AvailabilityZone="us-east-1a", Size=8, VolumeType="gp3")
    snap_id = create_snapshot("us-east-1", vol["VolumeId"])
    # Register an AMI with gsm- prefix
    ami = ec2.register_image(
        Name="gsm-restore-test",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{
            "DeviceName": "/dev/xvda",
            "Ebs": {"SnapshotId": snap_id},
        }],
    )
    # Register an AMI without gsm- prefix
    ec2.register_image(
        Name="other-ami",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{
            "DeviceName": "/dev/xvda",
            "Ebs": {"SnapshotId": snap_id},
        }],
    )

    results = find_gsm_amis("us-east-1")
    assert len(results) == 1
    assert results[0]["image_id"] == ami["ImageId"]
    assert results[0]["name"] == "gsm-restore-test"


@mock_aws
def test_find_gsm_amis_empty():
    results = find_gsm_amis("us-east-1")
    assert results == []
