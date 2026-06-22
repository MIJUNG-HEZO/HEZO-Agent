# P5: Infrastructure as Code (IaC) Pipeline Architecture

**목표:** P4 검증 완료 후 고객사 클라우드 인프라를 자동으로 프로비저닝하고 도메인을 연결합니다.

---

## 📊 전체 흐름

```
P4 검증 완료 (hezo-site-pipeline)
  │
  └─ site-published 이벤트 발행
     │
     └─ EventBridge Rule (hezo-site-published-to-lambda)
        │
        └─ Step Functions (hezo-iac-pipeline) 자동 시작
           │
           ├─ 1️⃣ CreateCustomerStack
           │  └─ CloudFormation 스택 생성 (또는 업데이트)
           │     • VPC + Subnet + IGW
           │     • EC2 인스턴스 (hezo-customer-backend Docker)
           │     • MariaDB 데이터베이스
           │     • CloudFront Distribution
           │
           ├─ 2️⃣ GetStackOutputs
           │  └─ CloudFront Distribution ID 추출
           │  └─ 도메인 URL 추출
           │
           ├─ 3️⃣ StoreCustomerDomain
           │  └─ DynamoDB pipeline_state에 저장
           │
           ├─ 4️⃣ CloudFrontInvalidation
           │  └─ CloudFront 캐시 무효화
           │
           ├─ 5️⃣ RegisterReportSchedule
           │  └─ EventBridge Scheduler 등록
           │  └─ 7일마다 리포트 자동 생성
           │
           └─ Success!
              ✅ site_tax_13_001.doodo.cloud 완성
```

---

## 🔄 Step-by-Step 설명

### 1️⃣ CreateCustomerStack (AWS SDK Integration)

**역할:** CloudFormation 스택 자동 생성

**AWS SDK Call:**
```
aws-sdk:cloudformation:createStack
```

**입력 파라미터:**
```json
{
  "StackName": "hezo-customer-site_tax_13_001",
  "TemplateURL": "https://hezo-artifacts.s3.ap-northeast-2.amazonaws.com/customer-infra.yaml",
  "Parameters": [
    { "ParameterKey": "SiteId", "ParameterValue": "site_tax_13_001" },
    { "ParameterKey": "DomainName", "ParameterValue": "site-tax-13-001.doodo.cloud" },
    { "ParameterKey": "HostedZoneId", "ParameterValue": "Z10483373699UEVYVQFQS" },
    { "ParameterKey": "WildcardCertArn", "ParameterValue": "arn:aws:acm:us-east-1:..." },
    { "ParameterKey": "EcrImage", "ParameterValue": "492554570964.dkr.ecr.ap-northeast-2.amazonaws.com/hezo-customer-backend:latest" },
    { "ParameterKey": "TemplateType", "ParameterValue": "tax-accounting" },
    { "ParameterKey": "TemplateCategory", "ParameterValue": "landing" }
  ],
  "Capabilities": ["CAPABILITY_NAMED_IAM"],
  "Tags": [
    { "Key": "hezo:site-id", "Value": "site_tax_13_001" },
    { "Key": "hezo:managed-by", "Value": "step-functions-iac" }
  ]
}
```

**생성되는 리소스:**
| 리소스 | 설명 |
|---|---|
| **VPC** | CIDR: 10.100.0.0/16 (고객 전용) |
| **EC2 인스턴스** | t3.micro · Amazon Linux 2023 · hezo-customer-backend Docker 실행 |
| **MariaDB** | EC2 내장 · 고객 DB 격리 |
| **CloudFront Distribution** | 정적 파일 배포 · 기본 도메인 (d1gmrb6iv6h3jg.cloudfront.net) |
| **IAM 역할** | EC2용 · S3 및 ECR 접근 권한 |

**에러 핸들링:**
- **AlreadyExistsException** → UpdateCustomerStack으로 자동 전환 (멱등성 보장)
- 다른 에러 → IaCFailed (SNS 알림)

---

### 2️⃣ GetStackOutputs

**역할:** CloudFormation Output 추출

**추출 항목:**
```
CloudFrontDistributionId: E3E03PEB57T1WW
DomainUrl: https://d1gmrb6iv6h3jg.cloudfront.net
```

**다음 단계로 전달:** `$.stack_outputs`

---

### 3️⃣ StoreCustomerDomain (DynamoDB)

**역할:** 생성된 인프라 정보를 DynamoDB에 저장

**테이블:** `pipeline_state`

**저장 내용:**
| 필드 | 값 |
|---|---|
| `site_id` | site_tax_13_001 |
| `domain_url` | https://d1gmrb6iv6h3jg.cloudfront.net |
| `cf_distribution_id` | E3E03PEB57T1WW |
| `publish_status` | published |

---

### 4️⃣ CloudFrontInvalidation

**역할:** CloudFront 캐시 무효화 (인증서 적용 반영)

**무효화 경로:** `/*` (모든 객체)

**목적:** ACM 인증서 적용 후 캐시 갱신

---

### 5️⃣ RegisterReportSchedule (EventBridge Scheduler)

**역할:** 리포트 자동 생성 스케줄 등록

**생성되는 스케줄:**
```
Name: hezo-report-site_tax_13_001
Schedule: rate(7 days)
Target: hezo-report-pipeline (Step Functions)
Input: {
  "site_id": "site_tax_13_001",
  "domain_url": "https://d1gmrb6iv6h3jg.cloudfront.net"
}
```

**효과:**
- 매주 7일마다 자동 실행
- P4 리포트 에이전트가 AI 인용률 측정
- 리포트 생성 후 S3에 저장

---

## 🎯 도메인 연결 (추후 단계)

**현재:** CloudFormation이 기본 도메인만 생성 (d1gmrb6iv6h3jg.cloudfront.net)

**다음:** Lambda를 통해 고객 도메인 연결
```
Lambda (setup_customer_domain)
  ├─ Route 53: A Record 추가 (site-tax-13-001.doodo.cloud)
  ├─ CloudFront: Aliases 추가
  └─ CloudFront: ACM 인증서 적용
```

---

## ⚙️ 기술 상세

### AWS SDK Integration vs Service Integration

| 항목 | Service Integration | AWS SDK Integration |
|---|---|---|
| 리소스 | `arn:aws:states:::cloudformation:createStack.sync` | `arn:aws:states:::aws-sdk:cloudformation:createStack` |
| 지원 리전 | 특정 리전만 | 모든 리전 |
| ap-northeast-2 | ❌ 미지원 | ✅ 지원 |
| 사용 | 최적화됨 | 범용 |

**P5에서 사용:** AWS SDK Integration (`.sync` 없음)

### 환경 변수 (Step Functions 배포 시)

| 변수 | 값 |
|---|---|
| `${CFN_TEMPLATE_URL}` | S3 CloudFormation 템플릿 URL |
| `${HEZO_HOSTED_ZONE_ID}` | Route 53 Hosted Zone ID |
| `${WILDCARD_CERT_ARN}` | ACM 와일드카드 인증서 ARN |
| `${HEZO_SITES_BUCKET_DOMAIN}` | S3 정적 파일 버킷 |
| `${CUSTOMER_BACKEND_ECR_IMAGE}` | Docker 이미지 URI |
| `${REPORT_STATE_MACHINE_ARN}` | 리포트 Step Functions ARN |
| `${SCHEDULER_ROLE_ARN}` | EventBridge Scheduler 역할 ARN |

---

## 📝 DynamoDB pipeline_state 테이블 구조

**PK:** `site_id` (STRING)

**필드:**
```
{
  "site_id": "site_tax_13_001",
  "publish_status": "published",
  "domain_url": "https://d1gmrb6iv6h3jg.cloudfront.net",
  "cf_distribution_id": "E3E03PEB57T1WW",
  "attempt": 1,
  "created_at": "2026-06-22T03:15:00Z",
  "updated_at": "2026-06-22T03:20:00Z"
}
```

**상태 전이:**
```
draft
  → building (P4: InvokeGenerationAgent)
  → validating (P4: InvokeValidationAgent)
  → published (P4: MarkPublished)
  → published (P5: IaC 완료)
```

---

## 🚨 에러 핸들링

### 레이어별 에러 처리

| 단계 | 에러 | 처리 |
|---|---|---|
| CreateCustomerStack | AlreadyExistsException | UpdateCustomerStack으로 전환 |
| CreateCustomerStack | 기타 | IaCFailed (SNS) |
| GetStackOutputs | 모든 에러 | IaCFailed (SNS) |
| CloudFrontInvalidation | 에러 | 무시하고 계속 진행 |
| RegisterReportSchedule | 에러 | 무시하고 완료 |

### SNS 알림

**주제:** `arn:aws:sns:ap-northeast-2:492554570964:hezo-pipeline-alerts`

**메시지:**
```
IaC pipeline failed for site_id=site_tax_13_001, execution=<execution-id>
```

---

## ✅ 완료 조건

P5 IaC Pipeline이 성공적으로 완료되려면:

1. ✅ CloudFormation 스택 생성/업데이트
2. ✅ CloudFront Distribution ID 추출
3. ✅ DynamoDB에 정보 저장
4. ✅ CloudFront 캐시 무효화
5. ✅ EventBridge Scheduler 등록 (리포트 자동화)

**다음 단계:** Lambda가 도메인 연결 (Route 53 + Aliases)

---

## 📊 성능 및 비용

### 실행 시간
- 평균: 10~15분
- 최악의 경우: 30분 (CloudFormation 생성 시간 포함)

### 비용
| 리소스 | 월 비용 (고객 1명) |
|---|---|
| EC2 t3.micro | ~$7 |
| CloudFront | ~$0.50 |
| Route 53 | ~$0.50 |
| DynamoDB | ~$1 |
| **총계** | ~$9/월 |

---

## 🔗 관련 문서

- **P4 (검증 에이전트):** `P4_PRD_v3.md`
- **CloudFormation 템플릿:** `customer-infra.yaml`
- **Lambda (도메인 연결):** `setup_customer_domain.py`
- **EventBridge Rule:** `hezo-site-published-to-lambda`

---

## 📌 주요 설계 원칙

1. **멱등성:** 같은 요청 재실행 시 동일 결과 (AlreadyExistsException 처리)
2. **자동화:** 사람 개입 없이 전체 프로비저닝 자동화
3. **추적:** DynamoDB에 모든 상태 저장 (감사 추적)
4. **안정성:** 에러 발생 시 SNS 알림 (즉시 대응)
5. **비용 효율:** t3.micro 인스턴스로 최소 비용 유지

---

**배포 날짜:** 2026-06-22  
**상태:** ✅ 활성 배포 (aws-sdk:cloudformation API 사용)
