mock_provider "aws" {}

variables {
  name              = "gov-portal-central-ap-southeast-1"
  region            = "ap-southeast-1"
  account_id        = "519012038215"
  kms_arn           = "arn:aws:kms:ap-southeast-1:519012038215:key/example"
  api_id            = "example"
  state_machine_arn = "arn:aws:states:ap-southeast-1:519012038215:stateMachine:example"
  dlq_name          = "example-failed-accounts"
}

run "canary_can_access_the_function_and_its_aliases" {
  command = plan
  module {
    source = "../../modules/observability"
  }
  assert {
    condition = contains(
      flatten([jsondecode(aws_iam_role_policy.codedeploy.policy).Statement[0].Resource]),
      "arn:aws:lambda:ap-southeast-1:519012038215:function:gov-portal-central-ap-southeast-1-api"
      ) && contains(
      flatten([jsondecode(aws_iam_role_policy.codedeploy.policy).Statement[0].Resource]),
      "arn:aws:lambda:ap-southeast-1:519012038215:function:gov-portal-central-ap-southeast-1-api:*"
    )
    error_message = "CodeDeploy needs both the base function and qualified alias resource scope for Lambda operations."
  }
}
