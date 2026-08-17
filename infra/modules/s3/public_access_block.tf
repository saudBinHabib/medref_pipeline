resource "aws_s3_bucket_public_access_block" "raw_landing" {
  bucket = aws_s3_bucket.raw_landing.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_public_access_block" "dead_letter" {
  bucket = aws_s3_bucket.dead_letter.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
