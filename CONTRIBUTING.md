# Contributing

## 기본 흐름

1. 이슈 생성
2. 브랜치 생성
3. 작업
4. 테스트/검증
5. PR 생성
6. 리뷰 반영
7. merge

## 작업 단위

- 하나의 PR은 하나의 작업 단위만 담습니다.
- 기능, 문서, 설정, 리팩터링을 한 PR에 섞지 않습니다.
- Contract/schema/interface 변경은 반드시 문서와 fixture 변경을 함께 검토합니다.

## 리뷰 기준

- Agent 책임 경계가 명확한가?
- Contract JSON과 인터페이스가 깨지지 않는가?
- 동기/비동기 경계가 기획서 원칙과 맞는가?
- 비밀값, credential, 개인 환경 파일이 포함되지 않았는가?
- 테스트 또는 검증 방법이 PR에 명시되었는가?

## 금지 사항

- `.env`, secret, API key, AWS credential 커밋 금지
- 합의되지 않은 schema 필드 추가 금지
- Agent 로직과 infra/worker 실행 책임 혼합 금지
- PR 템플릿 미작성 금지

