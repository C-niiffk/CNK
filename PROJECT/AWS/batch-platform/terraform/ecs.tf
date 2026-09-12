resource "aws_ecr_repository" "service" {
  for_each = local.modules
  name = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}
resource "aws_cloudwatch_log_group" "service" {
  for_each = toset(["frontend", "backend", "app1", "app2", "init"])
  name = "/ecs/${local.name}/${each.key}"
  retention_in_days = 30
}
resource "aws_ecs_cluster" "main" {
  name = local.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
resource "aws_service_discovery_http_namespace" "main" {
  name = "${local.name}.internal"
}
locals {
  ports = {frontend = 8080, backend = 8081}
  runtime_secret = aws_secretsmanager_secret.runtime.arn
  logs = { for k, g in aws_cloudwatch_log_group.service : k => {
    logDriver = "awslogs"
    options   = { "awslogs-group" = g.name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "ecs" }
  } }
  common_env    = [{ name = "DB_URL", value = local.db_url }]
  common_secret = [{ name = "INTERNAL_TOKEN", valueFrom = "${local.runtime_secret}:internal_token::" }]
}
resource "aws_ecs_task_definition" "core" {
  for_each = local.ports
  family = "${local.name}-${each.key}"
  network_mode = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu = 256
  memory = 512
  execution_role_arn = aws_iam_role.execution[each.key].arn
  task_role_arn = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture = "X86_64"
  }
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name = each.key
    image = "${aws_ecr_repository.service["${each.key}-service"].repository_url}:${var.image_tag}"
    essential = true
    user = "10001:10001"
    readonlyRootFilesystem = true
    mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    cpu = 128
    memory = 384
    stopTimeout = 120
    portMappings = [{ name = each.key, containerPort = each.value, protocol = "tcp", appProtocol = "http" }]
    logConfiguration = local.logs[each.key]
    environment = concat(local.common_env, [
      { name = "PORT", value = tostring(each.value) },
      { name = "DB_USER", value = each.key == "frontend" ? "BATCH_READER" : "BATCH_OWNER" }
    ], each.key == "frontend" ? [
      { name = "BACKEND_URL", value = "http://${aws_lb.main["internal"].dns_name}:8081" },
      { name = "UI_USER", value = "operator" },
      { name = "COOKIE_SECURE", value = "true" }
    ] : [
      { name = "AGENT_APP1_URL", value = "http://app1-agent:8090" },
      { name = "AGENT_APP2_URL", value = "http://app2-agent:8090" }
    ])
    secrets = concat(local.common_secret, [{ name = "DB_PASSWORD", valueFrom = "${local.runtime_secret}:${each.key == "frontend" ? "reader_password" : "owner_password"}::" }], each.key == "frontend" ? [{ name = "UI_PASSWORD", valueFrom = "${local.runtime_secret}:ui_password::" }] : [])
    healthCheck = { command = ["CMD-SHELL", "curl -fsS http://127.0.0.1:${each.value}/health/liveness || exit 1"], interval = 30, timeout = 5, retries = 3, startPeriod = 60 }
  }])
}
resource "aws_ecs_task_definition" "app" {
  for_each = toset(["app1", "app2"])
  family = "${local.name}-${each.key}"
  network_mode = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu = 512
  memory = 1024
  execution_role_arn = aws_iam_role.execution["app"].arn
  task_role_arn = aws_iam_role.task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture = "X86_64"
  }
  volume {
    name = "scratch"
  }
  volume { name = "application-tmp" }
  volume { name = "agent-tmp" }
  container_definitions = jsonencode([
    {
      name                   = "application"
      image                  = "${aws_ecr_repository.service["application-service"].repository_url}:${var.image_tag}"
      essential              = true
      user                   = "10001:10001"
      readonlyRootFilesystem = true
      mountPoints = [
        { sourceVolume = "application-tmp", containerPath = "/tmp", readOnly = false },
        { sourceVolume = "scratch", containerPath = "/work", readOnly = false }
      ]
      cpu                    = 256
      memory                 = 384
      stopTimeout            = 120
      portMappings           = [{ name = "application", containerPort = 8082, protocol = "tcp" }]
      logConfiguration       = local.logs[each.key]
      environment            = [{ name = "PORT", value = "8082" }, { name = "BIND_ADDRESS", value = "127.0.0.1" }, { name = "APP_ID", value = each.key }]
      secrets                = local.common_secret
      healthCheck            = { command = ["CMD-SHELL", "curl -fsS http://127.0.0.1:8082/health/liveness || exit 1"], interval = 15, timeout = 5, retries = 3, startPeriod = 60 }
    },
    {
      name                   = "agent"
      image                  = "${aws_ecr_repository.service["agent-service"].repository_url}:${var.image_tag}"
      essential              = true
      user                   = "10001:10001"
      readonlyRootFilesystem = true
      cpu                    = 256
      memory                 = 512
      stopTimeout            = 120
      dependsOn              = [{ containerName = "application", condition = "HEALTHY" }]
      portMappings           = [{ name = "agent", containerPort = 8090, protocol = "tcp", appProtocol = "http" }]
      mountPoints            = [{ sourceVolume = "scratch", containerPath = "/work", readOnly = false }, { sourceVolume = "agent-tmp", containerPath = "/tmp", readOnly = false }]
      logConfiguration       = local.logs[each.key]
      environment            = [{ name = "PORT", value = "8090" }, { name = "APP_ID", value = each.key }, { name = "APPLICATION_URL", value = "http://127.0.0.1:8082" }, { name = "CLAIM_URL", value = "http://${aws_lb.main["internal"].dns_name}:8081/execution-claims" }]
      secrets                = local.common_secret
      healthCheck            = { command = ["CMD-SHELL", "curl -fsS http://127.0.0.1:8090/health/liveness || exit 1"], interval = 30, timeout = 5, retries = 3, startPeriod = 60 }
    }
  ])
}
resource "aws_ecs_task_definition" "init" {
  family                   = "${local.name}-init"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution["init"].arn
  task_role_arn            = aws_iam_role.task.arn
  volume { name = "tmp" }
  container_definitions = jsonencode([{
    name                   = "init"
    image                  = "${aws_ecr_repository.service["backend-service"].repository_url}:${var.image_tag}"
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    mountPoints            = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]
    command                = ["init-db"]
    logConfiguration       = local.logs["init"]
    environment            = [{ name = "DB_URL", value = local.db_url }, { name = "DB_USER", value = "batchadmin" }]
    secrets = [
      { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.oracle.master_user_secret[0].secret_arn}:password::" },
      { name = "OWNER_PASSWORD", valueFrom = "${local.runtime_secret}:owner_password::" },
      { name = "READER_PASSWORD", valueFrom = "${local.runtime_secret}:reader_password::" }
    ]
  }])
}
resource "aws_ecs_service" "app" {
  for_each = var.deploy_services ? toset(["app1", "app2"]) : toset([])
  name = "${local.name}-${each.key}"
  cluster = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app[each.key].arn
  desired_count = 1
  launch_type = "FARGATE"
  platform_version = "1.4.0"
  availability_zone_rebalancing = "ENABLED"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets = aws_subnet.private[*].id
    security_groups = [aws_security_group.tier["app"].id]
    assign_public_ip = false
  }
  service_connect_configuration {
    enabled = true
    namespace = aws_service_discovery_http_namespace.main.arn
    log_configuration {
      log_driver = "awslogs"
      options = {
        "awslogs-group"  = aws_cloudwatch_log_group.service[each.key].name
        "awslogs-region" = var.region
        "awslogs-stream-prefix" = "ecs"
      }
    }
    service {
      port_name = "agent"
      discovery_name = "${each.key}-agent"
      client_alias {
        port = 8090
        dns_name = "${each.key}-agent"
      }
      timeout {
        per_request_timeout_seconds = 100
        idle_timeout_seconds = 120
      }
    }
  }
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.secrets, aws_route_table_association.private]
}
resource "aws_ecs_service" "core" {
  for_each                           = var.deploy_services ? local.ports : {}
  name                               = "${local.name}-${each.key}"
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.core[each.key].arn
  desired_count                      = 1
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  availability_zone_rebalancing      = "ENABLED"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 60
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.tier[each.key].id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.main[each.key].arn
    container_name   = each.key
    container_port   = each.value
  }
  dynamic "service_connect_configuration" {
    for_each = each.key == "backend" ? [1] : []
    content {
      enabled   = true
      namespace = aws_service_discovery_http_namespace.main.arn
      log_configuration {
        log_driver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.service[each.key].name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    }
  }
  depends_on = [aws_lb_listener.public, aws_lb_listener.internal, aws_ecs_service.app, aws_iam_role_policy_attachment.execution, aws_iam_role_policy.secrets, aws_route_table_association.private]
}
