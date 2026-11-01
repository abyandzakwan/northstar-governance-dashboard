locals {
  remediation_state_machine_arn = "arn:aws:states:${var.region}:${var.account_id}:stateMachine:${local.name}-remediation"
  remediation_start             = { Effect = "Allow", Action = "states:StartExecution", Resource = local.remediation_state_machine_arn }
  github_secret                 = var.remediation_github_secret_arn == "" ? [] : [{ Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = var.remediation_github_secret_arn }]
  remediation_scope             = var.deployment_mode == "standalone" ? [] : [{ Effect = "Allow", Action = ["organizations:ListParents", "organizations:DescribeOrganizationalUnit"], Resource = "*" }]
  remediation_read = concat(local.remediation_scope,
    [for statement in [{ Effect = "Allow", Action = ["s3:GetAccountPublicAccessBlock", "ec2:GetEbsEncryptionByDefault", "s3:GetBucketAcl", "s3:GetBucketTagging", "s3:GetBucketVersioning"], Resource = "*" }] : statement if var.deployment_mode == "standalone"],
    [for statement in [{ Effect = "Allow", Action = "sts:AssumeRole", Resource = "arn:aws:iam::*:role/${var.member_role_name}", Condition = { StringEquals = { "aws:ResourceOrgID" = var.organization_id } } }] : statement if var.deployment_mode == "organization"]
  )
  remediation_write = concat(
    [for statement in [
      { Effect = "Allow", Action = ["s3:PutAccountPublicAccessBlock", "ec2:EnableEbsEncryptionByDefault"], Resource = "*" },
      { Effect = "Allow", Action = "s3:PutBucketVersioning", Resource = "arn:aws:s3:::*", Condition = { StringEquals = { "s3:ResourceAccount" = var.account_id } } }
    ] : statement if var.enable_remediation && var.deployment_mode == "standalone"],
    [for statement in [{ Effect = "Allow", Action = "sts:AssumeRole", Resource = "arn:aws:iam::*:role/${var.remediation_member_role}", Condition = { StringEquals = { "aws:ResourceOrgID" = var.organization_id } } }] : statement if var.enable_remediation && var.deployment_mode == "organization"]
  )
}

module "remediation" {
  source    = "../../modules/remediation"
  name      = local.name
  functions = module.runtime.functions
  kms_arn   = module.data.kms_arn
}

output "remediation_state_machine_arn" { value = module.remediation.arn }
output "remediation_callback_function" { value = module.runtime.functions.remediation_callback.name }
output "remediation_reader_role_arns" {
  value = [for key in ["collector", "remediation_plan", "remediation_apply"] : "arn:aws:iam::${var.account_id}:role/${local.name}-${key}"]
}
output "remediation_writer_role_arn" { value = "arn:aws:iam::${var.account_id}:role/${local.name}-remediation_apply" }

output "remediation_validate_function" { value = module.runtime.functions.remediation_validate.name }
