variable "name" {
  type = string
}

variable "region" {
  type = string
}

variable "account_id" {
  type = string
}

variable "kms_arn" {
  type = string
}

variable "api_id" {
  type = string
}

variable "state_machine_arn" {
  type = string
}

variable "dlq_name" {
  type = string
}

variable "notification_email" {
  type    = string
  default = ""
}

resource "aws_sns_topic" "alerts" {
  name              = "${var.name}-alerts"
  kms_master_key_id = var.kms_arn
}

resource "aws_sns_topic" "summary" {
  name              = "${var.name}-weekly"
  kms_master_key_id = var.kms_arn
}

resource "aws_sns_topic_subscription" "email" {
  for_each = var.notification_email != "" ? {
    alerts = aws_sns_topic.alerts.arn, summary = aws_sns_topic.summary.arn
    } : {
  }

  topic_arn = each.value
  protocol  = "email"
  endpoint  = var.notification_email
}

locals {
  alarms = {
    scan_failed = {
      namespace = "AWS/States", metric = "ExecutionsFailed", statistic = "Sum", threshold = 0, period = 300, evaluations = 1, dimensions = {
        StateMachineArn = var.state_machine_arn
      }, comparison     = "GreaterThanThreshold", missing = "notBreaching"
    },
    scan_duration = {
      namespace = "AWS/States", metric = "ExecutionTime", statistic = "Maximum", threshold = 1800000, period = 300, evaluations = 1, dimensions = {
        StateMachineArn = var.state_machine_arn
      }, comparison     = "GreaterThanThreshold", missing = "notBreaching"
    },
    dlq = {
      namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", statistic = "Maximum", threshold = 0, period = 300, evaluations = 12, dimensions = {
        QueueName   = var.dlq_name
      }, comparison = "GreaterThanThreshold", missing = "notBreaching"
    },
    stale = { namespace = "GovPortal", metric = "StaleAccountPercent", statistic = "Maximum", threshold = 2, period = 3600, evaluations = 1, dimensions = {}, comparison = "GreaterThanThreshold", missing = "breaching" },
    unclassified = {
      namespace = "GovPortal", metric = "UnclassifiedAccounts", statistic = "Sum", threshold = 0, period = 300, evaluations = 1, dimensions = {
      }, comparison = "GreaterThanThreshold", missing = "notBreaching"
    },
    api_errors = {
      namespace = "AWS/Lambda", metric = "Errors", statistic = "Sum", threshold = 0, period = 60, evaluations = 1, dimensions = {
        FunctionName = "${var.name}-api", Resource = "${var.name}-api:live"
      }, comparison  = "GreaterThanThreshold", missing = "notBreaching"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "portal" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  statistic           = each.value.statistic
  threshold           = each.value.threshold
  period              = each.value.period
  evaluation_periods  = each.value.evaluations
  comparison_operator = each.value.comparison
  dimensions          = each.value.dimensions
  treat_missing_data  = each.value.missing
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "api_5xx" {
  alarm_name          = "${var.name}-api-5xx-rate"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 1
  threshold           = 1
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  metric_query {
    id          = "rate"
    expression  = "IF(requests > 0, 100 * errors / requests, 0)"
    return_data = true
  }

  metric_query {
    id = "errors"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "5xx"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiId = var.api_id
      }
    }
  }

  metric_query {
    id = "requests"
    metric {
      namespace   = "AWS/ApiGateway"
      metric_name = "Count"
      period      = 300
      stat        = "Sum"
      dimensions = {
        ApiId = var.api_id
      }
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "latency" {
  alarm_name          = "${var.name}-api-p95-latency"
  namespace           = "AWS/ApiGateway"
  metric_name         = "Latency"
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 3
  threshold           = 1500
  comparison_operator = "GreaterThanThreshold"
  dimensions = {
    ApiId = var.api_id
  }

  treat_missing_data = "notBreaching"
  alarm_actions      = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "portal" {
  dashboard_name = var.name
  dashboard_body = jsonencode({
    widgets = [for i, metric in [
      ["AWS/States", "ExecutionTime", "StateMachineArn", var.state_machine_arn],
      ["GovPortal", "AccountsScanned"],
      ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", var.dlq_name],
      ["AWS/ApiGateway", "5xx", "ApiId", var.api_id]
      ] : {
      type = "metric", x = (i % 2) * 12, y = floor(i / 2) * 6, width = 12, height = 6, properties = {
        metrics = [metric], region = var.region, period = 300, stat = "Sum", title = metric[1]
      }
    }]
  })
}

resource "aws_iam_role" "codedeploy" {
  name = "${var.name}-codedeploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "codedeploy.amazonaws.com"
      }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "codedeploy" {
  role = aws_iam_role.codedeploy.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["lambda:GetAlias", "lambda:UpdateAlias", "lambda:GetProvisionedConcurrencyConfig", "lambda:PutProvisionedConcurrencyConfig"], Resource = "arn:aws:lambda:${var.region}:${var.account_id}:function:${var.name}-api:*"
      },
      {
        Effect = "Allow", Action = ["cloudwatch:DescribeAlarms"], Resource = "*"
      }
    ]
  })
}

resource "aws_codedeploy_app" "api" {
  name             = var.name
  compute_platform = "Lambda"
}

resource "aws_codedeploy_deployment_group" "api" {
  app_name               = aws_codedeploy_app.api.name
  deployment_group_name  = "${var.name}-api"
  service_role_arn       = aws_iam_role.codedeploy.arn
  deployment_config_name = "CodeDeployDefault.LambdaCanary10Percent10Minutes"
  deployment_style {
    deployment_type   = "BLUE_GREEN"
    deployment_option = "WITH_TRAFFIC_CONTROL"
  }

  auto_rollback_configuration {
    enabled = true
    events  = ["DEPLOYMENT_FAILURE", "DEPLOYMENT_STOP_ON_ALARM", "DEPLOYMENT_STOP_ON_REQUEST"]
  }

  alarm_configuration {
    enabled                   = true
    alarms                    = [aws_cloudwatch_metric_alarm.portal["api_errors"].alarm_name, aws_cloudwatch_metric_alarm.api_5xx.alarm_name]
    ignore_poll_alarm_failure = false
  }
}

output "summary_arn" {
  value = aws_sns_topic.summary.arn
}

output "codedeploy_app" {
  value = aws_codedeploy_app.api.name
}

output "codedeploy_group" {
  value = aws_codedeploy_deployment_group.api.deployment_group_name
}

