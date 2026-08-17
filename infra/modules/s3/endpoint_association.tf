# The S3 gateway endpoint itself is created by the network module (it needs
# to exist before the public route table is fully wired up); this module
# associates it with the public route table so pipeline traffic to these two
# buckets from the public-subnet ECS tasks stays on AWS's network.
resource "aws_vpc_endpoint_route_table_association" "s3_public" {
  vpc_endpoint_id = var.s3_endpoint_id
  route_table_id  = var.public_route_table_id
}
