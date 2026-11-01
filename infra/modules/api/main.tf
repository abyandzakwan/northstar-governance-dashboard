variable "name" {
  type = string
}

variable "domain" {
  type = string
}

variable "region" {
  type = string
}

variable "kms_arn" {
  type = string
}

variable "api_invoke_arn" {
  type = string
}

variable "api_alias_arn" {
  type = string
}

variable "auth_function_arn" {
  type = string
}

variable "saml_metadata_url" {
  type = string
}
variable "auth_mode" {
  type    = string
  default = "saml"
}

resource "aws_cognito_user_pool" "portal" {
  name              = var.name
  mfa_configuration = var.auth_mode == "cognito" ? "ON" : "OFF"
  dynamic "software_token_mfa_configuration" {
    for_each = var.auth_mode == "cognito" ? [1] : []
    content {
      enabled = true
    }
  }
  password_policy {
    minimum_length                   = 14
    require_lowercase                = true
    require_uppercase                = true
    require_numbers                  = true
    require_symbols                  = true
    temporary_password_validity_days = 3
  }
  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  schema {
    name                = "groups"
    attribute_data_type = "String"
    mutable             = true
    required            = false
    string_attribute_constraints {
      min_length = 0
      max_length = 2048
    }
  }

  lambda_config {
    pre_token_generation = var.auth_function_arn
  }
}

resource "aws_cognito_identity_provider" "sso" {
  count         = var.auth_mode == "saml" ? 1 : 0
  user_pool_id  = aws_cognito_user_pool.portal.id
  provider_name = "CompanySSO"
  provider_type = "SAML"
  provider_details = {
    MetadataURL = var.saml_metadata_url, IDPSignout = "true"
  }

  attribute_mapping = {
    email = "email", "custom:groups" = "groups"
  }
}

resource "aws_cognito_user_pool_client" "spa" {
  name                                 = "${var.name}-spa"
  user_pool_id                         = aws_cognito_user_pool.portal.id
  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = var.auth_mode == "cognito" ? ["COGNITO"] : aws_cognito_identity_provider.sso[*].provider_name
  callback_urls                        = ["https://${var.domain}"]
  logout_urls                          = ["https://${var.domain}"]
  access_token_validity                = 60
  id_token_validity                    = 60
  refresh_token_validity               = 1
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "days"
  }

  prevent_user_existence_errors = "ENABLED"
  read_attributes               = ["email"]
  # Federated attribute mapping needs write access; the client has no password/SRP auth flows.
  # A WAF rule blocks UpdateUserAttributes calls so group assertions cannot be self-modified.
  write_attributes    = var.auth_mode == "cognito" ? ["email"] : ["email", "custom:groups"]
  explicit_auth_flows = var.auth_mode == "cognito" ? ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"] : []
}

resource "aws_cognito_user_group" "local" {
  for_each     = var.auth_mode == "cognito" ? toset(["gov-portal-admins", "gov-portal-viewers"]) : toset([])
  name         = each.value
  user_pool_id = aws_cognito_user_pool.portal.id
}

resource "aws_cognito_user_pool_domain" "portal" {
  domain       = var.name
  user_pool_id = aws_cognito_user_pool.portal.id
}

resource "aws_wafv2_web_acl" "identity" {
  name  = "${var.name}-identity"
  scope = "REGIONAL"
  default_action {
    allow {
    }
  }

  rule {
    name     = "block-attribute-mutation"
    priority = 0
    action {
      block {
      }
    }

    statement {
      byte_match_statement {
        search_string         = "AWSCognitoIdentityProviderService.UpdateUserAttributes"
        positional_constraint = "EXACTLY"
        field_to_match {
          single_header {
            name = "x-amz-target"
          }
        }

        text_transformation {
          priority = 0
          type     = "NONE"
        }
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "block-attributes"
      sampled_requests_enabled   = false
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name}-identity"
    sampled_requests_enabled   = false
  }
}

resource "aws_wafv2_web_acl_association" "identity" {
  resource_arn = aws_cognito_user_pool.portal.arn
  web_acl_arn  = aws_wafv2_web_acl.identity.arn
}

resource "aws_lambda_permission" "cognito" {
  statement_id  = "CognitoTokenGroups"
  action        = "lambda:InvokeFunction"
  function_name = var.auth_function_arn
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.portal.arn
}

resource "aws_apigatewayv2_api" "portal" {
  name          = var.name
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["https://${var.domain}"]
    allow_methods = ["GET", "POST", "DELETE", "PUT", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.portal.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = var.auth_mode == "cognito" ? "native-cognito" : "company-sso"
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.spa.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.portal.id}"
  }
}

resource "aws_apigatewayv2_integration" "api" {
  api_id                 = aws_apigatewayv2_api.portal.id
  integration_type       = "AWS_PROXY"
  integration_uri        = var.api_invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 29000
}

resource "aws_apigatewayv2_route" "api" {
  for_each             = toset(["GET /session", "GET /environments", "GET /environments/{env}/accounts", "GET /accounts", "GET /accounts/{id}", "GET /accounts/{id}/findings", "GET /accounts/{id}/deployments", "GET /rules", "GET /rules/{id}/accounts", "GET /frameworks/{id}", "GET /deployments", "GET /trends", "POST /reports", "GET /reports/{id}", "GET /waivers", "POST /waivers", "DELETE /waivers/{id}", "POST /scans", "GET /settings/weekly", "PUT /settings/weekly"])
  api_id               = aws_apigatewayv2_api.portal.id
  route_key            = each.value
  target               = "integrations/${aws_apigatewayv2_integration.api.id}"
  authorization_type   = "JWT"
  authorizer_id        = aws_apigatewayv2_authorizer.jwt.id
  authorization_scopes = ["openid"]
}

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigateway/${var.name}"
  retention_in_days = 30
  kms_key_id        = var.kms_arn
}

resource "aws_apigatewayv2_stage" "live" {
  api_id      = aws_apigatewayv2_api.portal.id
  name        = "$default"
  auto_deploy = true
  default_route_settings {
    throttling_burst_limit   = 50
    throttling_rate_limit    = 25
    detailed_metrics_enabled = true
  }

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId = "$context.requestId", status = "$context.status", latency = "$context.responseLatency", route = "$context.routeKey", ip = "$context.identity.sourceIp"
    })
  }
}

resource "aws_lambda_permission" "api" {
  statement_id  = "APIGateway"
  action        = "lambda:InvokeFunction"
  function_name = var.api_alias_arn
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.portal.execution_arn}/*/*"
}

output "api_id" {
  value = aws_apigatewayv2_api.portal.id
}

output "url" {
  value = aws_apigatewayv2_api.portal.api_endpoint
}

output "config" {
  value = {
    apiUrl = aws_apigatewayv2_api.portal.api_endpoint, clientId = aws_cognito_user_pool_client.spa.id, cognitoDomain = "https://${aws_cognito_user_pool_domain.portal.domain}.auth.${var.region}.amazoncognito.com", redirectUri = "https://${var.domain}", identityProvider = var.auth_mode == "saml" ? "CompanySSO" : null
  }
}

output "user_pool_id" {
  value = aws_cognito_user_pool.portal.id
}

