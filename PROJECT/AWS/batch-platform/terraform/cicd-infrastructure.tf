# Bootstrap with the operator identity. Normal Infrastructure CD cannot modify IAM.
locals {
  cicd_repo_subjects = [
    "repo:${var.github_repository}",
    "repo:${local.cicd_parts[0]}@${var.github_owner_id}/${local.cicd_parts[1]}@${var.github_repository_id}"
  ]
  cicd_read_statements = flatten([
    for p in aws_iam_role_policy.cicd : [
      for s in jsondecode(p.policy).Statement : s
      if contains(["TerraformResourceDiscovery", "ReadTerraformIAMResources", "ReadProjectBuckets", "TerraformLock"], s.Sid)
    ]
  ])
}

resource "aws_ssm_parameter" "cicd_revision" {
  count = local.cicd_enabled ? 1 : 0
  name  = "/${local.name}/cicd/revision"
  type  = "String"
  value = "unconfirmed"
  lifecycle { ignore_changes = [value] }
}

# Saved plans are private, encrypted, and automatically expire. They are not public GitHub artifacts.
resource "aws_s3_bucket" "cicd_plans" {
  count         = local.cicd_enabled ? 1 : 0
  bucket        = "${local.name}-plans-${local.cicd_account}-${var.region}"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "cicd_plans" {
  count                   = local.cicd_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.cicd_plans[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "cicd_plans" {
  count  = local.cicd_enabled ? 1 : 0
  bucket = aws_s3_bucket.cicd_plans[0].id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "cicd_plans" {
  count  = local.cicd_enabled ? 1 : 0
  bucket = aws_s3_bucket.cicd_plans[0].id
  rule {
    id     = "expire-plans"
    status = "Enabled"
    filter { prefix = "plans/" }
    expiration { days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

resource "aws_iam_role" "cicd_infrastructure" {
  for_each             = local.cicd_enabled ? toset(["plan", "pr-plan", "apply"]) : toset([])
  name                 = "${local.name}-github-${each.key}"
  max_session_duration = 7200
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity"
    Principal = { Federated = local.cicd_provider }
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = flatten([
        for repo in local.cicd_repo_subjects : [
          for env in(each.key == "plan" ? ["terraform-plan"] : each.key == "pr-plan" ? ["terraform-pr-plan"] : ["infra-lab"]) : "${repo}:environment:${env}"
        ]
      ])
    } }
  }] })
}
resource "aws_iam_role_policy" "cicd_read" {
  for_each = aws_iam_role.cicd_infrastructure
  name     = "terraform-read"
  role     = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat(local.cicd_read_statements, [
    {
      Sid      = "ReadState", Effect = "Allow", Action = ["s3:GetObject"]
      Resource = ["arn:aws:s3:::${var.cicd_state_bucket}/${var.cicd_state_key}"]
    },
    {
      Sid      = "ReadNonsecretCheckpoints", Effect = "Allow", Action = ["ssm:GetParameter", "ssm:ListTagsForResource"]
      Resource = [aws_ssm_parameter.cicd_init[0].arn, aws_ssm_parameter.cicd_revision[0].arn]
    },
    {
      Sid      = "ReadRuntimeMetadata", Effect = "Allow"
      Action   = ["secretsmanager:DescribeSecret", "secretsmanager:GetResourcePolicy", "secretsmanager:ListSecretVersionIds"]
      Resource = [aws_secretsmanager_secret.runtime.arn]
    }
  ]) })
}

resource "aws_iam_role_policy" "cicd_apply" {
  count = local.cicd_enabled ? 1 : 0
  name  = "approved-infrastructure"
  role  = aws_iam_role.cicd_infrastructure["apply"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    for s in jsondecode(aws_iam_role_policy.cicd[0].policy).Statement : s
    if contains(["TerraformState", "RegisterTaskDefinitions", "ManageProjectTaskRevisions", "DeployProjectServices", "PassOnlyProjectTaskRoles", "EnableExistingTargetAlarms"], s.Sid)
    ], [
    {
      Sid      = "ReadApprovedPlan", Effect = "Allow", Action = ["s3:GetObject"]
      Resource = ["${aws_s3_bucket.cicd_plans[0].arn}/plans/*"]
    },
    {
      Sid      = "ModifyExistingOracle", Effect = "Allow"
      Action   = ["rds:ModifyDBInstance", "rds:AddTagsToResource", "rds:RemoveTagsFromResource"]
      Resource = [aws_db_instance.oracle.arn]
    },
    {
      Sid      = "ModifyDatabaseNetwork", Effect = "Allow"
      Action   = ["rds:ModifyDBSubnetGroup", "rds:ModifyOptionGroup", "rds:AddTagsToResource", "rds:RemoveTagsFromResource"]
      Resource = [aws_db_subnet_group.main.arn, aws_db_option_group.oracle.arn]
    },
    {
      Sid = "RegionalNetworkChanges", Effect = "Allow", Resource = "*"
      # Network creation APIs need wildcard resources. This is a lab role for the selected region.
      # A production account should further restrict these actions by VPC, resource tags, and permission boundaries.
      Condition = { StringEquals = { "aws:RequestedRegion" = var.region } }
      Action = [
        "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:ModifySubnetAttribute", "ec2:ModifyVpcAttribute",
        "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable", "ec2:ReplaceRouteTableAssociation",
        "ec2:CreateRoute", "ec2:ReplaceRoute", "ec2:DeleteRoute", "ec2:CreateTags", "ec2:DeleteTags",
        "ec2:AllocateAddress", "ec2:ReleaseAddress", "ec2:CreateNatGateway", "ec2:DeleteNatGateway",
        "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup", "ec2:AuthorizeSecurityGroupIngress", "ec2:AuthorizeSecurityGroupEgress",
        "ec2:RevokeSecurityGroupIngress", "ec2:RevokeSecurityGroupEgress", "ec2:ModifySecurityGroupRules",
        "ec2:UpdateSecurityGroupRuleDescriptionsIngress", "ec2:UpdateSecurityGroupRuleDescriptionsEgress"
      ]
    },
    {
      Sid      = "ModifyProjectCluster", Effect = "Allow"
      Action   = ["ecs:UpdateCluster", "ecs:UpdateClusterSettings", "ecs:PutClusterCapacityProviders", "ecs:TagResource", "ecs:UntagResource"]
      Resource = [aws_ecs_cluster.main.arn]
    },
    {
      Sid = "ModifyProjectLoadBalancing", Effect = "Allow"
      Action = ["elasticloadbalancing:ModifyLoadBalancerAttributes", "elasticloadbalancing:SetSubnets", "elasticloadbalancing:SetSecurityGroups",
      "elasticloadbalancing:ModifyTargetGroup", "elasticloadbalancing:ModifyTargetGroupAttributes", "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags"]
      Resource = concat([for b in aws_lb.main : b.arn], [for t in aws_lb_target_group.main : t.arn])
    },
    {
      Sid      = "ModifyProjectLogs", Effect = "Allow"
      Action   = ["logs:PutRetentionPolicy", "logs:DeleteRetentionPolicy", "logs:PutMetricFilter", "logs:DeleteMetricFilter", "logs:TagResource", "logs:UntagResource"]
      Resource = ["arn:aws:logs:${var.region}:${local.cicd_account}:log-group:/ecs/${local.name}/*"]
    },
    {
      Sid      = "ModifyProjectAlarms", Effect = "Allow"
      Action   = ["cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms", "cloudwatch:TagResource", "cloudwatch:UntagResource"]
      Resource = ["arn:aws:cloudwatch:${var.region}:${local.cicd_account}:alarm:${local.name}-*"]
    }
  ]) })
}

output "cicd_plan_role_arn" { value = try(aws_iam_role.cicd_infrastructure["plan"].arn, "") }
output "cicd_infra_role_arn" { value = try(aws_iam_role.cicd_infrastructure["apply"].arn, "") }
output "cicd_plan_bucket" { value = try(aws_s3_bucket.cicd_plans[0].id, "") }
output "cicd_revision_parameter" { value = try(aws_ssm_parameter.cicd_revision[0].name, "") }

resource "aws_iam_role_policy" "cicd_plan_upload" {
  count = local.cicd_enabled ? 1 : 0
  name  = "private-plan-upload"
  role  = aws_iam_role.cicd_infrastructure["plan"].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect   = "Allow", Action = ["s3:PutObject"]
    Resource = ["${aws_s3_bucket.cicd_plans[0].arn}/plans/*"]
  }] })
}
output "cicd_pr_plan_role_arn" { value = try(aws_iam_role.cicd_infrastructure["pr-plan"].arn, "") }
