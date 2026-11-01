mock_provider "aws" {}
mock_provider "aws" {
  alias = "us_east_1"
}
variables {
  stage             = "dev"
  account_id        = "111111111111"
  organization_id   = "o-example1234"
  target_ou_ids     = ["ou-abcd-sandbox1"]
  config_aggregator = "sandbox"
  saml_metadata_url = "https://example.com/saml/metadata"
  artifact_bucket   = "gov-portal-dev-artifacts-111111111111"
  artifact_version  = "0123456789012345678901234567890123456789"
  artifact_hash     = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="
}
run "serverless_portal_plan" {
  command = plan
  assert {
    condition     = output.frontend_bucket == "gov-portal-dev-frontend-111111111111"
    error_message = "Stage/account isolation must be preserved in frontend storage."
  }
}

run "single_account_without_organization_or_sso" {
  command = plan
  variables {
    deployment_mode   = "standalone"
    auth_mode         = "cognito"
    organization_id   = ""
    target_ou_ids     = []
    config_aggregator = ""
    saml_metadata_url = ""
  }
  assert {
    condition     = output.deployment_mode == "standalone" && output.frontend_config.identityProvider == null
    error_message = "Standalone deployment must not require a SAML identity provider."
  }
}

run "production_cannot_silently_switch_to_native_login" {
  command = plan
  variables {
    stage     = "prod"
    auth_mode = "cognito"
  }
  expect_failures = [var.auth_mode]
}

run "one_central_pilot_has_gated_remediation" {
  command = plan
  variables {
    stage              = "central"
    deployment_mode    = "standalone"
    auth_mode          = "cognito"
    organization_id    = ""
    target_ou_ids      = []
    config_aggregator  = ""
    saml_metadata_url  = ""
    enable_remediation = false
  }
  assert {
    condition     = local.name == "gov-portal-central" && length(local.remediation_write) == 0
    error_message = "A single central pilot must start without live write permissions."
  }
  assert {
    condition     = contains(keys(module.runtime.functions), "remediation_callback") && contains(keys(module.runtime.functions), "remediation_validate")
    error_message = "Plan validation and execution callbacks must have separate IAM boundaries."
  }
}
