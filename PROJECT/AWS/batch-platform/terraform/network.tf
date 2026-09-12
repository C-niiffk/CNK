locals {
  name = "${var.project}-${var.environment}"
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
  modules = toset(["frontend-service", "backend-service", "agent-service", "application-service"])
}
resource "aws_vpc" "main" {
  cidr_block = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support = true
  tags = {Name = local.name}
}
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}
resource "aws_subnet" "public" {
  count = 2
  vpc_id = aws_vpc.main.id
  cidr_block = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone = local.azs[count.index]
  tags = {Name = "${local.name}-public-${count.index}"}
}
resource "aws_subnet" "private" {
  count = 2
  vpc_id = aws_vpc.main.id
  cidr_block = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  availability_zone = local.azs[count.index]
  tags = {Name = "${local.name}-tasks-${count.index}"}
}
resource "aws_subnet" "database" {
  count = 2
  vpc_id = aws_vpc.main.id
  cidr_block = cidrsubnet(var.vpc_cidr, 8, 20 + count.index)
  availability_zone = local.azs[count.index]
  tags = {Name = "${local.name}-database-${count.index}"}
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
resource "aws_route_table_association" "public" {
  count = 2
  subnet_id = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_eip" "nat" {
  count = 2
  domain = "vpc"
}
resource "aws_nat_gateway" "main" {
  count = 2
  allocation_id = aws_eip.nat[count.index].id
  subnet_id = aws_subnet.public[count.index].id
  depends_on = [aws_internet_gateway.main]
}
resource "aws_route_table" "private" {
  count = 2
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index].id
  }
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}
resource "aws_route_table" "database" {
  vpc_id = aws_vpc.main.id
}
resource "aws_route_table_association" "database" {
  count          = 2
  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.database.id
}
resource "aws_security_group" "tier" {
  for_each = toset(["public-alb", "frontend", "internal-alb", "backend", "app", "database", "init"])
  name = "${local.name}-${each.key}"
  description = "${each.key} traffic boundary"
  vpc_id = aws_vpc.main.id
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each = toset(["frontend", "backend", "app", "init"])
  security_group_id = aws_security_group.tier[each.key].id
  cidr_ipv4 = "0.0.0.0/0"
  ip_protocol = "tcp"
  from_port = 443
  to_port = 443
  description = "ECR, Secrets Manager and CloudWatch via NAT"
}
resource "aws_vpc_security_group_ingress_rule" "public" {
  for_each = {for i, c in var.allowed_cidrs : tostring(i) => c}
  cidr_ipv4 = each.value
  security_group_id = aws_security_group.tier["public-alb"].id
  ip_protocol = "tcp"
  from_port = 443
  to_port = 443
}
locals {
  flows = {
    web = {source = "public-alb", destination = "frontend", port = 8080}
    front_to_lb = {source = "frontend", destination = "internal-alb", port = 8081}
    lb_to_back = {source = "internal-alb", destination = "backend", port = 8081}
    app_to_lb = {source = "app", destination = "internal-alb", port = 8081}
    back_to_agent = {source = "backend", destination = "app", port = 8090}
    front_to_db = {source = "frontend", destination = "database", port = 1521}
    back_to_db = {source = "backend", destination = "database", port = 1521}
    init_to_db = {source = "init", destination = "database", port =1521}
  }
}
resource "aws_vpc_security_group_ingress_rule" "flow" {
  for_each = local.flows
  security_group_id = aws_security_group.tier[each.value.destination].id
  referenced_security_group_id = aws_security_group.tier[each.value.source].id
  ip_protocol = "tcp"
  from_port = each.value.port
  to_port = each.value.port
}

resource "aws_vpc_security_group_egress_rule" "flow" {
  for_each = local.flows
  security_group_id = aws_security_group.tier[each.value.source].id
  referenced_security_group_id = aws_security_group.tier[each.value.destination].id
  ip_protocol = "tcp"
  from_port = each.value.port
  to_port = each.value.port
}



