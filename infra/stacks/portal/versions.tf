terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source = "hashicorp/aws", version = "~> 5.100"
    }
  }

  backend "s3" {
    use_lockfile = true
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      Service = "gov-portal", Stage = var.stage, Owner = var.owner, ManagedBy = "terraform"
    }
  }
}

provider "aws" {
  alias               = "us_east_1"
  region              = "us-east-1"
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = {
      Service = "gov-portal", Stage = var.stage, Owner = var.owner, ManagedBy = "terraform"
    }
  }
}

