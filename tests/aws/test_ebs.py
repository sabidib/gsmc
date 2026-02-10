import boto3
from moto import mock_aws
import pytest

from gsm.aws.ebs import (
    create_snapshot,
    wait_for_snapshot_complete,
    delete_snapshot,
    find_amis_using_snapshot,
    list_snapshots,
    register_ami_from_snapshot,
    deregister_ami,
)

pytestmark = pytest.mark.uses_moto


def _create_volume(ec2):
    vol = ec2.create_volume(
        AvailabilityZone="us-east-1a", Size=8, VolumeType="gp3",
    )
    return vol["VolumeId"]


@mock_aws
def test_create_snapshot():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    snapshot_id = create_snapshot(
        "us-east-1", volume_id, description="test snapshot",
        tags={"gsm:id": "srv-123", "gsm:game": "factorio"},
    )
    assert snapshot_id.startswith("snap-")


@mock_aws
def test_wait_for_snapshot_complete():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    snapshot_id = create_snapshot("us-east-1", volume_id)
    # moto completes instantly
    wait_for_snapshot_complete("us-east-1", snapshot_id)
    snaps = ec2.describe_snapshots(SnapshotIds=[snapshot_id])
    assert snaps["Snapshots"][0]["State"] == "completed"


@mock_aws
def test_delete_snapshot():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    snapshot_id = create_snapshot("us-east-1", volume_id)
    delete_snapshot("us-east-1", snapshot_id)
    snaps = ec2.describe_snapshots(OwnerIds=["self"])
    snap_ids = [s["SnapshotId"] for s in snaps["Snapshots"]]
    assert snapshot_id not in snap_ids


@mock_aws
def test_list_snapshots_filtered_by_tag():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    # Create tagged snapshot
    create_snapshot(
        "us-east-1", volume_id, tags={"gsm:id": "srv-1"},
    )
    # Create untagged snapshot
    ec2.create_snapshot(VolumeId=volume_id, Description="untagged")
    results = list_snapshots("us-east-1")
    assert len(results) == 1
    tags = {t["Key"]: t["Value"] for t in results[0].get("Tags", [])}
    assert tags["gsm:id"] == "srv-1"


@mock_aws
def test_register_ami_from_snapshot():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    snapshot_id = create_snapshot("us-east-1", volume_id)
    ami_id = register_ami_from_snapshot(
        "us-east-1", snapshot_id, name="gsm-restore-test",
        description="test AMI",
    )
    assert ami_id.startswith("ami-")
    images = ec2.describe_images(ImageIds=[ami_id])
    assert images["Images"][0]["Name"] == "gsm-restore-test"


def test_find_amis_using_snapshot(monkeypatch):
    """find_amis_using_snapshot returns AMI IDs whose BDMs reference the snapshot."""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.describe_images.return_value = {
        "Images": [
            {
                "ImageId": "ami-match",
                "BlockDeviceMappings": [
                    {"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": "snap-target"}},
                ],
            },
            {
                "ImageId": "ami-other",
                "BlockDeviceMappings": [
                    {"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": "snap-other"}},
                ],
            },
        ]
    }
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: mock_client)

    result = find_amis_using_snapshot("us-east-1", "snap-target")
    assert result == ["ami-match"]


def test_find_amis_using_snapshot_empty(monkeypatch):
    """find_amis_using_snapshot returns empty list when no AMIs reference the snapshot."""
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    mock_client.describe_images.return_value = {"Images": []}
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: mock_client)

    result = find_amis_using_snapshot("us-east-1", "snap-nonexistent")
    assert result == []


@mock_aws
def test_deregister_ami():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    volume_id = _create_volume(ec2)
    snapshot_id = create_snapshot("us-east-1", volume_id)
    ami_id = register_ami_from_snapshot("us-east-1", snapshot_id, name="gsm-dereg")
    deregister_ami("us-east-1", ami_id)
    images = ec2.describe_images(ImageIds=[ami_id])
    assert len(images["Images"]) == 0
