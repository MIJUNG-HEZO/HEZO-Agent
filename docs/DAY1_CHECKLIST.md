# Day 1 체크리스트

기획서와 에이전트 개발 체크리스트 기준으로 코드 작성 전 필요한 산출물을 정리합니다.

## T0. 단계 경계 정렬

- [ ] `docs/stage_boundary.md`
- [ ] Stage 1 정의
- [ ] Stage 2 정의
- [ ] 전환 트리거: 프리뷰 승인 이벤트
- [ ] 책임 경계 정의

## T1. 공통 vocabulary 확정

- [ ] `docs/shared_vocab.md`
- [ ] `template_id` enum
- [ ] `section.type` enum
- [ ] `api_profile` enum
- [ ] `industry` enum
- [ ] `site_goal` enum
- [ ] `validation_status` enum
- [ ] `publish_status` enum

## T2. Contract JSON 스키마 확정

- [ ] `docs/contracts/contract_schema.md`
- [ ] `schemas/contract.schema.json`
- [ ] `fixtures/contract_samples/clinic_sample.json`
- [ ] `missing_items` / `unresolved_items` 구조
- [ ] confidence 표현 방식
- [ ] preview-ready / generation-ready 조건

## T3. 인터페이스 4종 확정

- [ ] `docs/interfaces/interface_spec.md`
- [ ] IF-1 챗봇/Contract -> 프리뷰
- [ ] IF-2 프리뷰 승인 -> 생성
- [ ] IF-3 생성 명세 -> 빌드/배포
- [ ] IF-4 검증 결과 -> 배포 판정

## T4. 현재 단계 인프라 범위 확정

- [ ] `docs/infra/infra_scope_now_vs_later.md`
- [ ] 이번 단계 포함 자원 목록
- [ ] 이번 단계 제외 자원 목록
- [ ] 현재 배포 경로 그림

## T5. 저장소/문서/샘플 구조 확정

- [ ] `docs/repo_structure.md`
- [ ] `/docs`, `/schemas`, `/fixtures` 구조 확정
- [ ] 브랜치 전략
- [ ] PR 규칙
- [ ] 문서 owner 표

## T6. 판정 조건 초안

- [ ] `docs/preview_ready_criteria.md`
- [ ] `docs/generation_ready_criteria.md`
- [ ] `docs/validation/validation_status_spec.md`
- [ ] `docs/artifact_manifest_spec.md`

## T7. 첫 주 작업/owner/일정 확정

- [ ] owner별 산출물 확정
- [ ] 제출일 확정
- [ ] mock fixture 제출일 확정
- [ ] 다음 공동 합의 회의 일정 확정

