variable "name" {
  type = string
}

variable "account_id" {
  type = string
}

variable "kms_arn" {
  type = string
}

resource "aws_s3_bucket" "frontend" {
  bucket = "${var.name}-frontend-${var.account_id}"
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_arn
    }

    bucket_key_enabled = true
  }
}

output "bucket" {
  value = aws_s3_bucket.frontend.bucket
}

output "arn" {
  value = aws_s3_bucket.frontend.arn
}

output "domain" {
  value = aws_s3_bucket.frontend.bucket_regional_domain_name
}

