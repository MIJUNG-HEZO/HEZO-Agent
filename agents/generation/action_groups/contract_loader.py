"""
Lambda 함수: hezo-p4-contract-loader
Bedrock Agent Action Group - ContractLoader

역할:
- Bedrock Agent가 호출하는 액션 그룹 핸들러
- S3 hezo-artifacts 버킷에서 contract_final.json 을 읽어 에이전트에 반환
- 선택적으로 crawl_snapshot.json 도 읽어 반환 (크롤링 데이터가 있는 경우)

입력 (Bedrock Agent Action Group 이벤트 형식):
  - apiPath: "/get-contract" 또는 "/get-crawl-snapshot"
  - parameters: [{"name": "site_id", "type": "string", "value": "..."}]
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

from libs.telemetry import init_telemetry

# ─── 로거 설정 ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─── 텔레메트리 초기화 (Lambda 콜드스타트 시 1회) ────────────────────────────
# otlp=False: Lambda 환경에서는 ADOT 사이드카 없음 → CloudWatch Logs로만 출력
init_telemetry("generation", otlp=False)

# ─── AWS 클라이언트 (Lambda 실행 환경에서 재사용) ────────────────────────────
_s3_client = None


def _get_s3() -> Any:
    """S3 클라이언트 싱글턴 반환"""
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _s3_client


# ─── 환경변수 ────────────────────────────────────────────────────────────────
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "hezo-artifacts")


# =============================================================================
# Bedrock Agent 응답 포맷 헬퍼
# =============================================================================

def _bedrock_response(
    action_group: str,
    api_path: str,
    http_status: int,
    body: dict[str, Any],
) -> dict[str, Any]:
    """
    Bedrock Agent Action Group 표준 응답 형식으로 래핑.

    참고: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html
    """
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": "GET",
            "httpStatusCode": http_status,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(body, ensure_ascii=False)
                }
            },
        },
    }


def _error_response(
    action_group: str,
    api_path: str,
    http_status: int,
    error_code: str,
    message: str,
) -> dict[str, Any]:
    """에러 응답 생성"""
    logger.error("Action Group 오류 [%s %s]: %s - %s", api_path, http_status, error_code, message)
    return _bedrock_response(
        action_group=action_group,
        api_path=api_path,
        http_status=http_status,
        body={"error": error_code, "message": message},
    )


# =============================================================================
# 파라미터 추출 헬퍼
# =============================================================================

def _extract_param(parameters: list[dict], name: str) -> str | None:
    """Bedrock Agent 파라미터 목록에서 특정 이름의 값 추출"""
    for param in parameters:
        if param.get("name") == name:
            return param.get("value")
    return None


# =============================================================================
# S3 읽기 함수
# =============================================================================

def _read_s3_json(bucket: str, key: str) -> dict[str, Any]:
    """
    S3에서 JSON 파일을 읽어 dict로 반환.

    Raises:
        ClientError: S3 접근 오류 (NoSuchKey 포함)
        json.JSONDecodeError: JSON 파싱 오류
    """
    s3 = _get_s3()
    logger.info("S3 읽기: s3://%s/%s", bucket, key)

    response = s3.get_object(Bucket=bucket, Key=key)
    content = response["Body"].read().decode("utf-8")
    data = json.loads(content)

    content_size = len(content)
    logger.info("S3 읽기 완료: %d bytes", content_size)
    return data


# =============================================================================
# 액션 그룹 핸들러
# =============================================================================

def _handle_get_contract(
    action_group: str,
    api_path: str,
    parameters: list[dict],
) -> dict[str, Any]:
    """
    /get-contract 액션 핸들러.
    S3에서 sites/{site_id}/contract_final.json 을 읽어 반환.
    """
    site_id = _extract_param(parameters, "site_id")
    if not site_id:
        return _error_response(
            action_group, api_path, 400,
            "MISSING_PARAMETER",
            "필수 파라미터 'site_id' 가 없습니다.",
        )

    # 입력값 검증 (경로 순회 방지)
    site_id = site_id.strip()
    if not site_id or "/" in site_id or ".." in site_id:
        return _error_response(
            action_group, api_path, 400,
            "INVALID_SITE_ID",
            f"유효하지 않은 site_id: {site_id!r}",
        )

    s3_key = f"sites/{site_id}/contract_final.json"

    try:
        contract = _read_s3_json(ARTIFACTS_BUCKET, s3_key)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in ("NoSuchKey", "404"):
            return _error_response(
                action_group, api_path, 404,
                "CONTRACT_NOT_FOUND",
                f"site_id={site_id} 의 contract_final.json 을 찾을 수 없습니다. "
                f"S3 키: {s3_key}",
            )
        logger.exception("S3 ClientError: %s", exc)
        return _error_response(
            action_group, api_path, 500,
            "S3_ACCESS_ERROR",
            f"S3 접근 오류: {error_code}",
        )
    except json.JSONDecodeError as exc:
        logger.exception("JSON 파싱 오류: %s", exc)
        return _error_response(
            action_group, api_path, 500,
            "JSON_PARSE_ERROR",
            f"contract_final.json JSON 파싱 실패: {exc}",
        )

    logger.info("contract_final.json 로드 성공 - site_id=%s, keys=%s", site_id, list(contract.keys()))

    return _bedrock_response(
        action_group=action_group,
        api_path=api_path,
        http_status=200,
        body={
            "site_id": site_id,
            "s3_key": s3_key,
            "contract": contract,
        },
    )


def _handle_get_crawl_snapshot(
    action_group: str,
    api_path: str,
    parameters: list[dict],
) -> dict[str, Any]:
    """
    /get-crawl-snapshot 액션 핸들러.
    S3에서 sites/{site_id}/crawl_snapshot.json 을 읽어 반환 (선택적).
    파일이 없으면 404가 아니라 빈 스냅샷을 반환 (optional 데이터이므로).
    """
    site_id = _extract_param(parameters, "site_id")
    if not site_id:
        return _error_response(
            action_group, api_path, 400,
            "MISSING_PARAMETER",
            "필수 파라미터 'site_id' 가 없습니다.",
        )

    site_id = site_id.strip()
    if not site_id or "/" in site_id or ".." in site_id:
        return _error_response(
            action_group, api_path, 400,
            "INVALID_SITE_ID",
            f"유효하지 않은 site_id: {site_id!r}",
        )

    s3_key = f"sites/{site_id}/crawl_snapshot.json"

    try:
        snapshot = _read_s3_json(ARTIFACTS_BUCKET, s3_key)
        logger.info("crawl_snapshot.json 로드 성공 - site_id=%s", site_id)

        return _bedrock_response(
            action_group=action_group,
            api_path=api_path,
            http_status=200,
            body={
                "site_id": site_id,
                "s3_key": s3_key,
                "snapshot_available": True,
                "snapshot": snapshot,
            },
        )

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        if error_code in ("NoSuchKey", "404"):
            # 크롤 스냅샷은 선택적 - 없어도 정상 응답
            logger.info("crawl_snapshot.json 없음 (선택적 파일) - site_id=%s", site_id)
            return _bedrock_response(
                action_group=action_group,
                api_path=api_path,
                http_status=200,
                body={
                    "site_id": site_id,
                    "s3_key": s3_key,
                    "snapshot_available": False,
                    "snapshot": None,
                    "note": "크롤 스냅샷이 없습니다. contract_final.json 데이터만으로 render_spec을 생성하세요.",
                },
            )
        logger.exception("S3 ClientError (crawl_snapshot): %s", exc)
        return _error_response(
            action_group, api_path, 500,
            "S3_ACCESS_ERROR",
            f"S3 접근 오류: {error_code}",
        )
    except json.JSONDecodeError as exc:
        logger.exception("crawl_snapshot.json JSON 파싱 오류: %s", exc)
        return _error_response(
            action_group, api_path, 500,
            "JSON_PARSE_ERROR",
            f"crawl_snapshot.json JSON 파싱 실패: {exc}",
        )


# =============================================================================
# Lambda 핸들러 (진입점)
# =============================================================================

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Bedrock Agent Action Group Lambda 핸들러.

    이벤트 구조 (Bedrock Agent → Lambda):
    {
        "messageVersion": "1.0",
        "agent": { "name": "...", "id": "...", "aliasId": "...", "version": "..." },
        "sessionId": "...",
        "sessionAttributes": { ... },
        "promptSessionAttributes": { ... },
        "inputText": "...",
        "actionGroup": "ContractLoader",
        "apiPath": "/get-contract",
        "httpMethod": "GET",
        "parameters": [{"name": "site_id", "type": "string", "value": "..."}],
        "requestBody": { ... }
    }
    """
    logger.info("ContractLoader 호출 이벤트: %s", json.dumps(event, ensure_ascii=False, default=str))

    action_group = event.get("actionGroup", "ContractLoader")
    api_path     = event.get("apiPath", "")
    parameters   = event.get("parameters", [])

    # apiPath 기반 라우팅
    if api_path == "/get-contract":
        return _handle_get_contract(action_group, api_path, parameters)

    elif api_path == "/get-crawl-snapshot":
        return _handle_get_crawl_snapshot(action_group, api_path, parameters)

    else:
        logger.warning("알 수 없는 apiPath: %s", api_path)
        return _error_response(
            action_group, api_path, 404,
            "UNKNOWN_API_PATH",
            f"지원하지 않는 API 경로: {api_path}. 지원 경로: /get-contract, /get-crawl-snapshot",
        )
