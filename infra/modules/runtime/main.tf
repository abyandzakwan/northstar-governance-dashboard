variable "name" {
  type = string
}

variable "artifact_bucket" {
  type = string
}

variable "artifact_key" {
  type = string
}

variable "artifact_hash" {
  type = string
}

variable "kms_arn" {
  type = string
}

variable "environment" {
  type = map(string)
}

variable "policies" {
  type = any
}

variable "dlq_arn" {
  type = string
}
variable "reserve_concurrency" {
  type    = bool
  default = true
}

locals {
  functions = {
    remediation_validate = { handler = "portal.remediation_workers.validate_callback", timeout = 120, memory = 512 },
    remediation_plan     = { handler = "portal.remediation_workers.planner", timeout = 300, memory = 512 },
    remediation_apply    = { handler = "portal.remediation_workers.applier", timeout = 300, memory = 512 },
    remediation_finalize = { handler = "portal.remediation_workers.finalizer", timeout = 120, memory = 256 },
    remediation_callback = { handler = "portal.remediation_workers.approval_callback", timeout = 120, memory = 512 },
    api = {
      handler = "portal.api.handler", timeout = 28, memory = 512
    },
    discovery = {
      handler = "portal.discovery.handler", timeout = 300, memory = 512
    },
    collector = {
      handler = "portal.collector.handler", timeout = 300, memory = 1024
    },
    evaluator = {
      handler = "portal.evaluator.handler", timeout = 300, memory = 1024
    },
    events = {
      handler = "portal.events.handler", timeout = 120, memory = 256
    },
    reports = {
      handler = "portal.reports.handler", timeout = 300, memory = 1024
    },
    retry = {
      handler = "portal.events.retry_handler", timeout = 120, memory = 256
    },
    health = { handler = "portal.health.handler", timeout = 60, memory = 256 },
    auth = {
      handler = "portal.auth.token_handler", timeout = 5, memory = 256
    }
  }
}

resource "aws_iam_role" "lambda" {
  for_each = local.functions
  name     = "${var.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "lambda.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = local.functions
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = 30
  kms_key_id        = var.kms_arn
}

resource "aws_iam_role_policy" "lambda" {
  for_each = local.functions
  name     = "portal-runtime"
  role     = aws_iam_role.lambda[each.key].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
      {
        Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda[each.key].arn}:*"
      },
      {
        Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = var.kms_arn
      }
    ], var.policies[each.key])
  })
}

resource "aws_lambda_function" "function" {
  for_each                       = local.functions
  function_name                  = "${var.name}-${each.key}"
  role                           = aws_iam_role.lambda[each.key].arn
  handler                        = each.value.handler
  runtime                        = "python3.12"
  kms_key_arn                    = var.kms_arn
  architectures                  = ["x86_64"]
  s3_bucket                      = var.artifact_bucket
  s3_key                         = var.artifact_key
  source_code_hash               = var.artifact_hash
  publish                        = true
  timeout                        = each.value.timeout
  memory_size                    = each.value.memory
  reserved_concurrent_executions = var.reserve_concurrency ? (each.key == "collector" ? 50 : each.key == "auth" ? 5 : 10) : -1
  environment {
    variables = var.environment
  }

  tracing_config {
    mode = "Active"
  }

  logging_config {
    log_format = "JSON"
  }

  dead_letter_config {
    target_arn = var.dlq_arn
  }

  depends_on = [aws_iam_role_policy.lambda, aws_cloudwatch_log_group.lambda]
}

resource "aws_lambda_alias" "api" {
  name             = "live"
  function_name    = aws_lambda_function.function["api"].function_name
  function_version = aws_lambda_function.function["api"].version
  lifecycle {
    ignore_changes = [function_version, routing_config]
  }
}

output "functions" {
  value = {
    for k, f in aws_lambda_function.function : k => {
      arn = f.arn, name = f.function_name, version = f.version, invoke_arn = f.invoke_arn
    }
  }
}

output "api_alias_arn" {
  value = aws_lambda_alias.api.arn
}

output "api_alias_invoke_arn" {
  value = aws_lambda_alias.api.invoke_arn
}

output "collector_role_arn" {
  value = aws_iam_role.lambda["collector"].arn
}

