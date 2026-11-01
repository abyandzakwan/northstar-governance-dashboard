variable "name" {
  type = string
}

variable "stage" {
  type = string
}

variable "account_id" {
  type = string
}

variable "region" {
  type = string
}


resource "aws_kms_key" "portal" {
  description             = "Governance portal confidential evidence"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Sid = "AccountAdministration", Effect = "Allow", Principal = {
          AWS     = "arn:aws:iam::${var.account_id}:root"
        }, Action = "kms:*", Resource = "*"
      },
      {
        Sid = "CloudWatchLogs", Effect = "Allow", Principal = {
          Service = "logs.${var.region}.amazonaws.com"
          }, Action = ["kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:Describe*"], Resource = "*", Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${var.region}:${var.account_id}:*"
          }
        }
      },
      {
        Sid = "CloudFrontOrigin", Effect = "Allow", Principal = {
          Service = "cloudfront.amazonaws.com"
          }, Action = "kms:Decrypt", Resource = "*", Condition = {
          ArnLike = {
            "AWS:SourceArn" = "arn:aws:cloudfront::${var.account_id}:distribution/*"
          }
        }
      },
      {
        Sid = "ServiceEncryption", Effect = "Allow", Principal = {
          Service = ["sns.amazonaws.com", "events.amazonaws.com", "s3.amazonaws.com", "cloudtrail.amazonaws.com", "delivery.logs.amazonaws.com"]
          }, Action = ["kms:Decrypt", "kms:GenerateDataKey*"], Resource = "*", Condition = {
          StringEquals = {
            "aws:SourceAccount" = var.account_id
          }
        }
      }
    ]
  })
}

resource "aws_kms_alias" "portal" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.portal.key_id
}

resource "aws_dynamodb_table" "state" {
  name         = "${var.name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "PK"
  range_key    = "SK"
  dynamic "attribute" {
    for_each = toset(["PK", "SK", "GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK"])
    content {
      name = attribute.value
      type = "S"
    }
  }

  dynamic "global_secondary_index" {
    for_each = toset(["GSI1", "GSI2", "GSI3"])
    content {
      name            = global_secondary_index.value
      hash_key        = "${global_secondary_index.value}PK"
      range_key       = "${global_secondary_index.value}SK"
      projection_type = "ALL"
    }
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.portal.arn
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  deletion_protection_enabled = var.stage == "prod"
}

resource "aws_s3_bucket" "data" {
  for_each = toset(["history", "exports", "queries", "audit"])
  bucket   = "${var.name}-${each.key}-${var.account_id}"
}

resource "aws_s3_bucket_public_access_block" "data" {
  for_each                = aws_s3_bucket.data
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "history" {
  bucket = aws_s3_bucket.data["history"].id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_versioning" "exports" {
  bucket = aws_s3_bucket.data["exports"].id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_versioning" "queries" {
  bucket = aws_s3_bucket.data["queries"].id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_versioning" "audit" {
  bucket = aws_s3_bucket.data["audit"].id
  versioning_configuration {
    status = "Enabled"
  }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.portal.arn
    }

    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_policy" "data" {
  for_each = aws_s3_bucket.data
  bucket   = each.value.id
  policy = jsonencode({
    Version = "2012-10-17", Statement = concat([
      {
        Sid = "TLSOnly", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [each.value.arn, "${each.value.arn}/*"], Condition = {
          Bool = {
            "aws:SecureTransport" = "false"
          }
        }
      }
      ], each.key == "audit" ? [
      { Sid = "LogDeliveryACL", Effect = "Allow", Principal = { Service = "delivery.logs.amazonaws.com" }, Action = "s3:GetBucketAcl", Resource = each.value.arn, Condition = { StringEquals = { "aws:SourceAccount" = var.account_id } } },
      { Sid = "LogDeliveryWrite", Effect = "Allow", Principal = { Service = "delivery.logs.amazonaws.com" }, Action = "s3:PutObject", Resource = "${each.value.arn}/AWSLogs/${var.account_id}/*", Condition = { StringEquals = { "aws:SourceAccount" = var.account_id, "s3:x-amz-acl" = "bucket-owner-full-control" } } },
      {
        Sid = "CloudTrailACL", Effect = "Allow", Principal = {
          Service = "cloudtrail.amazonaws.com"
          }, Action = "s3:GetBucketAcl", Resource = each.value.arn, Condition = {
          StringEquals = {
            "aws:SourceAccount" = var.account_id
          }
        }
      },
      {
        Sid = "CloudTrailWrite", Effect = "Allow", Principal = {
          Service = "cloudtrail.amazonaws.com"
          }, Action = "s3:PutObject", Resource = "${each.value.arn}/AWSLogs/${var.account_id}/*", Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control", "aws:SourceAccount" = var.account_id
          }
        }
      }
    ] : [])
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "history" {
  bucket = aws_s3_bucket.data["history"].id
  rule {
    id     = "snapshots-seven-years"
    status = "Enabled"
    filter {
      prefix = "snapshots/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 400
      storage_class = "GLACIER_IR"
    }

    expiration {
      days = 2557
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  rule {
    id     = "daily-scores-seven-years"
    status = "Enabled"
    filter {
      prefix = "daily-scores/"
    }

    transition {
      days          = 30
      storage_class = "STANDARD_IA"
    }

    transition {
      days          = 400
      storage_class = "GLACIER_IR"
    }

    expiration {
      days = 2557
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }
  }

  dynamic "rule" {
    for_each = {
      events = 90, collection = 2, "scan-input" = 2, "map-results" = 7
    }

    content {
      id     = "expire-${rule.key}"
      status = "Enabled"
      filter {
        prefix = "${rule.key}/"
      }

      expiration {
        days = rule.value
      }

      noncurrent_version_expiration {
        noncurrent_days = 1
      }
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "temporary" {
  for_each = toset(["exports", "queries"])
  bucket   = aws_s3_bucket.data[each.key].id
  rule {
    id     = "expire"
    status = "Enabled"
    filter {
      prefix = ""
    }

    expiration {
      days = 7
    }

    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

resource "aws_cloudtrail" "history" {
  name                       = "${var.name}-evidence-access"
  s3_bucket_name             = aws_s3_bucket.data["audit"].id
  kms_key_id                 = aws_kms_key.portal.arn
  enable_log_file_validation = true
  is_multi_region_trail      = true
  event_selector {
    read_write_type           = "All"
    include_management_events = false
    data_resource {
      type   = "AWS::S3::Object"
      values = ["${aws_s3_bucket.data["history"].arn}/", "${aws_s3_bucket.data["exports"].arn}/"]
    }
  }

  depends_on = [aws_s3_bucket_policy.data]
}

resource "aws_glue_catalog_database" "history" {
  name = replace(var.name, "-", "_")
}

resource "aws_glue_catalog_table" "snapshots" {
  name          = "snapshots"
  database_name = aws_glue_catalog_database.history.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                      = "TRUE", classification = "parquet", "projection.enabled" = "true",
    "projection.dt.type"          = "date", "projection.dt.range" = "2026-01-01,NOW", "projection.dt.format" = "yyyy-MM-dd",
    "projection.environment.type" = "enum", "projection.environment.values" = "dev,testing,production,unclassified",
    "storage.location.template"   = "s3://${aws_s3_bucket.data["history"].id}/snapshots/dt=$${dt}/environment=$${environment}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  partition_keys {
    name = "environment"
    type = "string"
  }

  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data["history"].id}/snapshots/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }

    dynamic "columns" {
      for_each = {
        account_id = "string", owner_team = "string", account_score = "double", rule_id = "string", resource = "string", status = "string", severity = "string", evaluated_at = "string", waiver_id = "string", frameworks = "string"
      }

      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}

resource "aws_athena_workgroup" "history" {
  name = var.name
  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824
    result_configuration {
      output_location = "s3://${aws_s3_bucket.data["queries"].id}/"
      encryption_configuration {
        encryption_option = "SSE_KMS"
        kms_key_arn       = aws_kms_key.portal.arn
      }
    }
  }
}

output "kms_arn" {
  value = aws_kms_key.portal.arn
}

output "table_name" {
  value = aws_dynamodb_table.state.name
}

output "table_arn" {
  value = aws_dynamodb_table.state.arn
}

output "buckets" {
  value = {
    for k, v in aws_s3_bucket.data : k => {
      name = v.id, arn = v.arn
    }
  }
}

output "database" {
  value = aws_glue_catalog_database.history.name
}

output "workgroup" {
  value = aws_athena_workgroup.history.name
}

