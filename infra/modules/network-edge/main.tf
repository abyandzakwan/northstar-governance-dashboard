terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source = "hashicorp/aws", version = ">= 5.100, < 6.0", configuration_aliases = [aws.us_east_1]
    }
  }
}

variable "name" {
  type = string
}

variable "bucket" {
  type = string
}

variable "bucket_arn" {
  type = string
}

variable "origin_domain" {
  type = string
}

variable "vpn_cidrs" {
  type    = list(string)
  default = []
}

variable "domain_name" {
  type    = string
  default = ""
}

variable "hosted_zone_id" {
  type    = string
  default = ""
}

resource "aws_wafv2_ip_set" "vpn" {
  provider           = aws.us_east_1
  count              = length(var.vpn_cidrs) > 0 ? 1 : 0
  name               = "${var.name}-vpn"
  scope              = "CLOUDFRONT"
  ip_address_version = "IPV4"
  addresses          = var.vpn_cidrs
}

resource "aws_wafv2_web_acl" "portal" {
  provider = aws.us_east_1
  name     = var.name
  scope    = "CLOUDFRONT"
  default_action {
    allow {
    }
  }

  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = var.name
    sampled_requests_enabled   = true
  }

  dynamic "rule" {
    for_each = length(var.vpn_cidrs) > 0 ? [1] : []
    content {
      name     = "corporate-vpn-only"
      priority = 0
      action {
        block {
        }
      }

      statement {
        not_statement {
          statement {
            ip_set_reference_statement {
              arn = aws_wafv2_ip_set.vpn[0].arn
            }
          }
        }
      }

      visibility_config {
        cloudwatch_metrics_enabled = true
        metric_name                = "vpn"
        sampled_requests_enabled   = true
      }
    }
  }

  rule {
    name     = "managed-common"
    priority = 10
    override_action {
      none {
      }
    }

    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "common"
      sampled_requests_enabled   = true
    }
  }

  rule {
    name     = "rate-limit"
    priority = 20
    action {
      block {
      }
    }

    statement {
      rate_based_statement {
        limit              = 1000
        aggregate_key_type = "IP"
      }
    }

    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "rate"
      sampled_requests_enabled   = true
    }
  }
}

resource "aws_acm_certificate" "portal" {
  provider          = aws.us_east_1
  count             = var.domain_name != "" ? 1 : 0
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "validation" {
  for_each = var.domain_name != "" ? {
    for d in aws_acm_certificate.portal[0].domain_validation_options : d.domain_name => d
    } : {
  }

  zone_id = var.hosted_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "portal" {
  provider                = aws.us_east_1
  count                   = var.domain_name != "" ? 1 : 0
  certificate_arn         = aws_acm_certificate.portal[0].arn
  validation_record_fqdns = [for r in aws_route53_record.validation : r.fqdn]
}

resource "aws_cloudfront_origin_access_control" "portal" {
  name                              = var.name
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_response_headers_policy" "security" {
  name = var.name
  security_headers_config {
    content_type_options {
      override = true
    }

    frame_options {
      frame_option = "DENY"
      override     = true
    }

    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }

    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }

    content_security_policy {
      content_security_policy = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self' https://*.execute-api.*.amazonaws.com https://*.amazoncognito.com; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
      override                = true
    }
  }
}

resource "aws_cloudfront_distribution" "portal" {
  enabled             = true
  is_ipv6_enabled     = true
  default_root_object = "index.html"
  price_class         = "PriceClass_100"
  web_acl_id          = aws_wafv2_web_acl.portal.arn
  aliases             = var.domain_name != "" ? [var.domain_name] : []
  origin {
    domain_name              = var.origin_domain
    origin_id                = "frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.portal.id
  }

  default_cache_behavior {
    target_origin_id           = "frontend"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD", "OPTIONS"]
    cached_methods             = ["GET", "HEAD"]
    compress                   = true
    cache_policy_id            = "658327ea-f89d-4fab-a63d-7e88639e58f6"
    response_headers_policy_id = aws_cloudfront_response_headers_policy.security.id
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = var.domain_name == ""
    acm_certificate_arn            = var.domain_name != "" ? aws_acm_certificate_validation.portal[0].certificate_arn : null
    ssl_support_method             = var.domain_name != "" ? "sni-only" : null
    minimum_protocol_version       = "TLSv1.2_2021"
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = var.bucket
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Principal = {
          Service = "cloudfront.amazonaws.com"
          }, Action = "s3:GetObject", Resource = "${var.bucket_arn}/*", Condition = {
          StringEquals = {
            "AWS:SourceArn" = aws_cloudfront_distribution.portal.arn
          }
        }
      },
      {
        Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [var.bucket_arn, "${var.bucket_arn}/*"], Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
    ]
  })
}

resource "aws_route53_record" "portal" {
  count   = var.domain_name != "" ? 1 : 0
  zone_id = var.hosted_zone_id
  name    = var.domain_name
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.portal.domain_name
    zone_id                = aws_cloudfront_distribution.portal.hosted_zone_id
    evaluate_target_health = false
  }
}

output "domain" {
  value = var.domain_name != "" ? var.domain_name : aws_cloudfront_distribution.portal.domain_name
}

output "distribution_id" {
  value = aws_cloudfront_distribution.portal.id
}

