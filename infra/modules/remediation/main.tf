variable "name" { type = string }
variable "functions" { type = any }
variable "kms_arn" { type = string }

resource "aws_iam_role" "workflow" {
  name               = "${var.name}-remediation-workflow"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "states.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "workflow" {
  role = aws_iam_role.workflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = [for key in ["remediation_plan", "remediation_apply", "remediation_finalize"] : var.functions[key].arn] },
    { Effect = "Allow", Action = ["logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery", "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups", "xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"], Resource = "*" }
  ] })
}
resource "aws_cloudwatch_log_group" "workflow" {
  name              = "/aws/vendedlogs/states/${var.name}-remediation"
  retention_in_days = 90
  kms_key_id        = var.kms_arn
}
resource "aws_sfn_state_machine" "workflow" {
  name     = "${var.name}-remediation"
  role_arn = aws_iam_role.workflow.arn
  tracing_configuration { enabled = true }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.workflow.arn}:*"
    include_execution_data = false
    level                  = "ALL"
  }
  definition = jsonencode({
    StartAt = "Phase", TimeoutSeconds = 3600,
    States = {
      Phase              = { Type = "Choice", Choices = [{ Variable = "$.phase", StringEquals = "apply", Next = "ApplyTargets" }], Default = "PlanOrSubmit" },
      PlanOrSubmit       = { Type = "Task", Resource = var.functions.remediation_plan.arn, Next = "Ready" },
      Ready              = { Type = "Choice", Choices = [{ Variable = "$.status", StringEquals = "READY", Next = "ApplyTargets" }], Default = "AwaitDashboardOrPR" },
      AwaitDashboardOrPR = { Type = "Succeed" },
      ApplyTargets = {
        Type = "Map", ItemsPath = "$.targets", MaxConcurrency = 5, ResultPath = null, Next = "Finalize",
        Iterator = { StartAt = "ApplyOne", States = {
          ApplyOne = {
            Type  = "Task", Resource = var.functions.remediation_apply.arn, End = true,
            Retry = [{ ErrorEquals = ["Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException", "RuntimeError"], IntervalSeconds = 5, BackoffRate = 2, MaxAttempts = 3 }],
            Catch = [{ ErrorEquals = ["States.ALL"], ResultPath = "$.error", Next = "TargetFailed" }]
          },
          TargetFailed = { Type = "Pass", End = true }
        } }
      },
      Finalize = { Type = "Task", Resource = var.functions.remediation_finalize.arn, End = true }
    }
  })
}
output "arn" { value = aws_sfn_state_machine.workflow.arn }
