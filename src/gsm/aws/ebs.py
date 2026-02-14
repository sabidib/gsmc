import boto3


def create_snapshot(
    region: str, volume_id: str, description: str = "",
    tags: dict[str, str] | None = None,
) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    tag_specs = []
    if tags:
        tag_specs = [{
            "ResourceType": "snapshot",
            "Tags": [{"Key": k, "Value": v} for k, v in tags.items()],
        }]
    kwargs = {"VolumeId": volume_id, "Description": description}
    if tag_specs:
        kwargs["TagSpecifications"] = tag_specs
    response = ec2.create_snapshot(**kwargs)
    return response["SnapshotId"]


def wait_for_snapshot_complete(region: str, snapshot_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    waiter = ec2.get_waiter("snapshot_completed")
    waiter.wait(SnapshotIds=[snapshot_id])


def delete_snapshot(region: str, snapshot_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    ec2.delete_snapshot(SnapshotId=snapshot_id)


def list_snapshots(region: str) -> list[dict]:
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_snapshots(
        OwnerIds=["self"],
        Filters=[{"Name": "tag-key", "Values": ["gsm:id"]}],
    )
    return response["Snapshots"]


def register_ami_from_snapshot(
    region: str, snapshot_id: str, name: str, description: str = "",
) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.register_image(
        Name=name,
        Description=description,
        Architecture="x86_64",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{
            "DeviceName": "/dev/xvda",
            "Ebs": {"SnapshotId": snapshot_id, "VolumeType": "gp3"},
        }],
        VirtualizationType="hvm",
        EnaSupport=True,
    )
    return response["ImageId"]


def find_amis_using_snapshot(region: str, snapshot_id: str) -> list[str]:
    """Return AMI IDs whose block device mappings reference the given snapshot."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_images(Owners=["self"])
    result = []
    for img in response.get("Images", []):
        for bdm in img.get("BlockDeviceMappings", []):
            if bdm.get("Ebs", {}).get("SnapshotId") == snapshot_id:
                result.append(img["ImageId"])
                break
    return result


def find_gsm_amis(region: str) -> list[dict]:
    """Find all self-owned AMIs with gsm- name prefix in a region."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_images(
        Owners=["self"],
        Filters=[{"Name": "name", "Values": ["gsm-*"]}],
    )
    results = []
    for img in response.get("Images", []):
        results.append({
            "image_id": img["ImageId"],
            "name": img.get("Name", ""),
            "state": img.get("State", ""),
            "creation_date": img.get("CreationDate", ""),
        })
    return results


def deregister_ami(region: str, ami_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    ec2.deregister_image(ImageId=ami_id)
