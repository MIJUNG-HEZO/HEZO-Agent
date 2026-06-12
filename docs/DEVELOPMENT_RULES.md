# 개발 규칙

## 공통 원칙

- Contract JSON을 Source of Truth로 둡니다.
- schema 변경은 문서, fixture, 검증 기준과 함께 다룹니다.
- 사용자 응답 경로를 막는 작업은 동기로 넣지 않습니다.
- 리서치, 생성, 빌드, 검증, 리포트는 비동기 처리 후보로 분리합니다.
- Agent 판단과 Worker 실행 책임을 섞지 않습니다.

## Stage 1 규칙

Stage 1은 챗봇부터 프리뷰까지입니다.

- 슬롯 추출
- 필수 슬롯 검증
- follow-up 질문 생성
- Contract JSON 컴파일
- preview payload 생성
- preview-ready 판정

## Stage 2 규칙

Stage 2는 프리뷰 승인 이후입니다.

- generation-ready 판정
- render payload 생성
- 빌드/배포 worker 호출
- validation result 생성
- report 생성

## 비동기 처리 기준

다음 작업은 동기 응답 경로에서 분리합니다.

- 웹 크롤링/리서치
- 대량 요약/정제
- 최종 생성
- 빌드/배포
- 검증
- 리포트 생성

## 문서 변경 규칙

- 새 용어는 `docs/shared_vocab.md`에 먼저 추가합니다.
- 새 schema는 `/schemas`에 추가하고 `/fixtures` 샘플을 함께 둡니다.
- 인터페이스 변경은 `docs/interfaces/interface_spec.md`에 반영합니다.
- readiness 조건은 별도 문서로 관리합니다.

## 보안 규칙

- `.env`, API key, AWS credential, private key는 커밋하지 않습니다.
- 샘플 값은 반드시 mock 값으로 작성합니다.
- 외부 API 호출 정책과 timeout/retry 정책은 문서화합니다.

