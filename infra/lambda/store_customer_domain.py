"""
IaC 파이프라인 — 도메인 저장 Lambda.

CloudFormation Outputs 배열에서 DomainUrl을 찾아
DynamoDB hezo_pipeline_state에 domain_url을 기록한다.

Step Functions Input:
  {
    "site_id": "...",
    "outputs": [{"OutputKey": "DomainUrl", "OutputValue": "https://..."}, ...]
  }
"""
import os
import boto3

_TABLE = os.environ.get("PIPELINE_TABLE", "hezo_pipeline_state")
_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")


def lambda_handler(event, context):
    site_id = event["site_id"]
    outputs = event.get("outputs", [])

    domain_url = next(
        (o["OutputValue"] for o in outputs if o.get("OutputKey") == "DomainUrl"),
        None,
    )
    if not domain_url:
        raise ValueError(f"DomainUrl not found in stack outputs for site_id={site_id}")

    ddb = boto3.client("dynamodb", region_name=_REGION)
    ddb.update_item(
        TableName=_TABLE,
        Key={"site_id": {"S": site_id}},
        UpdateExpression="SET domain_url = :u",
        ExpressionAttributeValues={":u": {"S": domain_url}},
    )

    return {"site_id": site_id, "domain_url": domain_url}
