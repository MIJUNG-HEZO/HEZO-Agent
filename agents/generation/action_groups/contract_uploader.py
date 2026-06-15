"""
Lambda 함수: hezo-p4-upload-contract
Step Functions 직접 Lambda Task (Bedrock Action Group 아님)

역할:
- Step Functions 파이프라인의 첫 번째 단계
- FastAPI 백엔드로부터 전달받은 contract_json을 S3에 업로드
- sites/{site_id}/contract_final.json 경로에 저장

입력 (Step Functions Payload):
{
    "site_id": "abc123",
    "contract_json": { ... }  // 전체 Contract JSON 오브젝트
}

출력:
{
    "status": "uploaded",
    "s3_key": "sites/abc123/contract_final.json",
    "s3_bucket": "hezo-artifacts",
    "size_bytes": 1234,
    "uploaded_at": "2026-06-15T00:00:00+00:00"
}
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from libs.telemetry import init_telemetry

# ─── 로거 설정 ──────────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ─── 텔레메트리 초기화 (Lambda 콜드스타트 시 1회) ────────────────────────────
init_telemetry("generation", otlp=False)

# ─── AWS 클라이언트 ──────────────────────────────────────────────────────────
_s3_client = None


def _get_s3() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3", region_name=os.environ.get("AWS_REGION", "ap-northeast-2"))
    return _s3_client


# ─── 환경변수 ────────────────────────────────────────────────────────────────
ARTIFACTS_BUCKET = os.environ.get("ARTIFACTS_BUCKET", "hezo-artifacts")


# =============================================================================
# 입력 유효성 검증
# =============================================================================

def _validate_input(event: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """
    이벤트에서 site_id와 contract_json을 추출하고 유효성 검증.

    Returns:
        (site_id, contract_json) 튜플

    Raises:
        ValueError: 필수 필드 누락 또는 유효하지 않은 값
    """
    site_id = event.get("site_id")
    if not site_id:
        raise ValueError("필수 필드 'site_id' 가 없습니다.")

    site_id = str(site_id).strip()
    if not site_id:
        raise ValueError("'site_id' 가 비어 있습니다.")
    if "/" in site_id or ".." in site_id:
        raise ValueError(f"유효하지 않은 site_id (경로 순회 방지): {site_id!r}")
    if len(site_id) > 128:
        raise ValueError(f"site_id가 너무 깁니다 (최대 128자): {len(site_id)}")

    contract_json = event.get("contract_json")
    if contract_json is None:
        raise ValueError("필수 필드 'contract_json' 가 없습니다.")
    if not isinstance(contract_json, dict):
        raise ValueError(f"'contract_json' 은 JSON 오브젝트여야 합니다. 받은 타입: {type(contract_json).__name__}")
    if not contract_json:
        raise ValueError("'contract_json' 이 빈 오브젝트입니다.")

    # G slot-based 0.1.0 필수 필드 확인 (경고 수준 - 저장은 계속)
    expected_fields = ["schema_version", "ids", "template", "slots", "gates"]
    missing_fields = [f for f in expected_fields if f not in contract_json]
    if missing_fields:
        logger.warning("contract_json에 G slot-based 필드 없음 (저장 계속): %s", missing_fields)

    schema_ver = contract_json.get("schema_version", "unknown")
    if schema_ver != "0.1.0":
        logger.warning("예상치 못한 schema_version: %s (기대값: 0.1.0)", schema_ver)

    # contract_json.ids.site_id와 파라미터 site_id 일관성 확인
    json_site_id = (contract_json.get("ids") or {}).get("site_id")
    if json_site_id and str(json_site_id) != site_id:
        logger.warning(
            "site_id 불일치 - 파라미터: %s, contract_json.site_id: %s. 파라미터 값 사용.",
            site_id, json_site_id,
        )

    return site_id, contract_json


# =============================================================================
# S3 업로드
# =============================================================================

def _upload_contract_to_s3(
    site_id: str,
    contract_json: dict[str, Any],
) -> tuple[str, int]:
    """
    contract_json을 S3에 업로드.

    Returns:
        (s3_key, size_bytes) 튜플

    Raises:
        ClientError: S3 접근 오류
    """
    s3_key = f"sites/{site_id}/contract_final.json"

    # 업로드 타임스탬프 메타데이터 추가 (원본 데이터 수정 없이 복사본에 추가)
    payload = {
        **contract_json,
        "_uploaded_at": datetime.now(timezone.utc).isoformat(),
        "_uploaded_by": "hezo-p4-upload-contract",
    }

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    body_bytes = serialized.encode("utf-8")
    size_bytes = len(body_bytes)

    logger.info(
        "S3 업로드 시작: s3://%s/%s (%d bytes)",
        ARTIFACTS_BUCKET, s3_key, size_bytes,
    )

    _get_s3().put_object(
        Bucket=ARTIFACTS_BUCKET,
        Key=s3_key,
        Body=body_bytes,
        ContentType="application/json; charset=utf-8",
        Metadata={
            "site-id": site_id,
            "uploaded-by": "hezo-p4-upload-contract",
            "schema-version": str(contract_json.get("schema_version", "unknown")),
            "template-category": str((contract_json.get("template") or {}).get("category", "unknown")),
        },
    )

    logger.info("S3 업로드 완료: %s", s3_key)
    return s3_key, size_bytes


# =============================================================================
# Lambda 핸들러 (진입점)
# =============================================================================

def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Step Functions Task Lambda 핸들러 - contract_uploader.

    Step Functions에서 직접 호출되는 Lambda (Bedrock Action Group 아님).
    성공 시 Step Functions ResultSelector가 사용할 dict 반환.
    실패 시 예외 raise → Step Functions Catch로 전달.
    """
    logger.info(
        "contract_uploader 호출 - site_id=%s",
        event.get("site_id", "<unknown>"),
    )

    # ── 입력 유효성 검증 ────────────────────────────────────────────────────
    try:
        site_id, contract_json = _validate_input(event)
    except ValueError as exc:
        logger.error("입력 유효성 검증 실패: %s", exc)
        raise ValueError(f"입력 유효성 오류: {exc}") from exc

    # ── S3 업로드 ────────────────────────────────────────────────────────────
    try:
        s3_key, size_bytes = _upload_contract_to_s3(site_id, contract_json)
    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]
        logger.exception("S3 업로드 실패 - 오류 코드: %s", error_code)
        raise RuntimeError(f"S3 업로드 실패 [{error_code}]: {exc}") from exc

    uploaded_at = datetime.now(timezone.utc).isoformat()

    logger.info(
        "contract_uploader 완료 - site_id=%s, s3_key=%s, size=%d bytes",
        site_id, s3_key, size_bytes,
    )

    return {
        "status": "uploaded",
        "site_id": site_id,
        "s3_key": s3_key,
        "s3_bucket": ARTIFACTS_BUCKET,
        "size_bytes": size_bytes,
        "uploaded_at": uploaded_at,
    }
