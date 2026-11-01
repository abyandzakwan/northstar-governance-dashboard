resource "aws_glue_catalog_table" "events" {
  name          = "events"
  database_name = aws_glue_catalog_database.history.name
  table_type    = "EXTERNAL_TABLE"
  parameters = {
    EXTERNAL                    = "TRUE"
    "projection.enabled"        = "true"
    "projection.dt.type"        = "date"
    "projection.dt.range"       = "2026-01-01,NOW"
    "projection.dt.format"      = "yyyy-MM-dd"
    "storage.location.template" = "s3://${aws_s3_bucket.data["history"].id}/events/dt=$${dt}/"
  }
  partition_keys {
    name = "dt"
    type = "string"
  }
  storage_descriptor {
    location      = "s3://${aws_s3_bucket.data["history"].id}/events/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    columns {
      name = "payload"
      type = "string"
    }
    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
      parameters            = { "field.delim" = "\u0001" }
    }
  }
}
