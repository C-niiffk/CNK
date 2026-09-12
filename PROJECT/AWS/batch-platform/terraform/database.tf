resource "aws_db_subnet_group" "main" {
  subnet_ids = aws_subnet.database[*].id
  name = local.name
}
resource "aws_db_option_group" "oracle" {
  name_prefix = "${local.name}-"
  engine_name = "oracle-se2"
  major_engine_version = "19"
  option {
    option_name = "NATIVE_NETWORK_ENCRYPTION"
    option_settings {
      name  = "SQLNET.ENCRYPTION_SERVER"
      value = "REQUIRED"
    }
    option_settings {
      name  = "SQLNET.ENCRYPTION_TYPES_SERVER"
      value = "AES256"
    }
    option_settings {
      name  = "SQLNET.CRYPTO_CHECKSUM_SERVER"
      value = "REQUIRED"
    }
    option_settings {
      name  = "SQLNET.CRYPTO_CHECKSUM_TYPES_SERVER"
      value = "SHA256"
    }
  }
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_db_instance" "oracle" {
  identifier = local.name
  engine = "oracle-se2"
  engine_version = var.oracle_engine_version
  license_model = "license-included"
  instance_class = var.db_instance_class
  allocated_storage = 100
  max_allocated_storage = 300
  storage_type = "gp3"
  storage_encrypted = true
  db_name = "BATCHDB"
  username = "batchadmin"
  manage_master_user_password = true
  multi_az = false
  publicly_accessible = false
  port = 1521
  db_subnet_group_name = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.tier["database"].id]
  option_group_name = aws_db_option_group.oracle.name
  backup_retention_period = 7
  backup_window = "18:00-19:00"
  maintenance_window = "sun:19:30-sun:20:30"
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot = true
  deletion_protection = var.protect_data
  skip_final_snapshot = false
  final_snapshot_identifier = var.final_snapshot_identifier
  enabled_cloudwatch_logs_exports = ["alert", "listener"]
  apply_immediately = false
}
resource "aws_secretsmanager_secret" "runtime" {
  name = "${local.name}/runtime"
  description = "JSON keys: owner_password, reader_password, internal_token, ui_password; values supplied out-of-band"
  recovery_window_in_days = 7
}
locals {
  db_url = "jdbc:oracle:thin:@//${aws_db_instance.oracle.address}:1521/BATCHDB"
}
