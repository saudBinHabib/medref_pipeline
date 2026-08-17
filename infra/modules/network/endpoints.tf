# S3 Gateway endpoint: free, route-table-based, no ENI/hourly cost. Lets the
# pipeline's traffic to the raw-landing/dead-letter buckets stay off the
# public route without needing a paid interface endpoint or NAT gateway.
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = [aws_route_table.public.id]

  tags = {
    Name = "${var.project}-s3-endpoint"
  }
}

data "aws_region" "current" {}
