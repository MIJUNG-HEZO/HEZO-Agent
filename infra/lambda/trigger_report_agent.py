"""
두 가지 경로로 호출됨:

1. EventBridge Rule (IaC 파이프라인 SUCCEEDED)
   event = {
     "source": "aws.states",
     "detail-type": "Step Functions Execution Status Change",
     "detail": {
       "status": "SUCCEEDED",
       "input": '{"detail":{"site_id":"UUID",...},...}'
     }
   }
   → site_id 추출 → 리포트 실행 → 7일 스케줄 등록

2. EventBridge Scheduler (7일 주기)
   event = {"site_id": "UUID"}
   → 리포트 실행
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "492554570964")
REPORT_AGENT_RUNTIME_ARN = os.environ["REPORT_AGENT_RUNTIME_ARN"]
SCHEDULER_ROLE_ARN = os.environ.get(
    "SCHEDULER_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/hezo-scheduler-role",
)
LAMBDA_ARN = os.environ.get(
    "LAMBDA_ARN",
    f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:hezo-trigger-report",
)


def _extract_site_id(event: dict) -> str:
    """이벤트 유형별 site_id 추출"""
    # 경로 1: EventBridge Scheduler 직접 호출 {"site_id": "..."}
    if "site_id" in event:
        return event["site_id"]

    # 경로 2: Step Functions Execution Status Change 이벤트
    raw_input = event.get("detail", {}).get("input", "")
    if raw_input:
        exec_input = json.loads(raw_input)
        # IaC 파이프라인 입력 구조: 최상위 detail.site_id
        site_id = (
            exec_input.get("detail", {}).get("site_id")
            or exec_input.get("site_id")
        )
        if site_id:
            return site_id

    raise ValueError(f"site_id를 찾을 수 없음 — event keys: {list(event.keys())}")


def _invoke_report_agent(site_id: str, request_id: str) -> int:
    """AgentCore Report Agent를 SigV4 서명 HTTP 요청으로 호출"""
    payload = json.dumps({
        "sessionId": f"sched-{site_id[:8]}",
        "inputText": f"site_id={site_id}",
        "sessionAttributes": {
            "site_id": site_id,
            "pipeline_run_id": request_id,
        },
    })

    encoded_arn = urllib.parse.quote(REPORT_AGENT_RUNTIME_ARN, safe="")
    url = f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations"

    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()

    aws_req = AWSRequest(
        method="POST",
        url=url,
        data=payload.encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(aws_req)

    req = urllib.request.Request(
        url=url,
        data=payload.encode(),
        headers=dict(aws_req.headers),
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=540) as resp:
            body = resp.read()
            print(f"[OK] site_id={site_id} status={resp.status} body={body[:300]}")
            return resp.status
    except urllib.error.HTTPError as exc:
        body = exc.read()
        print(f"[ERR] site_id={site_id} status={exc.code} body={body[:500]}")
        raise


def _ensure_scheduler(site_id: str) -> None:
    """7일 주기 EventBridge Scheduler 등록 (이미 있으면 무시)"""
    client = boto3.client("scheduler", region_name=REGION)
    schedule_name = f"hezo-report-{site_id}"

    try:
        client.get_schedule(Name=schedule_name)
        print(f"[SCHEDULER] 이미 존재: {schedule_name}")
        return
    except client.exceptions.ResourceNotFoundException:
        pass

    client.create_schedule(
        Name=schedule_name,
        ScheduleExpression="rate(7 days)",
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": LAMBDA_ARN,
            "RoleArn": SCHEDULER_ROLE_ARN,
            "Input": json.dumps({"site_id": site_id}),
        },
        Description=f"HEZO 리포트 에이전트 주기 실행 — site_id={site_id}",
    )
    print(f"[SCHEDULER] 등록 완료: {schedule_name} (rate 7 days)")


def lambda_handler(event, context):
    site_id = _extract_site_id(event)
    print(f"[START] site_id={site_id}")

    # 1. 리포트 에이전트 실행
    http_status = _invoke_report_agent(site_id, context.aws_request_id)

    # 2. 7일 스케줄 등록 (없으면)
    _ensure_scheduler(site_id)

    return {
        "status": "triggered",
        "site_id": site_id,
        "http_status": http_status,
    }
