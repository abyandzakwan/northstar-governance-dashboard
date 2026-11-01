mock_provider "aws" {}
variables {
  stage      = "prod"
  account_id = "333333333333"
  region     = "ap-southeast-1"
  repository = "example/gov-portal"
}
run "plan_oidc_and_independent_bootstrap_roles" {
  command = plan
  assert {
    condition     = local.name == "gov-portal-prod"
    error_message = "Foundation roles and buckets must remain stage-scoped."
  }
}

run "central_has_distinct_remediation_oidc_identities" {
  command = plan
  variables { stage = "central" }
  assert {
    condition     = length(aws_iam_role.remediation) == 2 && length(aws_iam_role.org) == 2
    error_message = "Central deployment needs distinct plan/apply identities and independent organization bootstrap."
  }
}
