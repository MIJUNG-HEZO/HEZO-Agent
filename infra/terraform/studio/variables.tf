# ── 기존 VPC 리소스 (변경 금지) ─────────────────────────────────────────────
variable "vpc_id" {
  description = "exam-vpc ID"
  default     = "vpc-00eb14011f2d736d5"
}

variable "public_subnet_a" {
  description = "Public Subnet AZ-a (NAT GW 위치)"
  default     = "subnet-0a3b49fab26108dfa"
}

variable "public_subnet_c" {
  description = "Public Subnet AZ-c"
  default     = "subnet-0aaf129a34bcb0a6b"
}

variable "private_web_subnet_a" {
  description = "Private-Web AZ-a (Internal ALB 위치)"
  default     = "subnet-002d6dbec4b8049c5"
}

variable "private_web_subnet_c" {
  description = "Private-Web AZ-c (Internal ALB 위치)"
  default     = "subnet-053fa81468110d100"
}

variable "private_was_subnet_a" {
  description = "Private-WAS AZ-a (ECS 태스크 위치)"
  default     = "subnet-0aae483f673958617"
}

variable "private_was_subnet_c" {
  description = "Private-WAS AZ-c (ECS 태스크 위치)"
  default     = "subnet-0e4f3112fb346bf99"
}

variable "private_db_subnet_a" {
  description = "Private-DB AZ-a (RDS 위치)"
  default     = "subnet-04c97b3f040a9c82d"
}

variable "private_db_subnet_c" {
  description = "Private-DB AZ-c (RDS 위치)"
  default     = "subnet-0fc21a733992d249d"
}

variable "cf_prefix_list_id" {
  description = "CloudFront managed prefix list (ap-northeast-2 전용)"
  default     = "pl-22a6434b"
}

# ── RDS ──────────────────────────────────────────────────────────────────────
variable "db_password" {
  description = "RDS PostgreSQL 비밀번호 (terraform apply 시 입력 또는 TF_VAR_db_password 환경변수)"
  type        = string
  sensitive   = true
}

variable "db_name" {
  description = "RDS 데이터베이스 이름"
  default     = "hezo_prod"
}

variable "db_username" {
  description = "RDS 마스터 유저"
  default     = "hezo"
}

# ── Feature Flags ─────────────────────────────────────────────────────────────
variable "enable_bedrock_agentcore_endpoint" {
  description = "Bedrock Agent Runtime VPC Endpoint 활성화 (AgentCore /invocations 경로)"
  type        = bool
  default     = true
}
