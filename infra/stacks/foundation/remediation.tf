# The workflow code runs from protected main; the plan role cannot invoke the apply callback.
resource "aws_iam_role" "remediation" {
  for_each             = toset(["plan", "apply"])
  name                 = "${local.name}-remediation-github-${each.key}"
  max_session_duration = 3600
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Federated = module.github_oidc.provider_arn }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com", "token.actions.githubusercontent.com:sub" = "repo:${var.repository}:environment:remediation-${each.key}" } }
  }] })
}
resource "aws_iam_role_policy_attachment" "remediation_read" {
  for_each   = aws_iam_role.remediation
  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
resource "aws_iam_role_policy" "remediation" {
  for_each = aws_iam_role.remediation
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = "lambda:InvokeFunction", Resource = concat(["arn:aws:lambda:${var.region}:${var.account_id}:function:${local.name}-remediation_validate"], each.key == "apply" ? ["arn:aws:lambda:${var.region}:${var.account_id}:function:${local.name}-remediation_callback"] : []) },
    { Effect = "Allow", Action = "sts:AssumeRole", Resource = "arn:aws:iam::*:role/${each.key == "plan" ? "GovPortalReadOnly" : "GovPortalRemediation"}" },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "${aws_s3_bucket.foundation["state"].arn}/accounts/*.tflock" },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"], Resource = aws_kms_key.foundation.arn }
    ], [for statement in [
      { Effect = "Allow", Action = "s3:PutObject", Resource = "${aws_s3_bucket.foundation["state"].arn}/accounts/*" },
      { Effect = "Allow", Action = ["s3:PutAccountPublicAccessBlock", "ec2:EnableEbsEncryptionByDefault"], Resource = "*" },
      { Effect = "Allow", Action = "s3:PutBucketVersioning", Resource = "arn:aws:s3:::*", Condition = { StringEquals = { "s3:ResourceAccount" = var.account_id } } }
  ] : statement if each.key == "apply"]) })
}
output "remediation_github_roles" { value = { for key, role in aws_iam_role.remediation : key => role.arn } }
