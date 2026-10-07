locals {
  name = "${var.project}-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
  sites = {
    a = 0
    b = 1
  }
}
