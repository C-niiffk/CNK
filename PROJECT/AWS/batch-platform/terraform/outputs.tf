output "url" {
  value = "https://${var.domain_name}"
}
output "cluster_name" {
  value = aws_ecs_cluster.main.name
}
output "db_identifier" {
  value = aws_db_instance.oracle.identifier
}
output "ecr_repositories" {
  value = {for k, r in aws_ecr_repository.service: k => r.repository_url}
}
output "runtime_secret_arn" {
  value = aws_secretsmanager_secret.runtime.arn
}
output "init_task_definition" {
  value = aws_ecs_task_definition.init.arn
}
output "init_network_configuration" {
  value = { awsvpcConfiguration = { subnets = aws_subnet.private[*].id, securityGroups = [aws_security_group.tier["init"].id], assignPublicIp = "DISABLED" } }
}
output "target_group_arns" {
  value = { for k, g in aws_lb_target_group.main : k => g.arn }
}
output "service_names" {
  value = concat([for s in aws_ecs_service.core : s.name], [for s in aws_ecs_service.app : s.name])
}
output "log_group_prefix" {
  value = "/ecs/${local.name}"
}
output "alb_log_bucket" {
  value = aws_s3_bucket.alb_logs.id
}
output "rds_master_secret_arn" {
  value = aws_db_instance.oracle.master_user_secret[0].secret_arn
}
