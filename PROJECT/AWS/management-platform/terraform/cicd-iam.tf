# Optional GitHub CI identities; managed in the same root module and state.
data "aws_caller_identity" "cicd" {}
locals {
  cicd_subjects = {
    plan   = "aws-terraform-plan"
    apply  = "aws-terraform-apply"
    build  = "aws-build"
    deploy = "aws-application"
  }
}
resource "aws_iam_openid_connect_provider" "cicd_github" {
  count          = var.enable_cicd && var.cicd_existing_github_oidc_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
locals {
  cicd_github_oidc_arn = var.enable_cicd ? (var.cicd_existing_github_oidc_arn != "" ? var.cicd_existing_github_oidc_arn : aws_iam_openid_connect_provider.cicd_github[0].arn) : ""
}
resource "aws_iam_role" "cicd_github" {
  for_each             = var.enable_cicd ? local.cicd_subjects : {}
  name                 = "${local.name}-github-${each.key}"
  max_session_duration = 7200
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Federated = local.cicd_github_oidc_arn }, Action = "sts:AssumeRoleWithWebIdentity",
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = format("repo:%s:environment:%s", var.cicd_oidc_repository_claim != "" ? var.cicd_oidc_repository_claim : var.cicd_repository, each.value)
    } }
  }] })
}
resource "aws_iam_role_policy_attachment" "cicd_plan_read" {
  count      = var.enable_cicd ? 1 : 0
  role       = aws_iam_role.cicd_github["plan"].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}
# Privileged lab provisioner: can create IAM, VPC, EKS, RDS and this optional CD infrastructure.
# The deployment guide requires a protected environment and trusted main branch.
resource "aws_iam_role_policy_attachment" "cicd_apply_admin" {
  count      = var.enable_cicd ? 1 : 0
  role       = aws_iam_role.cicd_github["apply"].name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
resource "aws_iam_role_policy" "cicd_state" {
  for_each = var.enable_cicd ? toset(["plan", "deploy"]) : toset([])
  role     = aws_iam_role.cicd_github[each.key].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = "arn:aws:s3:::${var.cicd_state_bucket}" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.cicd_state_bucket}/${var.cicd_state_key}" },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"], Resource = "arn:aws:s3:::${var.cicd_state_bucket}/${var.cicd_state_key}.tflock" }
  ] })
}
resource "aws_iam_role_policy" "cicd_build" {
  count = var.enable_cicd ? 1 : 0
  role  = aws_iam_role.cicd_github["build"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"], Resource = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.cicd.account_id}:repository/${local.name}/*" },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.cicd_artifacts[0].arn}/candidates/*" }
  ] })
}
resource "aws_iam_role_policy" "cicd_plans" {
  count = var.enable_cicd ? 1 : 0
  role  = aws_iam_role.cicd_github["plan"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "${aws_s3_bucket.cicd_artifacts[0].arn}/plans/*" },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.cicd.account_id}:secret:${local.name}-redis-*" }
  ] })
}
resource "aws_iam_role_policy" "cicd_deploy" {
  count = var.enable_cicd ? 1 : 0
  role  = aws_iam_role.cicd_github["deploy"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["eks:DescribeCluster", "ec2:DescribeVpcs", "ec2:DescribeInstances", "elasticloadbalancing:DescribeTargetHealth"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = "arn:aws:ecr:${var.region}:${data.aws_caller_identity.cicd.account_id}:repository/${local.name}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketLocation"], Resource = aws_s3_bucket.cicd_artifacts[0].arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.cicd_artifacts[0].arn}/*" },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.cicd_artifacts[0].arn}/releases/*", "${aws_s3_bucket.cicd_artifacts[0].arn}/bundles/*"] },
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = ["arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.cicd.account_id}:secret:${local.name}-runtime-*", "arn:aws:secretsmanager:${var.region}:${data.aws_caller_identity.cicd.account_id}:secret:${local.name}-redis-*"] },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ssm:${var.region}::document/AWS-RunShellScript" },
    { Effect = "Allow", Action = ["ssm:SendCommand"], Resource = "arn:aws:ec2:${var.region}:${data.aws_caller_identity.cicd.account_id}:instance/*", Condition = { StringEquals = { "ssm:resourceTag/Project" = var.project, "ssm:resourceTag/Environment" = var.environment, "ssm:resourceTag/Role" = "application" } } },
    { Effect = "Allow", Action = ["ssm:GetCommandInvocation", "ssm:ListCommands", "ssm:ListCommandInvocations", "ssm:DescribeInstanceInformation"], Resource = "*" }
  ] })
}
