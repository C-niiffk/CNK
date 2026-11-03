# Optional CI/CD resources; one root module and the platform Terraform state.
# CodeBuild creates one short-lived GitHub runner per deployment job inside this VPC.
resource "aws_eks_access_entry" "cicd" {
  count         = var.enable_cicd ? 1 : 0
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = local.cicd_deploy_role_arn
  lifecycle {
    precondition {
      condition     = local.cicd_deploy_role_arn != "" && local.cicd_artifact_bucket != "" && var.cicd_repository != "" && var.cicd_connection_arn != "" && var.cicd_state_bucket != "" && var.cicd_state_key != ""
      error_message = "Set the CD role, artifact bucket, repository and AVAILABLE CodeConnections ARN."
    }
  }
}
resource "aws_eks_access_policy_association" "cicd" {
  count         = var.enable_cicd ? 1 : 0
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = local.cicd_deploy_role_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
  depends_on = [aws_eks_access_entry.cicd]
}
resource "aws_security_group" "cicd_runner" {
  count       = var.enable_cicd ? 1 : 0
  name        = "${local.name}-cicd-runner"
  description = "Ephemeral CodeBuild runner; outbound only"
  vpc_id      = aws_vpc.main.id
}
resource "aws_vpc_security_group_egress_rule" "cicd_runner" {
  count             = var.enable_cicd ? 1 : 0
  security_group_id = aws_security_group.cicd_runner[0].id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "cicd_eks" {
  count                        = var.enable_cicd ? 1 : 0
  security_group_id            = aws_eks_cluster.main.vpc_config[0].cluster_security_group_id
  referenced_security_group_id = aws_security_group.cicd_runner[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_iam_role" "cicd_runner" {
  count = var.enable_cicd ? 1 : 0
  name  = "${local.name}-codebuild-runner"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "codebuild.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_cloudwatch_log_group" "cicd_runner" {
  count             = var.enable_cicd ? 1 : 0
  name              = "/aws/codebuild/${local.name}-deploy"
  retention_in_days = 14
}
resource "aws_iam_role_policy" "cicd_runner" {
  count = var.enable_cicd ? 1 : 0
  role  = aws_iam_role.cicd_runner[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.cicd_runner[0].arn}:*" },
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterface", "ec2:DescribeDhcpOptions", "ec2:DescribeNetworkInterfaces", "ec2:DeleteNetworkInterface", "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups", "ec2:DescribeVpcs"], Resource = "*" },
    { Effect = "Allow", Action = ["ec2:CreateNetworkInterfacePermission"], Resource = "arn:aws:ec2:${var.region}:*:network-interface/*", Condition = { StringEquals = { "ec2:AuthorizedService" = "codebuild.amazonaws.com" }, ArnEquals = { "ec2:Subnet" = [for s in aws_subnet.private : s.arn] } } },
    { Effect = "Allow", Action = ["codeconnections:GetConnectionToken", "codeconnections:GetConnection", "codeconnections:UseConnection", "codestar-connections:GetConnectionToken", "codestar-connections:GetConnection", "codestar-connections:UseConnection"], Resource = var.cicd_connection_arn }
  ] })
}
resource "aws_codebuild_project" "deploy" {
  count          = var.enable_cicd ? 1 : 0
  name           = "${local.name}-deploy"
  description    = "Ephemeral GitHub Actions application deployment runner"
  service_role   = aws_iam_role.cicd_runner[0].arn
  build_timeout  = 90
  queued_timeout = 30
  artifacts { type = "NO_ARTIFACTS" }
  environment {
    compute_type    = "BUILD_GENERAL1_SMALL"
    image           = "aws/codebuild/standard:7.0"
    type            = "LINUX_CONTAINER"
    privileged_mode = false
  }
  source {
    type     = "GITHUB"
    location = "https://github.com/${var.cicd_repository}.git"
    auth {
      type     = "CODECONNECTIONS"
      resource = var.cicd_connection_arn
    }
  }
  vpc_config {
    vpc_id             = aws_vpc.main.id
    subnets            = aws_subnet.private[*].id
    security_group_ids = [aws_security_group.cicd_runner[0].id]
  }
  logs_config {
    cloudwatch_logs { group_name = aws_cloudwatch_log_group.cicd_runner[0].name }
  }
  depends_on = [aws_iam_role_policy.cicd_runner, aws_route_table_association.private]
}
resource "aws_codebuild_webhook" "deploy" {
  count        = var.enable_cicd && var.cicd_enable_webhook ? 1 : 0
  project_name = aws_codebuild_project.deploy[0].name
  build_type   = "BUILD"
  filter_group {
    filter {
      type    = "EVENT"
      pattern = "WORKFLOW_JOB_QUEUED"
    }
    filter {
      type    = "WORKFLOW_NAME"
      pattern = "^Management Platform$"
    }
  }
}
resource "aws_iam_role_policy" "app2_cd_artifacts" {
  count = var.enable_cicd ? 1 : 0
  role  = aws_iam_role.ec2.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${local.cicd_artifact_bucket}/bundles/*" }
  ] })
}

locals {
  cicd_deploy_role_arn = var.enable_cicd ? aws_iam_role.cicd_github["deploy"].arn : ""
  cicd_artifact_bucket = var.enable_cicd ? aws_s3_bucket.cicd_artifacts[0].id : ""
}

resource "aws_s3_bucket" "cicd_artifacts" {
  count         = var.enable_cicd ? 1 : 0
  bucket        = var.cicd_artifact_bucket_name != "" ? var.cicd_artifact_bucket_name : "${local.name}-cicd-${data.aws_caller_identity.cicd.account_id}-${var.region}"
  force_destroy = false
}
resource "aws_s3_bucket_versioning" "cicd_artifacts" {
  count  = var.enable_cicd ? 1 : 0
  bucket = aws_s3_bucket.cicd_artifacts[0].id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "cicd_artifacts" {
  count  = var.enable_cicd ? 1 : 0
  bucket = aws_s3_bucket.cicd_artifacts[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_public_access_block" "cicd_artifacts" {
  count                   = var.enable_cicd ? 1 : 0
  bucket                  = aws_s3_bucket.cicd_artifacts[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_policy" "cicd_artifacts" {
  count  = var.enable_cicd ? 1 : 0
  bucket = aws_s3_bucket.cicd_artifacts[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "DenyPlainHTTP", Effect = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [aws_s3_bucket.cicd_artifacts[0].arn, "${aws_s3_bucket.cicd_artifacts[0].arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
