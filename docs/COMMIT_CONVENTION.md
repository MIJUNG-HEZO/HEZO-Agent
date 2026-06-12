# 커밋 컨벤션

## 형식

```text
<type>(<scope>): <subject>
```

예시:

```text
docs(repo): 초기 협업 문서 추가
chore(github): 이슈 및 PR 템플릿 추가
feature(contract): Contract JSON schema 초안 추가
fix(agent): 누락 슬롯 판정 조건 수정
```

## type

| type | 설명 |
|---|---|
| `feature` | 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서 수정 |
| `chore` | 설정/빌드/템플릿 |
| `refactor` | 리팩터링 |
| `test` | 테스트 |
| `infra` | 인프라 |

## scope 후보

- `repo`
- `github`
- `contract`
- `schema`
- `fixture`
- `agent`
- `preview`
- `generation`
- `validation`
- `report`
- `infra`

## 규칙

- subject는 한 줄로 작성합니다.
- 하나의 커밋은 하나의 의도를 갖습니다.
- 민감 정보 제거 커밋은 즉시 알려야 합니다.
- PR squash/merge 전략은 팀 합의에 따릅니다.

