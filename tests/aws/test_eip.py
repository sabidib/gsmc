import boto3
from moto import mock_aws
import pytest

from gsm.aws.eip import (
    allocate_eip,
    associate_eip,
    disassociate_eip,
    release_eip,
    find_gsm_eips,
)

pytestmark = pytest.mark.uses_moto


def _launch_instance(ec2):
    """Launch a minimal EC2 instance for EIP association tests."""
    response = ec2.run_instances(
        ImageId="ami-test",
        InstanceType="t3.micro",
        MinCount=1,
        MaxCount=1,
    )
    return response["Instances"][0]["InstanceId"]


@mock_aws
def test_allocate_eip():
    alloc_id, public_ip = allocate_eip("us-east-1", "srv-123")
    assert alloc_id.startswith("eipalloc-")
    assert public_ip  # non-empty IP

    # Verify tag was applied
    ec2 = boto3.client("ec2", region_name="us-east-1")
    addresses = ec2.describe_addresses(AllocationIds=[alloc_id])
    tags = {t["Key"]: t["Value"] for t in addresses["Addresses"][0].get("Tags", [])}
    assert tags["gsm:id"] == "srv-123"


@mock_aws
def test_associate_eip():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    alloc_id, _ = allocate_eip("us-east-1", "srv-456")

    assoc_id = associate_eip("us-east-1", alloc_id, instance_id)
    assert assoc_id  # non-empty association ID

    # Verify association
    addresses = ec2.describe_addresses(AllocationIds=[alloc_id])
    assert addresses["Addresses"][0]["InstanceId"] == instance_id


@mock_aws
def test_disassociate_eip():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    instance_id = _launch_instance(ec2)
    alloc_id, _ = allocate_eip("us-east-1", "srv-789")
    associate_eip("us-east-1", alloc_id, instance_id)

    disassociate_eip("us-east-1", alloc_id)

    # Verify disassociation
    addresses = ec2.describe_addresses(AllocationIds=[alloc_id])
    assert addresses["Addresses"][0].get("InstanceId", "") == ""


@mock_aws
def test_disassociate_eip_idempotent():
    """Disassociate is a no-op when EIP is not associated."""
    alloc_id, _ = allocate_eip("us-east-1", "srv-noop")
    disassociate_eip("us-east-1", alloc_id)  # Should not raise


@mock_aws
def test_release_eip():
    alloc_id, _ = allocate_eip("us-east-1", "srv-release")
    release_eip("us-east-1", alloc_id)

    ec2 = boto3.client("ec2", region_name="us-east-1")
    addresses = ec2.describe_addresses(
        Filters=[{"Name": "allocation-id", "Values": [alloc_id]}],
    )
    assert len(addresses["Addresses"]) == 0


@mock_aws
def test_find_gsm_eips_tagged_vs_untagged():
    ec2 = boto3.client("ec2", region_name="us-east-1")

    # Create a tagged EIP via our function
    allocate_eip("us-east-1", "srv-find")

    # Create an untagged EIP directly
    ec2.allocate_address(Domain="vpc")

    results = find_gsm_eips("us-east-1")
    assert len(results) == 1
    tags = {t["Key"]: t["Value"] for t in results[0].get("Tags", [])}
    assert tags["gsm:id"] == "srv-find"
