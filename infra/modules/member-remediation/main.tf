variable "name" { type = string }
variable "organization_id" { type = string }
variable "writer_role_arns" { type = list(string) }
variable "target_ou_ids" { type = list(string) }
variable "region" { type = string }
variable "role_name" { type = string }
# Additional Terraform actions must be reviewed explicitly by the account owner.
variable "extra_policy_arns" { type = list(string) }
resource "aws_cloudformation_stack_set" "role" {
  name             = "${var.name}-remediation-role"
  permission_model = "SERVICE_MANAGED"
  call_as          = "DELEGATED_ADMIN"
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = false
  }
  managed_execution { active = true }
  template_body = jsonencode({ AWSTemplateFormatVersion = "2010-09-09", Resources = {
    RemediationRole = { Type = "AWS::IAM::Role", Properties = {
      RoleName = var.role_name,
      AssumeRolePolicyDocument = { Version = "2012-10-17", Statement = [{
        Effect    = "Allow", Principal = { AWS = distinct([for arn in var.writer_role_arns : "arn:aws:iam::${split(":", arn)[4]}:root"]) }, Action = "sts:AssumeRole",
        Condition = { StringEquals = { "aws:PrincipalOrgID" = var.organization_id }, ArnEquals = { "aws:PrincipalArn" = var.writer_role_arns } }
      }] },
      ManagedPolicyArns = var.extra_policy_arns,
      Policies = [{ PolicyName = "BoundedRemediation", PolicyDocument = { Version = "2012-10-17", Statement = [
        { Effect = "Allow", Action = ["s3:GetAccountPublicAccessBlock", "s3:PutAccountPublicAccessBlock", "ec2:GetEbsEncryptionByDefault", "ec2:EnableEbsEncryptionByDefault"], Resource = "*" },
        { Effect = "Allow", Action = ["s3:GetBucketAcl", "s3:GetBucketTagging", "s3:GetBucketVersioning", "s3:PutBucketVersioning"], Resource = "arn:aws:s3:::*", Condition = { StringEquals = { "s3:ResourceAccount" = { Ref = "AWS::AccountId" } } } }
      ] } }]
    } }
  } })
}
resource "aws_cloudformation_stack_instances" "role" {
  stack_set_name = aws_cloudformation_stack_set.role.name
  call_as        = "DELEGATED_ADMIN"
  regions        = [var.region]
  deployment_targets { organizational_unit_ids = var.target_ou_ids }
  operation_preferences {
    max_concurrent_percentage = 10
    failure_tolerance_count   = 0
  }
}
