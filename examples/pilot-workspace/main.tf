terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.100" }
  }
  backend "s3" { use_lockfile = true }
}
variable "account_id" { type = string }
variable "region" { type = string }
variable "bucket_name" { type = string }
variable "bucket_versioning_enabled" { type = bool }
provider "aws" {
  region              = var.region
  profile             = "governance-target"
  allowed_account_ids = [var.account_id]
}
# Adopt the versioning configuration of an existing, disposable pilot bucket.
# This workspace never creates or destroys the bucket itself.
resource "aws_s3_bucket_versioning" "pilot" {
  bucket                = var.bucket_name
  expected_bucket_owner = var.account_id
  versioning_configuration {
    status = var.bucket_versioning_enabled ? "Enabled" : "Suspended"
  }
  lifecycle { prevent_destroy = true }
}
