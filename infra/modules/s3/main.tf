# Versioning is off for both buckets -- this is a demo, not a system of
# record; keeping it off avoids storage cost creep from repeated pipeline
# runs writing/overwriting the same key names.
resource "aws_s3_bucket" "raw_landing" {
  bucket = "${var.project}-raw-landing-${var.account_id}"
}

resource "aws_s3_bucket" "dead_letter" {
  bucket = "${var.project}-dead-letter-${var.account_id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "raw_landing" {
  bucket = aws_s3_bucket.raw_landing.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "dead_letter" {
  bucket = aws_s3_bucket.dead_letter.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
