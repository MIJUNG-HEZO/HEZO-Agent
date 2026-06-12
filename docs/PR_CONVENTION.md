# PR 컨벤션

## PR 제목

```text
<type>(<scope>): <subject>
```

예시:

```text
docs(repo): Agent 레포 초기 문서 세팅
chore(github): PR 및 이슈 템플릿 추가
feature(contract): Contract JSON schema 초안 추가
```

## PR 본문

PR 본문은 `.github/pull_request_template.md`를 사용합니다.

반드시 포함합니다.

- 연결된 이슈
- 작업 목표
- 주요 변경 내용
- 테스트 또는 검증 결과
- 리뷰 요청 사항

## PR 범위

- 하나의 PR은 하나의 작업 단위만 다룹니다.
- schema 변경과 fixture 변경은 같은 PR에 포함할 수 있습니다.
- schema 변경 없이 구현만 바꾸는 PR은 schema 영향이 없음을 명시합니다.
- Agent 로직과 infra 변경은 가능하면 분리합니다.

## 리뷰 중점

- Contract JSON과 인터페이스 호환성
- 동기/비동기 경계
- Agent/Worker 책임 분리
- 비용과 지연 시간 영향
- 실패/재시도/상태 추적
- 테스트 및 fixture 정합성

