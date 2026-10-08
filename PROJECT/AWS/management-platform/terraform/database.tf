resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "aws_db_subnet_group" "main" {
  subnet_ids = aws_subnet.database[*].id
  name       = local.name
}

resource "aws_db_option_group" "oracle" {
  name_prefix          = "${local.name}-"
  engine_name          = "oracle-se2"
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
  for_each                        = toset(["management"])
  identifier                      = "${local.name}-${each.key}"
  engine                          = "oracle-se2"
  engine_version                  = var.oracle_engine_version
  license_model                   = "license-included"
  instance_class                  = var.db_instance_class
  allocated_storage               = 20
  max_allocated_storage           = 50
  storage_type                    = "gp3"
  storage_encrypted               = true
  db_name                         = "MPDB"
  username                        = "platformadmin"
  manage_master_user_password     = true
  multi_az                        = true
  publicly_accessible             = false
  port                            = 1521
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.tier["database"].id]
  option_group_name               = aws_db_option_group.oracle.name
  backup_retention_period         = 7
  backup_window                   = "18:00-19:00"
  maintenance_window              = "sun:19:30-sun:20:30"
  auto_minor_version_upgrade      = true
  copy_tags_to_snapshot           = true
  enabled_cloudwatch_logs_exports = ["alert", "listener"]
  deletion_protection             = var.protect_data
  skip_final_snapshot             = false
  final_snapshot_identifier       = lookup(var.final_snapshot_identifiers, each.key, "${local.name}-${each.key}-final")
  apply_immediately               = false
  lifecycle {
    precondition {
      condition     = startswith(var.oracle_engine_version, "19.")
      error_message = "This Oracle SE2 option group requires an Oracle 19c engine version."
    }
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = local.name
  subnet_ids = aws_subnet.database[*].id
}

resource "aws_elasticache_replication_group" "rate" {
  replication_group_id       = substr("${local.name}-rate", 0, 40)
  description                = "Shared gateway rate counters"
  engine                     = "redis"
  node_type                  = "cache.t3.micro"
  port                       = 6379
  num_cache_clusters         = 2
  automatic_failover_enabled = true
  multi_az_enabled           = true
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result
  subnet_group_name          = aws_elasticache_subnet_group.main.name
  security_group_ids         = [aws_security_group.tier["redis"].id]
  snapshot_retention_limit   = 1
}

resource "aws_dynamodb_table" "routing" {
  name         = "${local.name}-routing-lock"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "lock_id"
  attribute {
    name = "lock_id"
    type = "S"
  }
  point_in_time_recovery {
    enabled = true
  }
  server_side_encryption {
    enabled = true
  }
}
