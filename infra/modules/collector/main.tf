variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "functions" {
  type = map(any)
}

variable "history_bucket" {
  type = string
}

variable "history_arn" {
  type = string
}

variable "kms_arn" {
  type = string
}

variable "dlq_arn" {
  type = string
}

resource "aws_iam_role" "states" {
  name = "${var.name}-states"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "states.amazonaws.com"
        }, Action = "sts:AssumeRole", Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "states" {
  role = aws_iam_role.states.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [for f in var.functions : f.arn]
      },
      {
        Effect = "Allow", Action = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"], Resource = ["arn:aws:states:${var.region}:${var.account_id}:stateMachine:${var.name}-scan", "arn:aws:states:${var.region}:${var.account_id}:execution:${var.name}-scan:*"]
      },
      {
        Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${var.history_arn}/*"
      },
      {
        Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = var.kms_arn
      },
      {
        Effect = "Allow", Action = "sqs:SendMessage", Resource = var.dlq_arn
      },
      {
        Effect = "Allow", Action = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups", "xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], Resource = "*"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "states" {
  name              = "/aws/vendedlogs/states/${var.name}"
  retention_in_days = 30
  kms_key_id        = var.kms_arn
}

locals {
  retry = [{
    ErrorEquals = ["Lambda.TooManyRequestsException", "Lambda.ServiceException", "Lambda.SdkClientException", "ThrottlingException", "TooManyRequestsException"], IntervalSeconds = 2, BackoffRate = 2, MaxAttempts = 5, JitterStrategy = "FULL"
  }]
  catch = [{
    ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "DeadLetter"
  }]
}

resource "aws_sfn_state_machine" "scan" {
  name     = "${var.name}-scan"
  role_arn = aws_iam_role.states.arn
  type     = "STANDARD"
  tracing_configuration {
    enabled = true
  }

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.states.arn}:*"
    include_execution_data = false
    level                  = "ERROR"
  }

  definition = jsonencode({
    StartAt = "Discover", TimeoutSeconds = 1800, States = {
      Discover = {
        Type = "Task", Resource = var.functions["discovery"].arn, Next = "Accounts", Retry = local.retry
      },
      Accounts = {
        Type = "Map", MaxConcurrency = 50, ToleratedFailurePercentage = 0,
        ItemReader = {
          Resource = "arn:aws:states:::s3:getObject", ReaderConfig = {
            InputType = "JSON"
            }, Parameters = {
            "Bucket.$" = "$.bucket", "Key.$" = "$.key"
          }
        },
        ResultWriter = {
          Resource = "arn:aws:states:::s3:putObject", Parameters = {
            Bucket = var.history_bucket, Prefix = "map-results"
          }
        },
        ItemProcessor = {
          ProcessorConfig = {
            Mode = "DISTRIBUTED", ExecutionType = "STANDARD"
            }, StartAt = "Collect", States = {
            Collect = {
              Type = "Task", Resource = var.functions["collector"].arn, Next = "Evaluate", Retry = local.retry, Catch = local.catch, ResultPath = "$.collected"
            },
            Evaluate = {
              Type = "Task", Resource = var.functions["evaluator"].arn, InputPath = "$.collected", End = true, Retry = local.retry, Catch = local.catch
            },
            DeadLetter = {
              Type = "Task", Resource = "arn:aws:states:::sqs:sendMessage", Parameters = {
                QueueUrl = "https://sqs.${var.region}.amazonaws.com/${var.account_id}/${var.name}-failed-accounts", "MessageBody.$" = "States.JsonToString($)"
              }, Next    = "AccountQueued"
            },
            AccountQueued = {
              Type = "Pass", Result = { queued_for_retry = true }, End = true
            }
          }
        }, End = true
      }
    }
  })
  depends_on = [aws_iam_role_policy.states]
}

resource "aws_iam_role" "scheduler" {
  name = "${var.name}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "scheduler.amazonaws.com"
        }, Action = "sts:AssumeRole", Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.account_id
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = "states:StartExecution", Resource = aws_sfn_state_machine.scan.arn
      },
      {
        Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [var.functions["retry"].arn, var.functions["reports"].arn, var.functions["health"].arn]
      },
      {
        Effect = "Allow", Action = "sqs:SendMessage", Resource = var.dlq_arn
      },
      {
        Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = var.kms_arn
      }
    ]
  })
}

resource "aws_scheduler_schedule" "jobs" {
  for_each = {
    daily = {
      expression = "cron(0 0 * * ? *)", arn = aws_sfn_state_machine.scan.arn, input = "{}"
    },
    retry = {
      expression = "rate(1 hour)", arn = var.functions["retry"].arn, input = "{}"
    },
    health = { expression = "rate(15 minutes)", arn = var.functions["health"].arn, input = "{}" },
    weekly = {
      expression = "cron(0 8 ? * MON *)", arn = var.functions["reports"].arn, input = jsonencode({
        action = "weekly"
      })
    }
  }

  name                         = "${var.name}-${each.key}"
  schedule_expression          = each.value.expression
  schedule_expression_timezone = "UTC"
  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = each.value.arn
    role_arn = aws_iam_role.scheduler.arn
    input    = each.value.input
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }

    dead_letter_config {
      arn = var.dlq_arn
    }
  }
}

output "arn" {
  value = aws_sfn_state_machine.scan.arn
}

