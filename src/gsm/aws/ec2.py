import boto3


DOCKER_USER_DATA = """#!/bin/bash
yum install -y docker
systemctl enable docker
systemctl start docker
usermod -aG docker ec2-user
"""


def launch_instance(
    region: str, ami_id: str, instance_type: str, key_name: str,
    security_group_id: str, subnet_id: str | None = None,
    game_name: str = "", server_id: str = "", server_name: str = "",
    disk_gb: int = 100,
) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    kwargs = {
        "ImageId": ami_id, "InstanceType": instance_type,
        "KeyName": key_name, "SecurityGroupIds": [security_group_id],
        "MinCount": 1, "MaxCount": 1, "UserData": DOCKER_USER_DATA,
        "BlockDeviceMappings": [{
            "DeviceName": "/dev/xvda",
            "Ebs": {"VolumeSize": disk_gb, "VolumeType": "gp3"},
        }],
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [
                {"Key": "Name", "Value": f"gsm-{game_name}-{server_name}"},
                {"Key": "gsm:game", "Value": game_name},
                {"Key": "gsm:id", "Value": server_id},
                {"Key": "gsm:name", "Value": server_name},
            ],
        }],
    }
    if subnet_id:
        kwargs["SubnetId"] = subnet_id
    response = ec2.run_instances(**kwargs)
    return response["Instances"][0]["InstanceId"]


def find_gsm_instances(region: str) -> list[dict]:
    """Find all EC2 instances tagged with gsm:id in a region."""
    ec2 = boto3.client("ec2", region_name=region)
    paginator = ec2.get_paginator("describe_instances")
    results = []
    for page in paginator.paginate(
        Filters=[{"Name": "tag-key", "Values": ["gsm:id"]}],
    ):
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                state = instance["State"]["Name"]
                if state in ("terminated", "shutting-down"):
                    continue
                tags = {t["Key"]: t["Value"] for t in instance.get("Tags", [])}
                results.append({
                    "instance_id": instance["InstanceId"],
                    "state": state,
                    "public_ip": instance.get("PublicIpAddress"),
                    "gsm_id": tags.get("gsm:id", ""),
                    "gsm_game": tags.get("gsm:game", ""),
                    "gsm_name": tags.get("gsm:name", ""),
                })
    return results


def terminate_instance(region: str, instance_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    ec2.terminate_instances(InstanceIds=[instance_id])


def get_instance_public_ip(region: str, instance_id: str) -> str | None:
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instances = response["Reservations"][0]["Instances"]
    if instances:
        return instances[0].get("PublicIpAddress")
    return None


def wait_for_instance_running(region: str, instance_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(InstanceIds=[instance_id])


def stop_instance(region: str, instance_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    ec2.stop_instances(InstanceIds=[instance_id])


def start_instance(region: str, instance_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    ec2.start_instances(InstanceIds=[instance_id])


def wait_for_instance_stopped(region: str, instance_id: str) -> None:
    ec2 = boto3.client("ec2", region_name=region)
    waiter = ec2.get_waiter("instance_stopped")
    waiter.wait(InstanceIds=[instance_id])


def get_instance_root_volume_id(region: str, instance_id: str) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_instances(InstanceIds=[instance_id])
    instance = response["Reservations"][0]["Instances"][0]
    root_device = instance["RootDeviceName"]
    for mapping in instance.get("BlockDeviceMappings", []):
        if mapping["DeviceName"] == root_device:
            return mapping["Ebs"]["VolumeId"]
    raise RuntimeError(f"No root volume found for instance {instance_id}")
