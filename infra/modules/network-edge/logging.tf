variable "audit_bucket_arn" { type = string }

resource "aws_cloudwatch_log_delivery_source" "cloudfront" {
  provider     = aws.us_east_1
  name         = "${var.name}-edge"
  log_type     = "ACCESS_LOGS"
  resource_arn = aws_cloudfront_distribution.portal.arn
}
resource "aws_cloudwatch_log_delivery_destination" "cloudfront" {
  provider      = aws.us_east_1
  name          = "${var.name}-edge"
  output_format = "json"
  delivery_destination_configuration {
    destination_resource_arn = var.audit_bucket_arn
  }
}
resource "aws_cloudwatch_log_delivery" "cloudfront" {
  provider                 = aws.us_east_1
  delivery_source_name     = aws_cloudwatch_log_delivery_source.cloudfront.name
  delivery_destination_arn = aws_cloudwatch_log_delivery_destination.cloudfront.arn
  record_fields            = ["date", "time", "c-ip", "cs-method", "cs-uri-stem", "sc-status", "time-taken"]
}
