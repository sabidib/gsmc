import boto3
from unittest.mock import patch, MagicMock
from moto import mock_aws
import pytest

from gsm.aws.ami import get_latest_al2023_ami

pytestmark = pytest.mark.uses_moto


@mock_aws
def test_get_latest_ami():
    ec2 = boto3.client("ec2", region_name="us-east-1")
    ec2.register_image(
        Name="al2023-ami-2023.0.20240101.0-kernel-6.1-x86_64",
        Description="Amazon Linux 2023 AMI",
        Architecture="x86_64",
        RootDeviceName="/dev/xvda",
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 8, "VolumeType": "gp3"}}],
        VirtualizationType="hvm",
    )
    ami_id = get_latest_al2023_ami("us-east-1")
    assert ami_id is not None and ami_id.startswith("ami-")


def test_get_latest_ami_no_results():
    mock_ec2 = MagicMock()
    mock_ec2.describe_images.return_value = {"Images": []}
    with patch("gsm.aws.ami.boto3.client", return_value=mock_ec2):
        try:
            get_latest_al2023_ami("us-east-1")
            assert False, "Should have raised"
        except RuntimeError as e:
            assert "No AL2023 AMI found" in str(e)
