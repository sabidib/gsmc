import boto3


def get_latest_al2023_ami(region: str) -> str:
    ec2 = boto3.client("ec2", region_name=region)
    response = ec2.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": ["al2023-ami-*-x86_64"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
            {"Name": "state", "Values": ["available"]},
        ],
    )
    images = response.get("Images", [])
    if not images:
        raise RuntimeError(f"No AL2023 AMI found in region {region}")
    images.sort(key=lambda x: x.get("CreationDate", ""), reverse=True)
    return images[0]["ImageId"]
