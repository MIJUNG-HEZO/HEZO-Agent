# 레포 구조

현재 문서는 코드 스켈레톤 생성 전 기준입니다. 실제 구현 디렉토리는 Contract JSON, 인터페이스, 실행 런타임 결정 이후 생성합니다.

## 현재 구조

```text
HEZO-Agent/
├── .github/
│   ├── ISSUE_TEMPLATE/
│   └── pull_request_template.md
├── docs/
│   ├── PROJECT_OVERVIEW.md
│   ├── REPO_STRUCTURE.md
│   ├── BRANCH_STRATEGY.md
│   ├── COMMIT_CONVENTION.md
│   ├── PR_CONVENTION.md
│   ├── DEVELOPMENT_RULES.md
│   └── DAY1_CHECKLIST.md
├── .gitignore
├── CONTRIBUTING.md
└── README.md
```

## 추후 생성 후보

```text
HEZO-Agent/
├── docs/
│   ├── contracts/
│   ├── interfaces/
│   ├── infra/
│   └── validation/
├── schemas/
│   ├── contract.schema.json
│   ├── preview_payload.schema.json
│   ├── render_payload.schema.json
│   └── validation_result.schema.json
├── fixtures/
│   ├── contract_samples/
│   ├── preview_samples/
│   ├── render_samples/
│   └── validation_samples/
├── apps/
├── services/
└── tests/
```

## 문서 우선순위

1. `docs/contracts/contract_schema.md`
2. `schemas/contract.schema.json`
3. `docs/shared_vocab.md`
4. `docs/interfaces/interface_spec.md`
5. preview/render/validation schema와 fixture
6. infra 범위 문서
7. readiness/validation/artifact manifest 문서

