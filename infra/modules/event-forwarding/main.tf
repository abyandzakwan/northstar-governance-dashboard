variable "name" {
  type = string
}

variable "central_bus_arn" {
  type = string
}

variable "target_ou_ids" {
  type = list(string)
}

variable "regions" {
  type = list(string)
}

resource "aws_cloudformation_stack_set" "events" {
  name             = "${var.name}-event-forwarding"
  permission_model = "SERVICE_MANAGED"
  call_as          = "DELEGATED_ADMIN"
  capabilities     = ["CAPABILITY_IAM"]
  auto_deployment {
    enabled                          = true
    retain_stacks_on_account_removal = false
  }

  template_body = jsonencode({
    AWSTemplateFormatVersion = "2010-09-09", Resources = {
      Forwarder = {
        Type = "AWS::IAM::Role", Properties = {
          AssumeRolePolicyDocument = {
            Version = "2012-10-17", Statement = [{
              Effect = "Allow", Principal = {
                Service = "events.amazonaws.com"
              }, Action = "sts:AssumeRole"
            }]
          },
          Policies = [{
            PolicyName = "CentralEventsOnly", PolicyDocument = {
              Version = "2012-10-17", Statement = [{
                Effect = "Allow", Action = "events:PutEvents", Resource = var.central_bus_arn
              }]
            }
          }]
        }
      },
      Changes = {
        Type = "AWS::Events::Rule", Properties = {
          EventPattern = {
            source = ["aws.config", "aws.securityhub", "aws.cloudformation"], "detail-type" = ["Config Rules Compliance Change", "Security Hub Findings - Imported", "CloudFormation Stack Status Change", "CloudFormation Drift Detection Status Change"]
          },
          State = "ENABLED", Targets = [{
            Id = "central", Arn = var.central_bus_arn, RoleArn = {
              "Fn::GetAtt" = ["Forwarder", "Arn"]
              }, RetryPolicy = {
              MaximumEventAgeInSeconds = 3600, MaximumRetryAttempts = 10
            }
          }]
        }
      }
    }
  })
}

resource "aws_cloudformation_stack_instances" "events" {
  stack_set_name = aws_cloudformation_stack_set.events.name
  call_as        = "DELEGATED_ADMIN"
  regions        = var.regions
  deployment_targets {
    organizational_unit_ids = var.target_ou_ids
  }

  operation_preferences {
    max_concurrent_percentage    = 25
    failure_tolerance_percentage = 5
  }
}

