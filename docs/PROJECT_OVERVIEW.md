# 프로젝트 개요

HEZO-Agent는 AI 홈페이지 빌더의 Agent 계층을 담당합니다.

## 한 줄 정의

사용자와 대화하여 요구사항을 수집하고, 이를 Contract JSON으로 확정한 뒤 프리뷰/생성/검증/리포트 단계로 넘기는 Agent 시스템입니다.

## 전체 단계

| 단계 | 범위 | 성격 | 핵심 산출물 |
|---|---|---|---|
| Stage 1 | 챗봇, 요구사항 수집, Contract JSON, 프리뷰 | 실시간/대화형 | Contract JSON, preview payload |
| Stage 2 | 생성, 빌드, 검증, 리포트, 배포 | 비동기/파이프라인 | render payload, artifacts, validation result, report |

## 핵심 원칙

- Contract JSON은 모든 후속 단계의 단일 진실 공급원입니다.
- 사용자 대화 UX를 막는 작업은 비동기로 분리합니다.
- Agent는 판단과 계획을 담당하고, Worker는 결정적 실행을 담당합니다.
- LLM Wiki, schema, fixture를 먼저 정리한 뒤 구현을 시작합니다.
- MVP는 고정 contract 기반 안정성을 우선합니다.

## 주요 컴포넌트 후보

- LangGraph: 대화형 Agent orchestration
- Amazon Bedrock AgentCore Runtime: Agent 실행 런타임
- SQS/EventBridge/Worker: 비동기 작업
- Airflow 또는 Step Functions: 생성 이후 파이프라인 orchestration
- S3/DynamoDB/RDS: 상태 및 산출물 저장
- OpenTelemetry: 관측성

## MVP 초점

- 대화 기반 필수 슬롯 수집
- Contract JSON schema 고정
- preview-ready / generation-ready 조건 정의
- 프리뷰 입력 payload 생성
- 생성/검증/리포트 인터페이스 고정

