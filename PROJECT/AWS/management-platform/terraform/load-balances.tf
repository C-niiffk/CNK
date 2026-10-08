resource "aws_lb" "public" {
  name                       = substr("${local.name}-public", 0, 32)
  load_balancer_type         = "application"
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.tier["public-alb"].id]
  drop_invalid_header_fields = true
}

resource "aws_lb" "internal" {
  name               = substr("${local.name}-internal", 0, 32)
  internal           = true
  load_balancer_type = "application"
  subnets            = aws_subnet.private[*].id
  security_groups    = [aws_security_group.tier["internal-lb"].id]
}

resource "aws_lb" "agents" {
  name                             = substr("${local.name}-agents", 0, 32)
  internal                         = true
  load_balancer_type               = "network"
  subnets                          = aws_subnet.private[*].id
  security_groups                  = [aws_security_group.tier["internal-lb"].id]
  enable_cross_zone_load_balancing = true
}

locals {
  http_targets = {
    gateway-a = {
      site = "a", kind = "ip", port = 8080
    },
    gateway-b = {
      site = "b", kind = "ip", port = 8080
    },
    management-a = {
      site = "a", kind = "ip", port = 8080
    },
    management-b = {
      site = "b", kind = "ip", port = 8080
    },
    internal-a = {
      site = "a", kind = "ip", port = 8080
    },
    internal-b = {
      site = "b", kind = "ip", port = 8080
    },
    app2 = { site = "shared", kind = "instance", port = 8080 }
  }
  tcp_targets = {
    oap = 11800, logstash = 5044
  }
}

resource "aws_lb_target_group" "http" {
  for_each             = local.http_targets
  name                 = substr("${var.environment}-${each.key}-${substr(sha1(local.name), 0, 6)}", 0, 32)
  vpc_id               = aws_vpc.main.id
  port                 = each.value.port
  protocol             = "HTTP"
  target_type          = each.value.kind
  deregistration_delay = 30
  health_check {
    path                = "/actuator/health/readiness"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_target_group" "tcp" {
  for_each             = local.tcp_targets
  name                 = substr("${var.environment}-${each.key}-${substr(sha1(local.name), 0, 6)}", 0, 32)
  vpc_id               = aws_vpc.main.id
  port                 = each.value
  protocol             = "TCP"
  target_type          = "ip"
  deregistration_delay = 30
  health_check {
    protocol = "TCP"
  }
}

resource "aws_lb_listener" "public" {
  load_balancer_arn = aws_lb.public.arn
  port              = var.certificate_arn == "" ? 80 : 443
  protocol          = var.certificate_arn == "" ? "HTTP" : "HTTPS"
  certificate_arn   = var.certificate_arn == "" ? null : var.certificate_arn
  ssl_policy        = var.certificate_arn == "" ? null : "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Not found"
    }
  }
}

resource "aws_lb_listener_rule" "services" {
  for_each = {
    app1 = 10, app2 = 20, management = 100
  }
  listener_arn = aws_lb_listener.public.arn
  priority     = each.value
  condition {
    path_pattern {
      values = each.key == "management" ? ["/*"] : ["/api/${each.key}/*"]
    }
  }

  action {
    type = "forward"
    forward {
      target_group {
        arn    = aws_lb_target_group.http[each.key == "management" ? "management-a" : "gateway-a"].arn
        weight = 50
      }
      target_group {
        arn    = aws_lb_target_group.http[each.key == "management" ? "management-b" : "gateway-b"].arn
        weight = 50
      }
      stickiness {
        enabled  = false
        duration = 1
      }
    }
  }
  lifecycle {
    ignore_changes = [action]
  }
}

resource "aws_lb_listener" "internal" {
  for_each = {
    internal-a = 8081, internal-b = 8082, app2-a = 8083, app2-b = 8084
  }
  load_balancer_arn = aws_lb.internal.arn
  port              = each.value
  protocol          = "HTTP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.http[startswith(each.key, "app2-") ? "app2" : each.key].arn
  }
}

resource "aws_lb_listener" "agents" {
  for_each          = local.tcp_targets
  load_balancer_arn = aws_lb.agents.arn
  port              = each.value
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.tcp[each.key].arn
  }
}

resource "aws_lb_listener" "health_anchor" {
  load_balancer_arn = aws_lb.public.arn
  port              = 8099
  protocol          = "HTTP"
  default_action {
    type = "forward"
    forward {
      dynamic "target_group" {
        for_each = toset(["gateway-a", "gateway-b", "management-a", "management-b"])
        content {
          arn    = aws_lb_target_group.http[target_group.value].arn
          weight = 1
        }
      }
    }
  }
}

resource "aws_lb_listener_rule" "hide_management_endpoints" {
  listener_arn = aws_lb_listener.public.arn
  priority     = 1
  condition {
    path_pattern { values = ["/actuator", "/actuator/*"] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
    }
  }
}

resource "aws_lb" "discovery" {
  for_each                         = local.sites
  name                             = substr("${local.name}-grdc-${each.key}", 0, 32)
  internal                         = true
  load_balancer_type               = "network"
  subnets                          = aws_subnet.private[*].id
  security_groups                  = [aws_security_group.tier["internal-lb"].id]
  enable_cross_zone_load_balancing = true
}
locals {
  # Site-specific Nacos plus Logstash, matching the architecture diagram.
  discovery_ports = { http = 8848, grpc = 9848, logstash = 5044 }
  discovery_targets = merge([for site in keys(local.sites) : {
    for kind, port in local.discovery_ports : "${site}-${kind}" => { site = site, port = port }
  }]...)
}

resource "aws_lb_target_group" "discovery" {
  for_each             = local.discovery_targets
  name                 = substr("${var.environment}-dis-${each.key}-${substr(sha1(local.name), 0, 6)}", 0, 32)
  vpc_id               = aws_vpc.main.id
  target_type          = "ip"
  port                 = each.value.port
  protocol             = "TCP"
  deregistration_delay = 10
  health_check { protocol = "TCP" }
}

resource "aws_lb_listener" "discovery" {
  for_each          = local.discovery_targets
  load_balancer_arn = aws_lb.discovery[each.value.site].arn
  port              = each.value.port
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.discovery[each.key].arn
  }
}
