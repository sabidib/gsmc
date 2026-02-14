from unittest.mock import MagicMock, patch

from gsm.control.provisioner import Provisioner


def test_list_all_resources_paid_only(tmp_path, make_server_record):
    """list_all_resources returns paid resources by default."""
    p = Provisioner(state_dir=tmp_path)
    p.state.save(make_server_record(region="us-east-1"))

    mock_instances = [{"instance_id": "i-123", "state": "running", "gsm_game": "factorio"}]
    mock_eips = [{"AllocationId": "eipalloc-1", "PublicIp": "1.2.3.4", "Tags": [{"Key": "gsm:id", "Value": "srv-1"}]}]
    mock_snapshots = [{
        "SnapshotId": "snap-1", "State": "completed", "VolumeSize": 100,
        "Description": "test", "Tags": [{"Key": "gsm:id", "Value": "srv-1"}],
    }]
    mock_amis = [{"image_id": "ami-1", "name": "gsm-restore", "state": "available", "creation_date": "2025-01-01"}]

    with patch("gsm.control.provisioner.find_gsm_instances", return_value=mock_instances), \
         patch("gsm.control.provisioner.find_gsm_eips", return_value=mock_eips), \
         patch("gsm.control.provisioner.aws_list_snapshots", return_value=mock_snapshots), \
         patch("gsm.control.provisioner.find_gsm_amis", return_value=mock_amis), \
         patch.object(p, "_get_active_regions", return_value=set()):
        result = p.list_all_resources(include_free=False)

    assert len(result["instances"]) == 1
    assert len(result["eips"]) == 1
    assert len(result["snapshots"]) == 1
    assert len(result["amis"]) == 1
    # Free resources should not be present
    assert "security_groups" not in result
    assert "key_pairs" not in result
    assert "ssm_parameters" not in result


def test_list_all_resources_include_free(tmp_path, make_server_record):
    """list_all_resources with include_free=True includes SGs, key pairs, SSM."""
    p = Provisioner(state_dir=tmp_path)
    p.state.save(make_server_record(region="us-east-1"))

    mock_sgs = [{"group_id": "sg-1", "group_name": "gsm-factorio-sg", "vpc_id": "vpc-1"}]
    mock_kps = [{"key_name": "gsm-key", "key_pair_id": "key-1"}]

    # Mock SSM paginator
    mock_ssm = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [{
        "Parameters": [
            {"Name": "/gsmc/active-regions", "Type": "String"},
            {"Name": "/gsmc/ssh-private-key", "Type": "SecureString"},
        ],
    }]
    mock_ssm.get_paginator.return_value = mock_paginator
    mock_ssm.get_parameter.side_effect = [
        {"Parameter": {"Value": "us-east-1"}},
        {"Parameter": {"Value": "-----BEGIN RSA KEY-----"}},
    ]

    with patch("gsm.control.provisioner.find_gsm_instances", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_eips", return_value=[]), \
         patch("gsm.control.provisioner.aws_list_snapshots", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_amis", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_security_groups", return_value=mock_sgs), \
         patch("gsm.control.provisioner.find_gsm_key_pairs", return_value=mock_kps), \
         patch("gsm.control.provisioner.boto3") as mock_boto3, \
         patch.object(p, "_get_active_regions", return_value=set()):
        mock_boto3.client.return_value = mock_ssm
        result = p.list_all_resources(include_free=True)

    assert len(result["security_groups"]) == 1
    assert result["security_groups"][0]["group_id"] == "sg-1"
    assert len(result["key_pairs"]) == 1
    assert result["key_pairs"][0]["key_name"] == "gsm-key"
    assert len(result["ssm_parameters"]) == 2
    # SSH key value should be masked
    ssh_param = [p for p in result["ssm_parameters"] if "ssh" in p["name"]]
    assert ssh_param[0]["value"] == "****"


def test_list_all_resources_empty(tmp_path):
    """list_all_resources returns empty lists when no resources found."""
    p = Provisioner(state_dir=tmp_path)

    with patch("gsm.control.provisioner.find_gsm_instances", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_eips", return_value=[]), \
         patch("gsm.control.provisioner.aws_list_snapshots", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_amis", return_value=[]), \
         patch.object(p, "_get_active_regions", return_value=set()):
        result = p.list_all_resources()

    assert result == {"instances": [], "eips": [], "snapshots": [], "amis": []}


def test_list_all_resources_uses_active_regions(tmp_path, make_server_record):
    """list_all_resources queries regions from both local state and SSM."""
    p = Provisioner(state_dir=tmp_path)
    p.state.save(make_server_record(region="us-east-1"))

    call_regions = []

    def track_instances(region):
        call_regions.append(region)
        return []

    with patch("gsm.control.provisioner.find_gsm_instances", side_effect=track_instances), \
         patch("gsm.control.provisioner.find_gsm_eips", return_value=[]), \
         patch("gsm.control.provisioner.aws_list_snapshots", return_value=[]), \
         patch("gsm.control.provisioner.find_gsm_amis", return_value=[]), \
         patch.object(p, "_get_active_regions", return_value={"eu-west-1"}):
        p.list_all_resources()

    assert "us-east-1" in call_regions
    assert "eu-west-1" in call_regions
