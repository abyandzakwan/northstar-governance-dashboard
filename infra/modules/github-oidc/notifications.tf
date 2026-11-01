variable "release_slack_secret_arn" {
  type    = string
  default = ""
}
variable "release_slack_kms_arn" {
  type    = string
  default = ""
}
resource "aws_iam_role_policy" "notifications" {
  count = var.release_slack_secret_arn != "" ? 1 : 0
  name  = "release-notification"
  role  = aws_iam_role.github["deploy"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = "secretsmanager:GetSecretValue", Resource = var.release_slack_secret_arn }
  ], [for key in [var.release_slack_kms_arn] : { Effect = "Allow", Action = "kms:Decrypt", Resource = key } if key != ""]) })
}
