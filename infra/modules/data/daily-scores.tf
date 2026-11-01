resource "aws_glue_catalog_table" "scores" {
  name          = "daily_scores"
  database_name = aws_glue_catalog_database.history.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                        = "TRUE"
    classification                  = "parquet"
    "projection.enabled"            = "true"
    "projection.dt.type"            = "date"
    "projection.dt.range"           = "2026-01-01,NOW"
    "projection.dt.format"          = "yyyy-MM-dd"
    "projection.environment.type"   = "enum"
    "projection.environment.values" = "dev,testing,production,unclassified"
    "storage.location.template"     = "s3://${aws_s3_bucket.data["history"].id}/daily-scores/dt=$${dt}/environment=$${environment}/"
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
    location      = "s3://${aws_s3_bucket.data["history"].id}/daily-scores/"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
    }
    dynamic "columns" {
      for_each = { account_id = "string", owner_team = "string", account_score = "double" }
      content {
        name = columns.key
        type = columns.value
      }
    }
  }
}
