"""
P3 빌드 워커 — ECS runTask 진입점

Step Functions ecs:runTask.sync 통합 전용.
환경변수로 파라미터를 수신하고 run() 완료 후 프로세스 종료.

환경변수:
  SITE_ID  (필수) — 빌드 대상 사이트 ID
  MODE     (선택, 기본값 "publish") — preview | publish
"""
from __future__ import annotations

import logging
import os
import sys

from agents.build.build_worker import run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("hezo.build.run_task")


def main() -> None:
    site_id = os.environ.get("SITE_ID", "").strip()
    mode = os.environ.get("MODE", "publish").strip()

    if not site_id:
        logger.error("SITE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    if mode not in ("preview", "publish"):
        logger.error("MODE는 preview 또는 publish 여야 합니다. 받은 값: %s", mode)
        sys.exit(1)

    logger.info("빌드 시작 — site_id=%s mode=%s", site_id, mode)
    result = run(site_id, mode=mode)

    file_count = len(result.get("uploaded_files", []))
    geo_count = len(result.get("geo_files", []))
    logger.info(
        "빌드 완료 — site_id=%s mode=%s files=%d geo=%d",
        site_id, mode, file_count, geo_count,
    )


if __name__ == "__main__":
    main()
