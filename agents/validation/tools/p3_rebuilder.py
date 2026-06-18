"""
검증 에이전트가 P3 빌드 워커를 직접 재트리거하는 도구.
ECS RunTask → 완료까지 폴링 (최대 10분).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
ECS_CLUSTER = os.environ.get("ECS_CLUSTER", "hezo-cluster")
TASK_DEFINITION = os.environ.get("BUILD_TASK_DEFINITION", "hezo-build-worker")
SUBNETS = os.environ.get("ECS_SUBNETS", "").split(",")
SECURITY_GROUPS = os.environ.get("ECS_SECURITY_GROUPS", "").split(",")

_POLL_INTERVAL = 10   # 초
_MAX_WAIT = 600       # 최대 10분

_ecs: Any = None


def _get_ecs():
    global _ecs
    if _ecs is None:
        _ecs = boto3.client("ecs", region_name=REGION)
    return _ecs


def trigger_and_wait(site_id: str, mode: str = "publish") -> bool:
    """
    P3 빌드 워커 ECS Task를 시작하고 완료까지 대기.
    반환: True = 성공(STOPPED + exitCode 0), False = 실패
    """
    ecs = _get_ecs()

    subnets = [s for s in SUBNETS if s.strip()]
    sg = [s for s in SECURITY_GROUPS if s.strip()]

    try:
        resp = ecs.run_task(
            cluster=ECS_CLUSTER,
            taskDefinition=TASK_DEFINITION,
            launchType="FARGATE",
            overrides={
                "containerOverrides": [
                    {
                        "name": "build-worker",
                        "environment": [
                            {"name": "SITE_ID", "value": site_id},
                            {"name": "MODE", "value": mode},
                        ],
                    }
                ]
            },
            networkConfiguration={
                "awsvpcConfiguration": {
                    "subnets": subnets or ["subnet-00000000"],
                    "securityGroups": sg or [],
                    "assignPublicIp": "DISABLED",
                }
            },
        )
    except ClientError as exc:
        logger.error("ECS RunTask 실패: %s", exc)
        return False

    failures = resp.get("failures", [])
    if failures:
        logger.error("ECS RunTask 실패 상세: %s", failures)
        return False

    tasks = resp.get("tasks", [])
    if not tasks:
        logger.error("ECS RunTask: task 목록 비어 있음")
        return False

    task_arn = tasks[0]["taskArn"]
    logger.info("P3 ECS Task 시작: %s (site_id=%s, mode=%s)", task_arn, site_id, mode)

    # 완료까지 폴링
    elapsed = 0
    while elapsed < _MAX_WAIT:
        time.sleep(_POLL_INTERVAL)
        elapsed += _POLL_INTERVAL

        try:
            desc = ecs.describe_tasks(cluster=ECS_CLUSTER, tasks=[task_arn])
        except ClientError as exc:
            logger.warning("describe_tasks 실패: %s", exc)
            continue

        task_list = desc.get("tasks", [])
        if not task_list:
            continue

        status = task_list[0].get("lastStatus", "")
        logger.info("ECS Task 상태: %s (elapsed=%ds)", status, elapsed)

        if status == "STOPPED":
            containers = task_list[0].get("containers", [])
            exit_code = containers[0].get("exitCode", -1) if containers else -1
            stop_reason = task_list[0].get("stoppedReason", "")

            if exit_code == 0:
                logger.info("P3 재빌드 완료: task=%s", task_arn)
                return True
            else:
                logger.error("P3 재빌드 실패: exitCode=%s reason=%s", exit_code, stop_reason)
                return False

    logger.error("P3 재빌드 타임아웃 (%ds 초과): task=%s", _MAX_WAIT, task_arn)
    return False
