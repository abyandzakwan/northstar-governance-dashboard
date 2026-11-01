variable "name" {
  type = string
}

variable "organization_id" {
  type = string
}

variable "kms_arn" {
  type = string
}

variable "account_id" {
  type = string
}

resource "aws_cloudwatch_event_bus" "central" {
  name = var.name
}

resource "aws_cloudwatch_event_bus_policy" "organization" {
  count          = var.organization_id != "" ? 1 : 0
  event_bus_name = aws_cloudwatch_event_bus.central.name
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Sid = "OrganizationEvents", Effect = "Allow", Principal = "*", Action = "events:PutEvents", Resource = aws_cloudwatch_event_bus.central.arn, Condition = {
        StringEquals = {
          "aws:PrincipalOrgID" = var.organization_id
        }
      }
    }]
  })
}

# Standalone service events arrive on the account's default bus; no StackSet is needed.
resource "aws_cloudwatch_event_rule" "local" {
  count         = var.organization_id == "" ? 1 : 0
  name          = "${var.name}-local-events"
  event_pattern = jsonencode({ source = ["aws.config", "aws.securityhub", "aws.cloudformation"] })
}
resource "aws_cloudwatch_event_target" "local" {
  count = var.organization_id == "" ? 1 : 0
  rule  = aws_cloudwatch_event_rule.local[0].name
  arn   = aws_sqs_queue.events.arn
  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }
}

resource "aws_sqs_queue" "dlq" {
  name                       = "${var.name}-failed-accounts"
  kms_master_key_id          = var.kms_arn
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 900
}

resource "aws_sqs_queue" "events" {
  name                       = "${var.name}-events"
  kms_master_key_id          = var.kms_arn
  visibility_timeout_seconds = 900
  message_retention_seconds  = 86400
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn, maxReceiveCount = 3
  })
}

resource "aws_cloudwatch_event_rule" "changes" {
  name           = "${var.name}-changes"
  event_bus_name = aws_cloudwatch_event_bus.central.name
  event_pattern = jsonencode({
    source = ["aws.config", "aws.securityhub", "aws.cloudformation", "aws.organizations"]
  })
}

resource "aws_cloudwatch_event_target" "queue" {
  rule           = aws_cloudwatch_event_rule.changes.name
  event_bus_name = aws_cloudwatch_event_bus.central.name
  arn            = aws_sqs_queue.events.arn
  dead_letter_config {
    arn = aws_sqs_queue.dlq.arn
  }

  retry_policy {
    maximum_event_age_in_seconds = 3600
    maximum_retry_attempts       = 5
  }
}

resource "aws_sqs_queue_policy" "events" {
  queue_url = aws_sqs_queue.events.url
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "events.amazonaws.com"
        }, Action = "sqs:SendMessage", Resource = aws_sqs_queue.events.arn, Condition = {
        ArnEquals = {
          "aws:SourceArn" = concat([aws_cloudwatch_event_rule.changes.arn], aws_cloudwatch_event_rule.local[*].arn)
        }
      }
    }]
  })
}

resource "aws_sqs_queue_policy" "dlq" {
  queue_url = aws_sqs_queue.dlq.url
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = ["events.amazonaws.com", "scheduler.amazonaws.com"]
        }, Action = "sqs:SendMessage", Resource = aws_sqs_queue.dlq.arn, Condition = {
        StringEquals = {
          "aws:SourceAccount" = var.account_id
        }
      }
    }]
  })
}

output "bus_arn" {
  value = aws_cloudwatch_event_bus.central.arn
}

output "dlq_arn" {
  value = aws_sqs_queue.dlq.arn
}

output "dlq_url" {
  value = aws_sqs_queue.dlq.url
}

output "queue_arn" {
  value = aws_sqs_queue.events.arn
}

