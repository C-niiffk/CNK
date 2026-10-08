resource "aws_security_group" "tier" {
  for_each = toset(["public-alb", "internal-lb", "ec2-app", "elasticsearch", "database", "redis"])
  name     = "${local.name}-${each.key}"
  vpc_id   = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "out" {
  for_each          = aws_security_group.tier
  security_group_id = each.value.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "public" {
  for_each = {
    for i, c in var.allowed_cidrs : tostring(i) => c
  }
  security_group_id = aws_security_group.tier["public-alb"].id
  ip_protocol       = "tcp"
  from_port         = var.certificate_arn == "" ? 80 : 443
  to_port           = var.certificate_arn == "" ? 80 : 443
  cidr_ipv4         = each.value
}

resource "aws_vpc_security_group_ingress_rule" "internal" {
  for_each          = toset(["8081", "8082", "8083", "8084", "8848", "9848", "11800", "5044"])
  security_group_id = aws_security_group.tier["internal-lb"].id
  ip_protocol       = "tcp"
  from_port         = tonumber(each.key)
  to_port           = tonumber(each.key)
  cidr_ipv4         = var.vpc_cidr
}

locals {
  eks_sg = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  rules = {
    public_pods = {
      target = local.eks_sg, source = aws_security_group.tier["public-alb"].id, from = 8080, to = 8080
    }
    lb_pods_8080 = {
      target = local.eks_sg, source = aws_security_group.tier["internal-lb"].id, from = 8080, to = 8080
    }
    lb_pods_8848 = {
      target = local.eks_sg, source = aws_security_group.tier["internal-lb"].id, from = 8848, to = 8848
    }
    lb_pods_9848 = {
      target = local.eks_sg, source = aws_security_group.tier["internal-lb"].id, from = 9848, to = 9848
    }
    lb_pods_11800 = {
      target = local.eks_sg, source = aws_security_group.tier["internal-lb"].id, from = 11800, to = 11800
    }
    lb_pods_5044 = {
      target = local.eks_sg, source = aws_security_group.tier["internal-lb"].id, from = 5044, to = 5044
    }
    lb_to_apps = {
      target = aws_security_group.tier["ec2-app"].id, source = aws_security_group.tier["internal-lb"].id, from = 8080, to = 8080
    }
    metrics = {
      target = aws_security_group.tier["ec2-app"].id, source = local.eks_sg, from = 9100, to = 9100
    }
    app_metrics = {
      target = aws_security_group.tier["ec2-app"].id, source = local.eks_sg, from = 8080, to = 8080
    }
    es_http = {
      target = aws_security_group.tier["elasticsearch"].id, source = local.eks_sg, from = 9200, to = 9200
    }
    oracle = {
      target = aws_security_group.tier["database"].id, source = local.eks_sg, from = 1521, to = 1521
    }
    app2_oracle = {
      target = aws_security_group.tier["database"].id, source = aws_security_group.tier["ec2-app"].id, from = 1521, to = 1521
    }
    redis = {
      target = aws_security_group.tier["redis"].id, source = local.eks_sg, from = 6379, to = 6379
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "tiers" {
  for_each                     = local.rules
  security_group_id            = each.value.target
  referenced_security_group_id = each.value.source
  ip_protocol                  = "tcp"
  from_port                    = each.value.from
  to_port                      = each.value.to
}
