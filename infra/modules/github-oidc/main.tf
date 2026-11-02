variable "name" {
  type = string
}

variable "repository" {
  type = string
}

variable "stage" {
  type = string
}

variable "account_id" {
  type = string
}

variable "region" {
  type = string
}

variable "state_bucket" {
  type = string
}

variable "artifact_bucket" {
  type = string
}

variable "artifact_kms_arn" {
  type = string
}

variable "create_provider" {
  type    = bool
  default = true
}

variable "provider_arn" {
  type    = string
  default = ""
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  provider_arn = var.create_provider ? aws_iam_openid_connect_provider.github[0].arn : var.provider_arn
  subjects = {
    plan   = ["repo:${var.repository}:environment:${var.stage}-plan"],
    deploy = ["repo:${var.repository}:environment:${var.stage}"]
  }
}

resource "aws_iam_role" "github" {
  for_each             = local.subjects
  name                 = "${var.name}-github-${each.key}"
  max_session_duration = 7200
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Federated = local.provider_arn
        }, Action = "sts:AssumeRoleWithWebIdentity", Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = each.value
        }
      }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "plan" {
  for_each   = aws_iam_role.github
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "state" {
  for_each = aws_iam_role.github
  role     = each.value.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketVersioning"], Resource = "arn:aws:s3:::${var.state_bucket}"
      },
      {
        Effect = "Allow", Action = "s3:GetObject", Resource = "arn:aws:s3:::${var.state_bucket}/portal/${var.stage}.tfstate"
      },
      {
        Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "arn:aws:s3:::${var.state_bucket}/portal/${var.stage}.tfstate.tflock"
      },
      {
        Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = "arn:aws:s3:::${var.artifact_bucket}"
      },
      {
        Effect = "Allow", Action = "s3:GetObject", Resource = "arn:aws:s3:::${var.artifact_bucket}/*"
      },
      {
        Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = var.artifact_kms_arn
      }
    ]
  })
}

resource "aws_iam_role_policy" "artifact_upload" {
  role   = aws_iam_role.github["plan"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = "s3:PutObject", Resource = "arn:aws:s3:::${var.artifact_bucket}/*" }] })
}

resource "aws_iam_role_policy" "deploy" {
  role = aws_iam_role.github["deploy"].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["s3:PutObject", "s3:DeleteObject"], Resource = ["arn:aws:s3:::${var.state_bucket}/portal/${var.stage}.tfstate", "arn:aws:s3:::${var.artifact_bucket}/*"]
      },
      {
        Effect = "Allow", Action = ["s3:*"], Resource = ["arn:aws:s3:::${var.name}-*", "arn:aws:s3:::${var.name}-*/*"]
      },
      {
        Effect = "Allow", Action = ["lambda:*"], Resource = "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.name}-*"
      },
      {
        Effect = "Allow", Action = ["iam:CreateRole", "iam:DeleteRole", "iam:GetRole", "iam:UpdateAssumeRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:GetRolePolicy", "iam:ListRolePolicies", "iam:ListAttachedRolePolicies", "iam:TagRole", "iam:UntagRole", "iam:PassRole"], Resource = [for role in ["api", "discovery", "collector", "evaluator", "events", "reports", "retry", "health", "auth", "states", "scheduler", "codedeploy", "remediation_validate", "remediation_plan", "remediation_apply", "remediation_finalize", "remediation_callback", "remediation-workflow"] : "arn:aws:iam::${var.account_id}:role/${var.name}-${role}"]
      },
      {
        Effect = "Allow", Action = ["dynamodb:*"], Resource = "arn:aws:dynamodb:${var.region}:${var.account_id}:table/${var.name}-*"
      },
      {
        Effect = "Allow", Action = ["states:*"], Resource = ["arn:aws:states:${var.region}:${var.account_id}:stateMachine:${var.name}-*", "arn:aws:states:${var.region}:${var.account_id}:execution:${var.name}-*:*"]
      },
      {
        Effect = "Allow", Action = ["sqs:*", "sns:*", "events:*", "scheduler:*", "logs:*", "athena:*", "glue:*", "cloudtrail:*", "codedeploy:*", "cloudwatch:*"], Resource = ["arn:aws:sqs:${var.region}:${var.account_id}:${var.name}-*", "arn:aws:sns:${var.region}:${var.account_id}:${var.name}-*", "arn:aws:events:${var.region}:${var.account_id}:event-bus/${var.name}", "arn:aws:events:${var.region}:${var.account_id}:rule/${var.name}/*", "arn:aws:scheduler:${var.region}:${var.account_id}:schedule/default/${var.name}-*", "arn:aws:logs:*:${var.account_id}:log-group:/aws/*/${var.name}*", "arn:aws:logs:*:${var.account_id}:delivery-source:${var.name}-*", "arn:aws:logs:*:${var.account_id}:delivery-destination:${var.name}-*", "arn:aws:logs:*:${var.account_id}:delivery:*", "arn:aws:athena:${var.region}:${var.account_id}:workgroup/${var.name}", "arn:aws:glue:${var.region}:${var.account_id}:catalog", "arn:aws:glue:${var.region}:${var.account_id}:database/${replace(var.name, "-", "_")}", "arn:aws:glue:${var.region}:${var.account_id}:table/${replace(var.name, "-", "_")}/*", "arn:aws:cloudtrail:${var.region}:${var.account_id}:trail/${var.name}-*", "arn:aws:codedeploy:${var.region}:${var.account_id}:application:${var.name}", "arn:aws:codedeploy:${var.region}:${var.account_id}:deploymentgroup:${var.name}/*", "arn:aws:cloudwatch:${var.region}:${var.account_id}:alarm:${var.name}-*", "arn:aws:cloudwatch::${var.account_id}:dashboard/${var.name}"]
      },
      {
        Effect = "Allow", Action = ["kms:CreateKey", "kms:ListKeys", "kms:ListAliases", "kms:CreateAlias"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["kms:*"], Resource = "arn:aws:kms:${var.region}:${var.account_id}:key/*", Condition = {
          StringEquals = {
            "aws:ResourceTag/Service" = "gov-portal"
          }
        }
      },
      {
        Effect = "Allow", Action = ["kms:*"], Resource = "arn:aws:kms:${var.region}:${var.account_id}:alias/${var.name}*"
      },
      {
        Effect = "Allow", Action = ["apigateway:*"], Resource = ["arn:aws:apigateway:${var.region}::/apis", "arn:aws:apigateway:${var.region}::/apis/*"]
      },
      {
        Effect = "Allow", Action = ["cognito-idp:*"], Resource = "arn:aws:cognito-idp:${var.region}:${var.account_id}:userpool/*", Condition = {
          StringEquals = {
            "aws:ResourceTag/Service" = "gov-portal"
          }
        }
      },
      {
        Effect = "Allow", Action = ["cognito-idp:CreateUserPool", "cognito-idp:ListUserPools", "cognito-idp:DescribeUserPoolDomain", "cloudfront:*", "wafv2:*", "acm:*", "route53:ChangeResourceRecordSets", "route53:GetChange", "route53:ListResourceRecordSets", "route53:GetHostedZone", "cloudwatch:DescribeAlarms", "cloudwatch:ListDashboards", "logs:DescribeLogGroups", "logs:DescribeDeliverySources", "logs:DescribeDeliveryDestinations", "logs:DescribeDeliveries", "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:ListLogDeliveries", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "events:ListEventBuses", "events:ListRules", "scheduler:ListSchedules", "athena:ListWorkGroups", "glue:GetDatabases", "lambda:ListFunctions", "cloudtrail:ListTrails", "cloudtrail:DescribeTrails", "sts:GetCallerIdentity", "codedeploy:GetDeployment", "codedeploy:GetDeploymentConfig", "codedeploy:ListApplications"], Resource = "*"
      }
    ]
  })
}

output "plan_role_arn" {
  value = aws_iam_role.github["plan"].arn
}

output "deploy_role_arn" {
  value = aws_iam_role.github["deploy"].arn
}

output "provider_arn" {
  value = local.provider_arn
}

