output "raw_landing_bucket_name" {
  value = aws_s3_bucket.raw_landing.bucket
}

output "raw_landing_bucket_arn" {
  value = aws_s3_bucket.raw_landing.arn
}

output "dead_letter_bucket_name" {
  value = aws_s3_bucket.dead_letter.bucket
}

output "dead_letter_bucket_arn" {
  value = aws_s3_bucket.dead_letter.arn
}
