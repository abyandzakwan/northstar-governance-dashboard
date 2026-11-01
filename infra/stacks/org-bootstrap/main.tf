terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.100" }
  }
  backend "s3" {
    use_lockfile = true
  }
}
variable "security_account_id" { type = string }
variable "organization_id" { type = string }
variable "region" { type = string }
variable "target_ou_ids" { type = list(string) }
variable "collector_role_arns" { type = list(string) }
variable "remediation_writer_role_arns" {
  type    = list(string)
  default = []
}
variable "remediation_extra_policy_arns" {
  type    = list(string)
  default = []
}
variable "central_bus_arn" {
  type    = string
  default = ""
}
variable "central_bus_arns" {
  type    = map(string)
  default = {}
}
variable "forwarding_regions" { type = list(string) }
variable "role_name" {
  type    = string
  default = "GovPortalReadOnly"
}
variable "name" {
  type    = string
  default = "gov-portal-central"
}
provider "aws" {
  region              = var.region
  allowed_account_ids = [var.security_account_id]
  default_tags {
    tags = { Service = "gov-portal", Owner = "platform", ManagedBy = "terraform", Stage = "org-bootstrap" }
  }
}
module "member_role" {
  source              = "../../modules/member-role"
  name                = var.name
  role_name           = var.role_name
  collector_role_arns = var.collector_role_arns
  organization_id     = var.organization_id
  target_ou_ids       = var.target_ou_ids
  # IAM roles are global: deploy one stack per member account in the primary Region only.
  regions = [var.region]
}
module "event_forwarding" {
  for_each        = length(var.central_bus_arns) > 0 ? var.central_bus_arns : { default = var.central_bus_arn }
  source          = "../../modules/event-forwarding"
  name            = each.key == "default" ? var.name : "${var.name}-${each.key}"
  central_bus_arn = each.value
  target_ou_ids   = var.target_ou_ids
  regions         = var.forwarding_regions
}
module "member_remediation" {
  count             = length(var.remediation_writer_role_arns) > 0 ? 1 : 0
  source            = "../../modules/member-remediation"
  name              = var.name
  role_name         = "GovPortalRemediation"
  writer_role_arns  = var.remediation_writer_role_arns
  extra_policy_arns = var.remediation_extra_policy_arns
  organization_id   = var.organization_id
  target_ou_ids     = var.target_ou_ids
  region            = var.region
}
