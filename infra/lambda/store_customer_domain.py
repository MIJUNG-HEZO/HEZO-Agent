"""
IaC 파이프라인 — 도메인 저장 Lambda.

CloudFormation Outputs 배열에서 DomainUrl, CloudFrontDistributionId를 찾아
DynamoDB hezo_pipeline_state에 기록한다.

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

    outputs_map = {o.get("OutputKey"): o.get("OutputValue") for o in outputs}

    domain_url = outputs_map.get("DomainUrl")
    if not domain_url:
        raise ValueError(f"DomainUrl not found in stack outputs for site_id={site_id}")

    cf_dist_id = outputs_map.get("CloudFrontDistributionId")

    ddb = boto3.client("dynamodb", region_name=_REGION)

    update_expr = "SET domain_url = :u, publish_status = :s"
    expr_values: dict = {":u": {"S": domain_url}, ":s": {"S": "published"}}

    if cf_dist_id:
        update_expr += ", cloudfront_distribution_id = :cf"
        expr_values[":cf"] = {"S": cf_dist_id}

    ddb.update_item(
        TableName=_TABLE,
        Key={"site_id": {"S": site_id}},
        UpdateExpression=update_expr,
        ExpressionAttributeValues=expr_values,
    )

    return {"site_id": site_id, "domain_url": domain_url, "cloudfront_distribution_id": cf_dist_id}
