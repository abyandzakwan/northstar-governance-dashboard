locals {
  name              = "gov-portal-${var.stage}"
  state_machine_arn = "arn:aws:states:${var.region}:${var.account_id}:stateMachine:${local.name}-scan"
  functions_arn     = "arn:aws:lambda:${var.region}:${var.account_id}:function:${local.name}"
  table_read = {
    Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:Scan"], Resource = [module.data.table_arn, "${module.data.table_arn}/index/*"]
  }

  table_write = {
    Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:BatchWriteItem", "dynamodb:UpdateItem"], Resource = module.data.table_arn
  }

  history_read = {
    Effect = "Allow", Action = ["s3:GetObject"], Resource = "${module.data.buckets.history.arn}/*"
  }

  history_write = {
    Effect = "Allow", Action = ["s3:PutObject"], Resource = "${module.data.buckets.history.arn}/*"
  }

  queue_send = {
    Effect = "Allow", Action = "sqs:SendMessage", Resource = module.events.dlq_arn
  }

  scan_start = {
    Effect = "Allow", Action = "states:StartExecution", Resource = local.state_machine_arn
  }
}

module "data" {
  source     = "../../modules/data"
  name       = local.name
  stage      = var.stage
  account_id = var.account_id
  region     = var.region
}

module "frontend" {
  source     = "../../modules/frontend"
  name       = local.name
  account_id = var.account_id
  kms_arn    = module.data.kms_arn
}

module "edge" {
  source = "../../modules/network-edge"
  providers = {
    aws = aws, aws.us_east_1 = aws.us_east_1
  }

  name             = local.name
  bucket           = module.frontend.bucket
  bucket_arn       = module.frontend.arn
  audit_bucket_arn = module.data.buckets.audit.arn
  origin_domain    = module.frontend.domain
  vpn_cidrs        = var.vpn_cidrs
  domain_name      = var.domain_name
  hosted_zone_id   = var.hosted_zone_id
}

module "events" {
  source          = "../../modules/events"
  name            = local.name
  account_id      = var.account_id
  organization_id = var.deployment_mode == "standalone" ? "" : var.organization_id
  kms_arn         = module.data.kms_arn
}

module "runtime" {
  source              = "../../modules/runtime"
  name                = local.name
  artifact_bucket     = var.artifact_bucket
  artifact_key        = "${var.artifact_version}/functions.zip"
  artifact_hash       = var.artifact_hash
  kms_arn             = module.data.kms_arn
  dlq_arn             = module.events.dlq_arn
  reserve_concurrency = var.deployment_mode != "standalone"
  environment = {
    REMEDIATION_ENABLED           = tostring(var.enable_remediation), REMEDIATION_DRIVER = var.remediation_driver,
    REMEDIATION_REPOSITORY        = var.remediation_repository, REMEDIATION_GITHUB_SECRET_ARN = var.remediation_github_secret_arn,
    REMEDIATION_APPROVERS         = join(",", var.remediation_approvers), REMEDIATION_MEMBER_ROLE = var.remediation_member_role,
    REMEDIATION_STATE_MACHINE_ARN = local.remediation_state_machine_arn, REMEDIATIONS_PATH = "/var/task/governance/remediations.yaml",
    DEPLOYMENT_MODE               = var.deployment_mode, AUTH_MODE = var.auth_mode,
    STANDALONE_ACCOUNT_ID         = var.account_id, STANDALONE_ACCOUNT_NAME = var.standalone_account_name,
    STANDALONE_ENVIRONMENT        = var.standalone_environment, STANDALONE_OWNER_TEAM = var.owner,
    COLLECTION_REGIONS            = jsonencode(length(var.collection_regions) > 0 ? var.collection_regions : var.deployment_mode == "standalone" ? [var.region] : []),
    TABLE_NAME                    = module.data.table_name, HISTORY_BUCKET = module.data.buckets.history.name,
    EXPORT_BUCKET                 = module.data.buckets.exports.name, STATE_MACHINE_ARN = local.state_machine_arn,
    REPORTS_FUNCTION              = "${local.name}-reports", ATHENA_WORKGROUP = module.data.workgroup,
    GLUE_DATABASE                 = module.data.database, RULES_PATH = "/var/task/governance/rules.yaml",
    CONFIG_AGGREGATOR             = var.config_aggregator, CONFIG_RULE_MAPPING = jsonencode(var.config_rule_mapping),
    TARGET_OU_IDS                 = jsonencode(var.target_ou_ids), MEMBER_ROLE_NAME = var.member_role_name,
    BASELINE_STACKSET_NAME        = var.baseline_stackset_name, BASELINE_VERSION = var.baseline_version,
    BASELINE_RELEASED_AT          = var.baseline_released_at, SLACK_SECRET_ARN = var.slack_secret_arn,
    SUMMARY_TOPIC_ARN             = "arn:aws:sns:${var.region}:${var.account_id}:${local.name}-weekly", DLQ_URL = module.events.dlq_url
  }

  policies = {
    api = [local.table_read, local.table_write, local.queue_send, local.scan_start, local.remediation_start, local.history_read,
      {
        Effect = "Allow", Action = "lambda:InvokeFunction", Resource = "${local.functions_arn}-reports"
      },
      {
        Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation"], Resource = [module.data.buckets.queries.arn, "${module.data.buckets.queries.arn}/*", "${module.data.buckets.exports.arn}/*"]
      },
      {
        Effect = "Allow", Action = "s3:ListBucket", Resource = module.data.buckets.history.arn
      },
      {
        Effect = "Allow", Action = ["athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults"], Resource = "arn:aws:athena:${var.region}:${var.account_id}:workgroup/${local.name}"
      },
      {
        Effect = "Allow", Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"], Resource = ["arn:aws:glue:${var.region}:${var.account_id}:catalog", "arn:aws:glue:${var.region}:${var.account_id}:database/${module.data.database}", "arn:aws:glue:${var.region}:${var.account_id}:table/${module.data.database}/*"]
      }
    ],
    discovery = concat([local.table_read, local.table_write, local.history_write, local.queue_send],
      [for statement in [{ Effect = "Allow", Action = "sts:GetCallerIdentity", Resource = "*" }] : statement if var.deployment_mode == "standalone"],
      [for statement in [{ Effect = "Allow", Action = ["organizations:ListAccounts", "organizations:ListParents", "organizations:DescribeOrganizationalUnit", "organizations:ListTagsForResource"], Resource = "*" }] : statement if var.deployment_mode == "organization"]
    ),
    collector = concat([local.table_write, local.history_write, local.queue_send,
      { Effect = "Allow", Action = ["securityhub:GetFindings", "securityhub:DescribeStandards", "securityhub:GetEnabledStandards", "config:SelectAggregateResourceConfig", "config:DescribeAggregateComplianceByConfigRules", "config:GetAggregateComplianceDetailsByConfigRule", "cloudformation:ListStackInstances"], Resource = "*" }
      ], [for statement in [
        { Effect = "Allow", Action = ["sts:GetCallerIdentity", "ec2:DescribeRegions", "cloudformation:DescribeStacks", "backup:ListBackupPlans", "backup:ListBackupSelections", "backup:GetBackupSelection", "s3:ListAllMyBuckets", "s3:GetEncryptionConfiguration", "config:SelectResourceConfig", "config:DescribeComplianceByConfigRule", "config:GetComplianceDetailsByConfigRule"], Resource = "*" }
        ] : statement if var.deployment_mode == "standalone"], [for statement in [
        { Effect = "Allow", Action = "sts:AssumeRole", Resource = "arn:aws:iam::*:role/${var.member_role_name}", Condition = { StringEquals = { "aws:ResourceOrgID" = var.organization_id } } }
    ] : statement if var.deployment_mode == "organization"]),
    evaluator = [local.table_read, local.table_write, local.history_read, local.history_write, local.queue_send,
      {
        Effect = "Allow", Action = "cloudwatch:PutMetricData", Resource = "*", Condition = {
          StringEquals = {
            "cloudwatch:namespace" = "GovPortal"
          }
        }
    }],
    events = [local.history_write, local.scan_start, local.queue_send,
      {
        Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"], Resource = module.events.queue_arn
    }],
    retry = [local.scan_start, local.queue_send,
      {
        Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes", "sqs:ChangeMessageVisibility"], Resource = module.events.dlq_arn
    }],
    reports = concat([local.table_read, local.table_write, local.queue_send,
      {
        Effect = "Allow", Action = "s3:PutObject", Resource = "${module.data.buckets.exports.arn}/*"
      },
      {
        Effect = "Allow", Action = "sns:Publish", Resource = "arn:aws:sns:${var.region}:${var.account_id}:${local.name}-weekly"
      }
      ], var.slack_secret_arn != "" ? [{
        Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = var.slack_secret_arn
    }] : []),
    health               = [local.table_read, local.table_write, local.queue_send, { Effect = "Allow", Action = "cloudwatch:PutMetricData", Resource = "*", Condition = { StringEquals = { "cloudwatch:namespace" = "GovPortal" } } }],
    auth                 = [local.queue_send],
    remediation_validate = concat([local.table_read, local.queue_send], local.github_secret),
    remediation_plan     = concat([local.table_read, local.table_write, local.queue_send], local.remediation_read, local.github_secret),
    remediation_apply    = concat([local.table_read, local.table_write, local.queue_send], local.remediation_read, local.github_secret, local.remediation_write),
    remediation_finalize = [local.table_read, local.table_write, local.queue_send, local.scan_start],
    remediation_callback = concat([local.table_read, local.table_write, local.queue_send, local.scan_start, local.remediation_start], local.github_secret, local.remediation_scope)

  }
}

resource "aws_lambda_event_source_mapping" "events" {
  event_source_arn                   = module.events.queue_arn
  function_name                      = module.runtime.functions.events.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 60
  function_response_types            = ["ReportBatchItemFailures"]
  scaling_config {
    maximum_concurrency = 5
  }
}

module "collector" {
  source         = "../../modules/collector"
  name           = local.name
  region         = var.region
  account_id     = var.account_id
  functions      = module.runtime.functions
  history_bucket = module.data.buckets.history.name
  history_arn    = module.data.buckets.history.arn
  kms_arn        = module.data.kms_arn
  dlq_arn        = module.events.dlq_arn
}

module "api" {
  source            = "../../modules/api"
  name              = "${local.name}-${var.account_id}"
  domain            = module.edge.domain
  region            = var.region
  kms_arn           = module.data.kms_arn
  api_invoke_arn    = module.runtime.api_alias_invoke_arn
  api_alias_arn     = module.runtime.api_alias_arn
  auth_function_arn = module.runtime.functions.auth.arn
  saml_metadata_url = var.saml_metadata_url
  auth_mode         = var.auth_mode
}

module "observability" {
  source             = "../../modules/observability"
  name               = local.name
  region             = var.region
  account_id         = var.account_id
  kms_arn            = module.data.kms_arn
  api_id             = module.api.api_id
  state_machine_arn  = module.collector.arn
  dlq_name           = "${local.name}-failed-accounts"
  notification_email = var.notification_email
}

output "frontend_bucket" {
  value = module.frontend.bucket
}

output "distribution_id" {
  value = module.edge.distribution_id
}

output "portal_url" {
  value = "https://${module.edge.domain}"
}

output "frontend_config" {
  value = module.api.config
}

output "api_function" {
  value = module.runtime.functions.api
}

output "state_machine_arn" {
  value = module.collector.arn
}

output "state_table" {
  value = module.data.table_name
}

output "dlq_url" {
  value = module.events.dlq_url
}

output "codedeploy_app" {
  value = module.observability.codedeploy_app
}

output "codedeploy_group" {
  value = module.observability.codedeploy_group
}

output "collector_role_arn" {
  value = module.runtime.collector_role_arn
}

output "event_bus_arn" {
  value = module.events.bus_arn
}
output "user_pool_id" {
  value = module.api.user_pool_id
}
output "deployment_mode" {
  value = var.deployment_mode
}

