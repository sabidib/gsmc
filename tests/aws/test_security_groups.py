import boto3
from moto import mock_aws
import pytest

from gsm.aws.security_groups import get_or_create_security_group
from gsm.games.registry import GamePort

pytestmark = pytest.mark.uses_moto


@mock_aws
def test_create_security_group():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    ports = [GamePort(port=25565, protocol="tcp"), GamePort(port=25575, protocol="tcp")]
    sg_id = get_or_create_security_group("us-east-1", "factorio", ports, vpc_id)
    assert sg_id.startswith("sg-")
    sgs = ec2.describe_security_groups(GroupIds=[sg_id])
    assert sgs["SecurityGroups"][0]["GroupName"] == "gsm-factorio-sg"


@mock_aws
def test_reuses_existing_security_group():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    ports = [GamePort(port=25565, protocol="tcp")]
    sg_id1 = get_or_create_security_group("us-east-1", "factorio", ports, vpc_id)
    sg_id2 = get_or_create_security_group("us-east-1", "factorio", ports, vpc_id)
    assert sg_id1 == sg_id2


@mock_aws
def test_security_group_has_ssh_rule():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    ports = [GamePort(port=25565, protocol="tcp")]
    sg_id = get_or_create_security_group("us-east-1", "factorio", ports, vpc_id)
    sgs = ec2.describe_security_groups(GroupIds=[sg_id])
    rules = sgs["SecurityGroups"][0]["IpPermissions"]
    ssh_rules = [r for r in rules if r.get("FromPort") == 22]
    assert len(ssh_rules) == 1
    cidrs = [rng["CidrIp"] for rng in ssh_rules[0]["IpRanges"]]
    assert "0.0.0.0/0" in cidrs


@mock_aws
def test_create_security_group_has_gsm_tag():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")
    vpc_id = vpc["Vpc"]["VpcId"]
    ports = [GamePort(port=25565, protocol="tcp")]
    sg_id = get_or_create_security_group("us-east-1", "factorio", ports, vpc_id)
    sgs = ec2.describe_security_groups(GroupIds=[sg_id])
    tags = {t["Key"]: t["Value"] for t in sgs["SecurityGroups"][0].get("Tags", [])}
    assert tags.get("gsm:id") == "factorio"
    assert tags.get("Name") == "gsm-factorio-sg"
