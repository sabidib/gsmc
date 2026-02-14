import boto3

from gsm.games.registry import GamePort


def get_or_create_security_group(
    region: str, game_name: str, ports: list[GamePort], vpc_id: str | None = None,
) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    sg_name = f"gsm-{game_name}-sg"

    filters = [{"Name": "group-name", "Values": [sg_name]}]
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})
    existing = ec2.describe_security_groups(Filters=filters)
    if existing["SecurityGroups"]:
        return existing["SecurityGroups"][0]["GroupId"]

    kwargs = {
        "GroupName": sg_name,
        "Description": f"GSM security group for {game_name}",
        "TagSpecifications": [{
            "ResourceType": "security-group",
            "Tags": [
                {"Key": "gsm:id", "Value": game_name},
                {"Key": "Name", "Value": sg_name},
            ],
        }],
    }
    if vpc_id:
        kwargs["VpcId"] = vpc_id
    sg = ec2.create_security_group(**kwargs)
    sg_id = sg["GroupId"]

    ip_permissions = [
        {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
         "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "SSH access"}]}
    ]
    for port in ports:
        ip_permissions.append(port.sg_rule())

    ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=ip_permissions)
    return sg_id


def find_gsm_security_groups(region: str) -> list[dict]:
    """Find all security groups tagged with gsm:id in a region."""
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_security_groups(
        Filters=[{"Name": "tag-key", "Values": ["gsm:id"]}],
    )
    results = []
    for sg in response["SecurityGroups"]:
        results.append({
            "group_id": sg["GroupId"],
            "group_name": sg["GroupName"],
            "vpc_id": sg.get("VpcId", ""),
        })
    return results
