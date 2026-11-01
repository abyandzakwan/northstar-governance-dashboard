variable "stage" {
  type = string
  validation {
    condition     = contains(["central", "dev", "testing", "prod"], var.stage)
    error_message = "Use central for the shared portal (legacy dev/testing/prod profiles remain supported)."
  }
}

variable "account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Set account_id to your 12-digit AWS account ID."
  }
}

variable "deployment_mode" {
  type    = string
  default = "organization"
  validation {
    condition     = contains(["organization", "standalone"], var.deployment_mode) && (var.deployment_mode != "standalone" || contains(["dev", "central"], var.stage))
    error_message = "Use organization mode, or standalone mode with stage=central."
  }
}

variable "auth_mode" {
  type    = string
  default = "saml"
  validation {
    condition     = contains(["saml", "cognito"], var.auth_mode) && (var.stage != "prod" || var.auth_mode == "saml")
    error_message = "Use saml or cognito authentication; the prod profile requires SAML."
  }
}

variable "standalone_account_name" {
  type    = string
  default = "Test account"
}
variable "standalone_environment" {
  type    = string
  default = "dev"
  validation {
    condition     = contains(["dev", "testing", "production"], var.standalone_environment)
    error_message = "Choose a governance profile: dev, testing or production."
  }
}
variable "collection_regions" {
  type    = list(string)
  default = []
}

variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "owner" {
  type    = string
  default = "platform"
}

variable "organization_id" {
  type    = string
  default = ""
  validation {
    condition     = var.deployment_mode == "standalone" || can(regex("^o-[a-z0-9]+$", var.organization_id))
    error_message = "Organization mode requires a valid organization_id."
  }
}

variable "target_ou_ids" {
  type    = list(string)
  default = []
  validation {
    condition     = var.deployment_mode == "standalone" || length(var.target_ou_ids) > 0
    error_message = "Explicit OU scope is mandatory, including in sandbox stages."
  }
}

variable "config_aggregator" {
  type    = string
  default = ""
  validation {
    condition     = var.deployment_mode == "standalone" || var.config_aggregator != ""
    error_message = "Organization mode requires a Config aggregator."
  }
}

variable "config_rule_mapping" {
  type = map(string)
  default = {
  }
}

variable "saml_metadata_url" {
  type    = string
  default = ""
  validation {
    condition     = var.auth_mode == "cognito" || startswith(var.saml_metadata_url, "https://")
    error_message = "SAML mode requires an HTTPS metadata URL."
  }
}

variable "artifact_bucket" {
  type = string
}

variable "artifact_version" {
  type = string
}

variable "artifact_hash" {
  type = string
}

variable "domain_name" {
  type    = string
  default = ""
}

variable "hosted_zone_id" {
  type    = string
  default = ""
}

variable "vpn_cidrs" {
  type    = list(string)
  default = []
}

variable "member_role_name" {
  type    = string
  default = "GovPortalReadOnly"
}

variable "baseline_stackset_name" {
  type    = string
  default = ""
}

variable "baseline_version" {
  type    = string
  default = ""
}

variable "baseline_released_at" {
  type    = string
  default = ""
}

variable "slack_secret_arn" {
  type    = string
  default = ""
}

variable "notification_email" {
  type    = string
  default = ""
}


variable "enable_remediation" {
  type    = bool
  default = false
}
variable "remediation_driver" {
  type    = string
  default = "terraform"
  validation {
    condition     = contains(["terraform", "api"], var.remediation_driver)
    error_message = "Choose terraform or api."
  }
}
variable "remediation_repository" {
  type    = string
  default = ""
}
variable "remediation_github_secret_arn" {
  type    = string
  default = ""
}
variable "remediation_approvers" {
  type    = list(string)
  default = []
}
variable "remediation_member_role" {
  type    = string
  default = "GovPortalRemediation"
}
