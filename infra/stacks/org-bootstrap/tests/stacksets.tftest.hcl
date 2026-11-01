mock_provider "aws" {}
variables {
  security_account_id = "333333333333"
  organization_id     = "o-example1234"
  region              = "ap-southeast-1"
  target_ou_ids       = ["ou-abcd-workload"]
  collector_role_arns = ["arn:aws:iam::333333333333:role/gov-portal-prod-collector"]
  central_bus_arn     = "arn:aws:events:ap-southeast-1:333333333333:event-bus/gov-portal-prod"
  forwarding_regions  = ["ap-southeast-1", "us-east-1"]
}
run "plan_member_enrollment_and_regional_event_forwarding" {
  command = plan
}
run "plan_shared_sandbox_role_and_two_event_destinations" {
  command = plan
  variables {
    name                = "gov-portal-sandbox"
    role_name           = "GovPortalSandboxReadOnly"
    target_ou_ids       = ["ou-abcd-sandbox1"]
    collector_role_arns = ["arn:aws:iam::111111111111:role/gov-portal-dev-collector", "arn:aws:iam::222222222222:role/gov-portal-testing-collector"]
    central_bus_arns = {
      dev     = "arn:aws:events:ap-southeast-1:111111111111:event-bus/gov-portal-dev"
      testing = "arn:aws:events:ap-southeast-1:222222222222:event-bus/gov-portal-testing"
    }
  }
}

run "bounded_member_writes_are_explicitly_opted_in" {
  command = plan
  variables {
    remediation_writer_role_arns = ["arn:aws:iam::333333333333:role/gov-portal-central-remediation_apply"]
  }
  assert {
    condition     = length(module.member_remediation) == 1
    error_message = "A bounded write-role StackSet must exist only when explicit trusted writer ARNs are configured."
  }
}
