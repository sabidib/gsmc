import boto3


def allocate_eip(region: str, server_id: str) -> tuple[str, str]:
    """Allocate an Elastic IP and tag it with gsm:id. Returns (allocation_id, public_ip)."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.allocate_address(
        Domain="vpc",
        TagSpecifications=[{
            "ResourceType": "elastic-ip",
            "Tags": [{"Key": "gsm:id", "Value": server_id}],
        }],
    )
    return response["AllocationId"], response["PublicIp"]


def associate_eip(region: str, allocation_id: str, instance_id: str) -> str:
    """Associate an EIP with an EC2 instance. Returns association_id."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.associate_address(
        AllocationId=allocation_id,
        InstanceId=instance_id,
    )
    return response["AssociationId"]


def disassociate_eip(region: str, allocation_id: str) -> None:
    """Disassociate an EIP. No-op if not currently associated."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_addresses(AllocationIds=[allocation_id])
    addresses = response.get("Addresses", [])
    if not addresses:
        return
    association_id = addresses[0].get("AssociationId")
    if association_id:
        ec2.disassociate_address(AssociationId=association_id)


def release_eip(region: str, allocation_id: str) -> None:
    """Permanently release (delete) an Elastic IP."""
    ec2 = boto3.client("ec2", region_name=region)
    ec2.release_address(AllocationId=allocation_id)


def find_gsm_eips(region: str) -> list[dict]:
    """Find all EIPs tagged with gsm:id."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_addresses(
        Filters=[{"Name": "tag-key", "Values": ["gsm:id"]}],
    )
    return response.get("Addresses", [])
