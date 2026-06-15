"""
Lambda 함수: hezo-p4-render-spec-saver
Bedrock Agent Action Group - RenderSpecSaver

역할:
- Generation Agent가 생성한 render_spec (dict)을 S3에 저장
- sites/{site_id}/render_spec.json 경로에 저장
- CloudWatch 메트릭 기록 (P5 텔레메트리)
- 저장 완료 후 s3_key와 status를 에이전트에 반환

입력 (Bedrock Agent Action Group 이벤트 형식):
  - apiPath: "/save-render-spec"
  - parameters: [{"name": "site_id", ...}, {"name": "render_spec_json", ...}]
  또는 requestBody를 통해 render_spec 전달 가능
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

# ─── 로거 설정 ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─── AWS 클라이언트 (Lambda 실행 환경에서 재사용) ────────────────────────────
_s3_client = None
_cw_client = None


def _get_s3() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _s3_client


def _get_cloudwatch() -> Any:
    global _cw_client
    if _cw_client is None:
        _cw_client = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _cw_client


# ─── 환경변수 ────────────────────────────────────────────────────────────────
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "hezo-artifacts")


# =============================================================================
# P5 텔레메트리 (2-line usage pattern)
# =============================================================================

def _emit_metric(metric_name: str, value: float = 1.0, unit: str = "Count", dimensions: list[dict] | None = None) -> None:
    """CloudWatch 메트릭 발행 (fire-and-forget, 실패해도 예외 미전파)"""
    from agents.shared.telemetry import record_metric  # 공유 텔레메트리 모듈
    record_metric("hezo/agent", metric_name, value, unit, dimensions or [])


# =============================================================================
# Bedrock Agent 응답 포맷 헬퍼
# =============================================================================

def _bedrock_response(
    action_group: str,
    api_path: str,
    http_status: int,
    body: dict[str, Any],
    http_method: str = "POST",
) -> dict[str, Any]:
    """Bedrock Agent Action Group 표준 응답 형식"""
    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "apiPath": api_path,
            "httpMethod": http_method,
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
    logger.error("RenderSpecSaver 오류 [%s %d]: %s - %s", api_path, http_status, error_code, message)
    return _bedrock_response(action_group, api_path, http_status, {"error": error_code, "message": message})


# =============================================================================
# 파라미터 추출 헬퍼
# =============================================================================

def _extract_param(parameters: list[dict], name: str) -> str | None:
    for param in parameters:
        if param.get("name") == name:
            return param.get("value")
    return None


def _extract_render_spec(event: dict[str, Any], parameters: list[dict]) -> dict[str, Any] | None:
    """
    render_spec을 이벤트에서 추출.
    우선순위: requestBody > parameters.render_spec_json
    """
    # 1) requestBody에서 추출 (Bedrock Agent가 구조화된 body를 전달하는 경우)
    request_body = event.get("requestBody", {})
    if request_body:
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        properties = json_content.get("properties", [])
        for prop in properties:
            if prop.get("name") == "render_spec":
                raw = prop.get("value", "")
                if isinstance(raw, dict):
                    return raw
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    pass

    # 2) parameters에서 render_spec_json 문자열로 전달된 경우
    render_spec_raw = _extract_param(parameters, "render_spec_json")
    if render_spec_raw:
        if isinstance(render_spec_raw, dict):
            return render_spec_raw
        try:
            return json.loads(render_spec_raw)
        except json.JSONDecodeError as exc:
            logger.error("render_spec_json JSON 파싱 실패: %s", exc)
            return None

    return None


# =============================================================================
# 메인 핸들러
# =============================================================================

def _handle_save_render_spec(
    action_group: str,
    api_path: str,
    parameters: list[dict],
    event: dict[str, Any],
) -> dict[str, Any]:
    """
    /save-render-spec 핸들러.
    render_spec을 S3에 저장하고 CloudWatch 메트릭을 기록.
    """
    # site_id 추출
    site_id = _extract_param(parameters, "site_id")
    if not site_id:
        return _error_response(action_group, api_path, 400, "MISSING_PARAMETER", "필수 파라미터 'site_id' 가 없습니다.")

    site_id = site_id.strip()
    if not site_id or "/" in site_id or ".." in site_id:
        return _error_response(action_group, api_path, 400, "INVALID_SITE_ID", f"유효하지 않은 site_id: {site_id!r}")

    # render_spec 추출
    render_spec = _extract_render_spec(event, parameters)
    if render_spec is None:
        return _error_response(
            action_group, api_path, 400,
            "MISSING_RENDER_SPEC",
            "render_spec 데이터가 없습니다. requestBody 또는 parameters.render_spec_json 를 확인하세요.",
        )

    if not isinstance(render_spec, dict):
        return _error_response(action_group, api_path, 400, "INVALID_RENDER_SPEC", "render_spec은 JSON 오브젝트여야 합니다.")

    # render_spec 기본 필드 검증
    required_fields = ["site_id", "pages"]
    missing = [f for f in required_fields if f not in render_spec]
    if missing:
        logger.warning("render_spec 에 필수 필드 누락: %s (저장은 계속 진행)", missing)

    # site_id 일관성 확인 (render_spec.site_id와 파라미터 site_id가 다른 경우 경고)
    spec_site_id = render_spec.get("site_id")
    if spec_site_id and spec_site_id != site_id:
        logger.warning(
            "site_id 불일치: 파라미터=%s, render_spec.site_id=%s. 파라미터 값 사용.",
            site_id, spec_site_id,
        )
        render_spec["site_id"] = site_id

    # 메타데이터 추가
    render_spec["_saved_at"] = datetime.now(timezone.utc).isoformat()
    render_spec["_schema_version"] = render_spec.get("schema_version", "1.0")

    # S3 저장
    s3_key = f"sites/{site_id}/render_spec.json"
    serialized = json.dumps(render_spec, ensure_ascii=False, indent=2)
    byte_size = len(serialized.encode("utf-8"))

    s3 = _get_s3()
    try:
        logger.info("render_spec.json S3 저장 시작: s3://%s/%s (%d bytes)", ARTIFACTS_BUCKET, s3_key, byte_size)
        start_ts = time.monotonic()

        s3.put_object(
            Bucket=ARTIFACTS_BUCKET,
            Key=s3_key,
            Body=serialized.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
            Metadata={
                "site-id": site_id,
                "schema-version": str(render_spec.get("schema_version", "1.0")),
                "saved-by": "hezo-p4-render-spec-saver",
            },
        )

        elapsed_ms = (time.monotonic() - start_ts) * 1000
        logger.info("render_spec.json 저장 완료: %.1f ms", elapsed_ms)

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.exception("S3 저장 실패: %s", exc)
        return _error_response(action_group, api_path, 500, "S3_WRITE_ERROR", f"S3 저장 실패: {error_code}")

    # P5 텔레메트리: CloudWatch 메트릭 기록 (fire-and-forget)
    try:
        _get_cloudwatch().put_metric_data(
            Namespace="hezo/agent",
            MetricData=[{"MetricName": "render_spec_saved", "Value": 1.0, "Unit": "Count", "Dimensions": [{"Name": "site_id", "Value": site_id}]}],
        )
    except Exception as metric_exc:  # noqa: BLE001
        logger.warning("CloudWatch 메트릭 기록 실패 (무시): %s", metric_exc)

    page_count = len(render_spec.get("pages", []))
    logger.info(
        "render_spec 저장 완료 - site_id=%s, pages=%d, size=%d bytes",
        site_id, page_count, byte_size,
    )

    return _bedrock_response(
        action_group=action_group,
        api_path=api_path,
        http_status=200,
        http_method="POST",
        body={
            "status": "render_spec_saved",
            "site_id": site_id,
            "s3_key": s3_key,
            "s3_bucket": ARTIFACTS_BUCKET,
            "size_bytes": byte_size,
            "page_count": page_count,
            "saved_at": render_spec["_saved_at"],
        },
    )


# =============================================================================
# Lambda 핸들러 (진입점)
# =============================================================================

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Bedrock Agent Action Group Lambda 핸들러 - RenderSpecSaver.

    지원 apiPath:
      POST /save-render-spec  - render_spec.json을 S3에 저장
    """
    logger.info(
        "RenderSpecSaver 호출 - actionGroup=%s, apiPath=%s",
        event.get("actionGroup"),
        event.get("apiPath"),
    )

    action_group = event.get("actionGroup", "RenderSpecSaver")
    api_path     = event.get("apiPath", "")
    parameters   = event.get("parameters", [])

    if api_path == "/save-render-spec":
        return _handle_save_render_spec(action_group, api_path, parameters, event)

    else:
        logger.warning("알 수 없는 apiPath: %s", api_path)
        return _error_response(
            action_group, api_path, 404,
            "UNKNOWN_API_PATH",
            f"지원하지 않는 API 경로: {api_path}. 지원 경로: POST /save-render-spec",
        )
