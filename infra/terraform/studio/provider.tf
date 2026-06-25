terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.50"
    }
  }

  # 추후 S3 backend 전환 시:
  # backend "s3" {
  #   bucket         = "hezo-terraform-state"
  #   key            = "studio/terraform.tfstate"
  #   region         = "ap-northeast-2"
  #   dynamodb_table = "hezo-terraform-locks"
  # }
}

provider "aws" {
  region = "ap-northeast-2"
}
