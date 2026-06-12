# 브랜치 전략

## 기본 브랜치

- `main`: 항상 안정적인 기준 브랜치입니다.
- 직접 push하지 않고 PR을 통해 병합합니다.

## 작업 브랜치 규칙

```text
<type>/<issue-number>-<description>
```

예시:

```text
docs/1-initial-repo-rules
feature/12-contract-schema
chore/18-github-template-setup
fix/24-preview-payload-validation
```

## type 목록

| type | 용도 |
|---|---|
| `feature` | 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서 작성/수정 |
| `chore` | 설정, 템플릿, 유지보수 |
| `refactor` | 동작 변경 없는 구조 개선 |
| `test` | 테스트 추가/수정 |
| `infra` | 인프라/배포 관련 작업 |

## 작업 흐름

1. GitHub Issue 생성
2. 이슈 번호 기준 브랜치 생성
3. 작업 및 검증
4. PR 생성
5. 리뷰 반영
6. main 병합

## 병합 전 확인

- PR 제목 컨벤션 준수
- 관련 이슈 연결
- 변경 범위가 하나의 작업 단위인지 확인
- 문서/schema/fixture 영향 확인
- 테스트 또는 검증 결과 작성

