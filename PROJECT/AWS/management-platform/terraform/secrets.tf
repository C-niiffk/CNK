resource "aws_secretsmanager_secret" "platform" {
  name_prefix             = "${local.name}-runtime-"
  description             = "Application runtime credentials; populate out-of-band after manual SQL initialization."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "redis" {
  name_prefix             = "${local.name}-redis-"
  description             = "Raw Redis AUTH token for Gateway; not a JSON object."
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "redis" {
  secret_id     = aws_secretsmanager_secret.redis.id
  secret_string = random_password.redis.result
}
