# HEZO-Agent

HEZO-Agent는 HEZO의 AI 홈페이지 빌더에서 대화형 요구사항 수집, Contract JSON 생성, 프리뷰 입력 생성, 생성/검증/리포트 에이전트 흐름을 담당하는 Agent 레포입니다.

현재 단계에서는 코드 스켈레톤을 만들기 전, 팀 협업 규칙과 문서 구조를 먼저 고정합니다.

## 현재 세팅 범위

- PR/Issue 템플릿
- 브랜치 전략
- 커밋/PR 컨벤션
- 레포 문서 구조
- 기획서 기반 Agent 범위 정리
- `.gitignore`

## 개발 원칙

- 먼저 Contract JSON과 인터페이스를 합의합니다.
- Agent 판단 계층과 Worker 실행 계층을 분리합니다.
- 사용자 응답 경로를 막는 작업은 비동기로 분리합니다.
- 프리뷰 승인 전후를 Stage 1 / Stage 2로 분리합니다.
- 민감 정보와 실서비스 credential은 커밋하지 않습니다.

## 주요 문서

- [프로젝트 개요](docs/PROJECT_OVERVIEW.md)
- [레포 구조](docs/REPO_STRUCTURE.md)
- [브랜치 전략](docs/BRANCH_STRATEGY.md)
- [커밋 컨벤션](docs/COMMIT_CONVENTION.md)
- [PR 컨벤션](docs/PR_CONVENTION.md)
- [개발 규칙](docs/DEVELOPMENT_RULES.md)
- [Day 1 체크리스트](docs/DAY1_CHECKLIST.md)

