variable "name" {
  type = string
}

variable "role_name" {
  type = string
}

variable "collector_role_arns" {
  type = list(string)
}

variable "organization_id" {
  type = string
}

variable "target_ou_ids" {
  type = list(string)
}

variable "regions" {
  type = list(string)
}

resource "aws_cloudformation_stack_set" "role" {
  name             = "${var.name}-member-role"
  permission_model = "SERVICE_MANAGED"
  call_as          = "DELEGATED_ADMIN"
  capabilities     = ["CAPABILITY_NAMED_IAM"]
  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = false
  }

  managed_execution {
    active = true
  }

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09", Resources = {
      ReadOnlyRole = {
        Type = "AWS::IAM::Role", Properties = {
          RoleName = var.role_name,
          AssumeRolePolicyDocument = {
            Version = "2012-10-17", Statement = [{
              Effect = "Allow", Principal = {
                AWS = distinct([for arn in var.collector_role_arns : "arn:aws:iam::${split(":", arn)[4]}:root"])
                }, Action = "sts:AssumeRole", Condition = {
                StringEquals = {
                  "aws:PrincipalOrgID" = var.organization_id
                }
                ArnEquals = {
                  "aws:PrincipalArn" = var.collector_role_arns
                }
              }
            }]
          },
          ManagedPolicyArns = ["arn:aws:iam::aws:policy/SecurityAudit", "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"],
          Policies = [{
            PolicyName = "PortalReadOnly", PolicyDocument = {
              Version = "2012-10-17", Statement = [{
                Effect = "Allow", Action = ["cloudformation:DescribeStacks", "cloudformation:DescribeStackDriftDetectionStatus", "cloudformation:DetectStackDrift", "tag:GetResources", "backup:ListBackupPlans", "backup:ListBackupSelections", "backup:GetBackupSelection", "s3:GetEncryptionConfiguration"], Resource = "*"
              }]
            }
          }]
        }
      }
    }
  })
}

resource "aws_cloudformation_stack_instances" "role" {
  stack_set_name = aws_cloudformation_stack_set.role.name
  call_as        = "DELEGATED_ADMIN"
  regions        = var.regions
  deployment_targets {
    organizational_unit_ids = var.target_ou_ids
  }

  operation_preferences {
    max_concurrent_percentage    = 25
    failure_tolerance_percentage = 5
  }
}

