"""
생성 에이전트 로컬 호출 테스트

AgentCore CLI(`invoke-agent-runtime`)는 payload를 암호화해서 보내기 때문에
컨테이너가 JSON 파싱 실패한다. 이 스크립트는 평문 JSON + SigV4 서명으로
AgentCore 엔드포인트에 직접 HTTP POST하는 올바른 방법을 사용한다.

사용법:
  python agents/generation/test_generation_invoke.py
  python agents/generation/test_generation_invoke.py --site-id site_tax_13_001 --run-id test-001
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from urllib.parse import quote
import urllib.request
import urllib.error

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = "ap-northeast-2"
RUNTIME_ARN = "arn:aws:bedrock-agentcore:ap-northeast-2:492554570964:runtime/hezo_generation_agent-GPmRKmCFnL"
QUALIFIER = "DEFAULT"


def invoke_generation_agent(site_id: str, pipeline_run_id: str) -> dict:
    session = boto3.Session()
    creds = session.get_credentials().get_frozen_credentials()

    encoded_arn = quote(RUNTIME_ARN, safe="")
    url = (
        f"https://bedrock-agentcore.ap-northeast-2.amazonaws.com"
        f"/runtimes/{encoded_arn}/invocations?qualifier={QUALIFIER}"
    )
    session_id = f"test-{uuid.uuid4().hex}"  # 33자 이상 필요

    payload = json.dumps({"site_id": site_id, "pipeline_run_id": pipeline_run_id}).encode()

    print(f"[invoke] site_id={site_id} run_id={pipeline_run_id}")
    print(f"[invoke] url={url}")

    req = AWSRequest(
        method="POST",
        url=url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    SigV4Auth(creds, "bedrock-agentcore", REGION).add_auth(req)

    http_req = urllib.request.Request(url, data=payload, headers=dict(req.headers), method="POST")
    with urllib.request.urlopen(http_req, timeout=360) as resp:
        body = resp.read().decode()
        return json.loads(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-id", default="site_tax_13_001")
    parser.add_argument("--run-id", default=f"test-{uuid.uuid4().hex[:8]}")
    args = parser.parse_args()

    try:
        result = invoke_generation_agent(args.site_id, args.run_id)
        print("\n[결과]")
        print(f"  output : {result.get('output')}")
        meta = result.get("metadata", {})
        print(f"  status : {meta.get('status')}")
        print(f"  rule   : {meta.get('eval_score')}")
        print(f"  llm    : {meta.get('llm_eval_score')}")
        print(f"  s3_key : {meta.get('render_spec_key')}")
        print(f"  patched: {meta.get('weak_sections_patched')}")
    except urllib.error.HTTPError as e:
        print(f"[오류] HTTP {e.code}: {e.read().decode()[:300]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[오류] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
