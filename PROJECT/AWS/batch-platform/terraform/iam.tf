locals {
  task_trust = jsonencode({Version = "2012-10-17", Statement = [{Effect = "Allow", Principal = {Service = "ecs-tasks.amazonaws.com"}, Action = "sts:AssumeRole"}]})
}
resource "aws_iam_role" "execution" {
  for_each = toset(["frontend", "backend", "app", "init"])
  name = "${local.name}-${each.key}-exec"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  for_each = aws_iam_role.execution
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
  role       = each.value.name
}
resource "aws_iam_role_policy" "secrets" {
  for_each = aws_iam_role.execution
  role = each.value.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = each.key == "init" ? [aws_secretsmanager_secret.runtime.arn, aws_db_instance.oracle.master_user_secret[0].secret_arn] : [aws_secretsmanager_secret.runtime.arn] }] })
}
resource "aws_iam_role" "task" {
  assume_role_policy = local.task_trust
  name = "${local.name}-task"
}
