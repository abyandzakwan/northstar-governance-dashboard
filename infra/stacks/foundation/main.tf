# Initial account enrollment: run once with an existing authorized AWS bootstrap identity.
# Keep the initial local state encrypted, then migrate it into this bucket under foundation/<stage>.tfstate.
terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.100" }
  }
}
variable "stage" { type = string }
variable "account_id" { type = string }
variable "region" { type = string }
variable "repository" { type = string }
variable "release_slack_secret_arn" {
  type    = string
  default = ""
}
variable "release_slack_kms_arn" {
  type    = string
  default = ""
}
variable "existing_github_provider_arn" {
  type    = string
  default = ""
}
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
  default_tags {
    tags = { Service = "gov-portal", Stage = var.stage, Owner = "platform", ManagedBy = "terraform" }
  }
}
locals {
  name    = "gov-portal-${var.stage}"
  buckets = { state = "${local.name}-state-${var.account_id}", artifacts = "${local.name}-artifacts-${var.account_id}" }
}
resource "aws_kms_key" "foundation" {
  description             = "Governance Terraform state and immutable deployment artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_s3_bucket" "foundation" {
  for_each = local.buckets
  bucket   = each.value
  lifecycle {
    prevent_destroy = true
  }
}
resource "aws_s3_bucket_public_access_block" "foundation" {
  for_each                = aws_s3_bucket.foundation
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "foundation" {
  for_each = aws_s3_bucket.foundation
  bucket   = each.value.id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "foundation" {
  for_each = aws_s3_bucket.foundation
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.foundation.arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_s3_bucket_policy" "foundation" {
  for_each = aws_s3_bucket.foundation
  bucket   = each.value.id
  policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [each.value.arn, "${each.value.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }] })
}
module "github_oidc" {
  source                   = "../../modules/github-oidc"
  name                     = local.name
  repository               = var.repository
  stage                    = var.stage
  account_id               = var.account_id
  region                   = var.region
  state_bucket             = aws_s3_bucket.foundation["state"].id
  artifact_bucket          = aws_s3_bucket.foundation["artifacts"].id
  artifact_kms_arn         = aws_kms_key.foundation.arn
  create_provider          = var.existing_github_provider_arn == ""
  provider_arn             = var.existing_github_provider_arn
  release_slack_secret_arn = var.release_slack_secret_arn
  release_slack_kms_arn    = var.release_slack_kms_arn
}
resource "aws_iam_role" "org" {
  for_each           = contains(["central", "prod"], var.stage) ? { plan = "org-bootstrap-plan", apply = "org-bootstrap" } : {}
  name               = "gov-portal-github-org-${each.key}"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Federated = module.github_oidc.provider_arn }, Action = "sts:AssumeRoleWithWebIdentity", Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = "repo:${var.repository}:environment:${each.value}" } } }] })
}
resource "aws_iam_role_policy_attachment" "org_read" {
  for_each   = aws_iam_role.org
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
resource "aws_iam_role_policy" "org" {
  for_each = aws_iam_role.org
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [for scope in ["prod", "sandbox"] : "${aws_s3_bucket.foundation["state"].arn}/org-bootstrap/${scope}.tfstate"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = [for scope in ["prod", "sandbox"] : "${aws_s3_bucket.foundation["state"].arn}/org-bootstrap/${scope}.tfstate.tflock"] },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = aws_kms_key.foundation.arn }
    ], [for statement in [
      { Effect = "Allow", Action = ["s3:PutObject"], Resource = [for scope in ["prod", "sandbox"] : "${aws_s3_bucket.foundation["state"].arn}/org-bootstrap/${scope}.tfstate"] },
      { Effect = "Allow", Action = ["cloudformation:CreateStackSet", "cloudformation:UpdateStackSet", "cloudformation:DeleteStackSet", "cloudformation:CreateStackInstances", "cloudformation:UpdateStackInstances", "cloudformation:DeleteStackInstances", "cloudformation:TagResource", "cloudformation:UntagResource"], Resource = ["arn:aws:cloudformation:*:${var.account_id}:stackset/gov-portal-*", "arn:aws:cloudformation:*:${var.account_id}:stackset-target/*"] },
      { Effect = "Allow", Action = "iam:CreateServiceLinkedRole", Resource = "*", Condition = { StringEquals = { "iam:AWSServiceName" = "member.org.stacksets.cloudformation.amazonaws.com" } } }
  ] : statement if each.key == "apply"]) })
}
output "state_bucket" { value = aws_s3_bucket.foundation["state"].id }
output "artifact_bucket" { value = aws_s3_bucket.foundation["artifacts"].id }
output "plan_role_arn" { value = module.github_oidc.plan_role_arn }
output "deploy_role_arn" { value = module.github_oidc.deploy_role_arn }
output "org_roles" { value = { for k, r in aws_iam_role.org : k => r.arn } }
