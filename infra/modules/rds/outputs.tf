output "db_instance_address" {
  value = aws_db_instance.this.address
}

output "db_instance_port" {
  value = aws_db_instance.this.port
}

output "db_name" {
  value = aws_db_instance.this.db_name
}

output "db_username" {
  value = aws_db_instance.this.username
}

output "db_password" {
  value     = random_password.db.result
  sensitive = true
}
