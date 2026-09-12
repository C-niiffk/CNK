resource "aws_s3_bucket" "alb_logs" {
  bucket = "${local.name}-alb-${data.aws_caller_identity.current.account_id}-${var.region}"
}
resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule {
    id = "retain-30-days"
    status = "Enabled"
    filter {
      prefix = ""
    }
    expiration {
      days = 30
    }
  }
}
resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = jsonencode({Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }, Action = "s3:PutObject", Resource = "${aws_s3_bucket.alb_logs.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*" },
    { Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.alb_logs.arn, "${aws_s3_bucket.alb_logs.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } }
  ] })
}
resource "aws_lb" "main" {
  for_each = toset(["public", "internal"])
  name = "${local.name}-${each.key == "public" ? "pub" : "int"}"
  internal = each.key == "internal"
  load_balancer_type = "application"
  subnets = each.key == "public" ? aws_subnet.public[*].id : aws_subnet.private[*].id
  security_groups = [aws_security_group.tier["${each.key}-alb"].id]
  enable_deletion_protection = var.protect_data
  drop_invalid_header_fields = true
  idle_timeout = 120
  access_logs {
    bucket = aws_s3_bucket.alb_logs.id
    enabled = true
  }
  depends_on = [aws_s3_bucket_policy.alb_logs]
}
resource "aws_lb_target_group" "main" {
  for_each = {frontend = 8080, backend = 8081}
  name = "${local.name}-${each.key == "frontend" ? "fe" : "be"}"
  vpc_id = aws_vpc.main.id
  protocol = "HTTP"
  port = each.value
  target_type = "ip"
  deregistration_delay = 30
  health_check {
    path = "/health/readiness"
    healthy_threshold = 2
    unhealthy_threshold = 3
    interval = 30
    timeout = 5
    matcher = "200"
  }
}
resource "aws_acm_certificate" "public" {
  domain_name = var.domain_name
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_route53_record" "validation" {
  for_each = { for d in aws_acm_certificate.public.domain_validation_options : d.domain_name => d }
  zone_id  = var.hosted_zone_id
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  records  = [each.value.resource_record_value]
  ttl      = 60
}
resource "aws_acm_certificate_validation" "public" {
  certificate_arn = aws_acm_certificate.public.arn
  validation_record_fqdns = [for r in aws_route53_record.validation: r.fqdn]
}
resource "aws_lb_listener" "public" {
  load_balancer_arn = aws_lb.main["public"].arn
  port = 443
  protocol = "HTTPS"
  ssl_policy = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn = aws_acm_certificate_validation.public.certificate_arn
  default_action {
    type = "forward"
    target_group_arn = aws_lb_target_group.main["frontend"].arn
  }
}
resource "aws_lb_listener" "internal" {
  load_balancer_arn = aws_lb.main["internal"].arn
  port = 8081
  protocol = "HTTP"
  default_action {
    type = "forward"
    target_group_arn = aws_lb_target_group.main["backend"].arn
  }
}
resource "aws_route53_record" "app" {
  name = var.domain_name
  type = "A"
  zone_id = var.hosted_zone_id
  alias {
    evaluate_target_health = true
    name = aws_lb.main["public"].dns_name
    zone_id = aws_lb.main["public"].zone_id
  }
}
