# Add to the existing Terraform root. Apply once using the operator's AWS identity.

variable "github_repository" {
  type    = string
  default = ""

  validation {
    condition     = var.github_repository == "" || can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use OWNER/REPO, or an empty string to disable CI/CD resources."
  }
}

variable "github_owner_id" {
  type    = string
  default = ""
}

variable "github_repository_id" {
  type    = string
  default = ""
}

variable "github_existing_oidc_provider_arn" {
  type    = string
  default = ""
}

variable "cicd_state_bucket" {
  type    = string
  default = ""
}

variable "cicd_state_key" {
  type    = string
  default = "batch-platform/lab/terraform.tfstate"
}

locals {
  cicd_enabled = var.github_repository != ""
  cicd_account = data.aws_caller_identity.current.account_id
  cicd_prefix  = "arn:aws:ecs:${var.region}:${local.cicd_account}"
  cicd_parts   = split("/", var.github_repository == "" ? "disabled/disabled" : var.github_repository)

  cicd_subjects = [
    "repo:${var.github_repository}:environment:app-lab",
    "repo:${local.cicd_parts[0]}@${var.github_owner_id}/${local.cicd_parts[1]}@${var.github_repository_id}:environment:app-lab"
  ]

  cicd_provider = var.github_existing_oidc_provider_arn != "" ? var.github_existing_oidc_provider_arn : try(aws_iam_openid_connect_provider.cicd[0].arn, "")
  cicd_roles    = concat([for r in aws_iam_role.execution : r.arn], [aws_iam_role.task.arn])
}

resource "aws_iam_openid_connect_provider" "cicd" {
  count = local.cicd_enabled && var.github_existing_oidc_provider_arn == "" ? 1 : 0

  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

resource "aws_ssm_parameter" "cicd_init" {
  count = local.cicd_enabled ? 1 : 0
  name  = "/${local.name}/cicd/init"
  type  = "String"

  # setup-cicd.py confirms a newly created DB or adopts an already running deployment.
  value = jsonencode({
    db_resource_id = aws_db_instance.oracle.resource_id
    status         = "unconfirmed"
  })

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_iam_role" "cicd" {
  count                = local.cicd_enabled ? 1 : 0
  name                 = "${local.name}-github"
  max_session_duration = 7200

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [{
      Effect = "Allow"
      Action = "sts:AssumeRoleWithWebIdentity"

      Principal = {
        Federated = local.cicd_provider
      }

      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = local.cicd_subjects
        }
      }
    }]
  })

  lifecycle {
    precondition {
      condition = (
        can(regex("^[0-9]+$", var.github_owner_id)) &&
        can(regex("^[0-9]+$", var.github_repository_id)) &&
        var.cicd_state_bucket != ""
      )

      error_message = "Configure GitHub owner/repository IDs and the existing Terraform state bucket."
    }
  }
}

resource "aws_iam_role_policy" "cicd" {
  count = local.cicd_enabled ? 1 : 0
  name  = "batch-deployment"
  role  = aws_iam_role.cicd[0].id

  policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Sid      = "TerraformResourceDiscovery"
        Effect   = "Allow"
        Resource = "*"

        Action = [
          "ec2:Describe*",
          "elasticloadbalancing:Describe*",
          "rds:Describe*",
          "rds:ListTagsForResource",
          "ecs:Describe*",
          "ecs:List*",
          "ecr:Describe*",
          "ecr:List*",
          "ecr:GetRepositoryPolicy",
          "ecr:GetLifecyclePolicy",
          "logs:Describe*",
          "logs:ListTagsForResource",
          "logs:ListTagsLogGroup",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:ListTagsForResource",
          "sns:GetTopicAttributes",
          "sns:GetSubscriptionAttributes",
          "sns:ListSubscriptionsByTopic",
          "sns:ListTagsForResource",
          "servicediscovery:GetNamespace",
          "servicediscovery:GetService",
          "servicediscovery:List*",
          "route53:GetHostedZone",
          "route53:ListResourceRecordSets",
          "route53:ListTagsForResource",
          "route53:GetChange",
          "acm:DescribeCertificate",
          "acm:ListTagsForCertificate"
        ]
      },
      {
        Sid    = "ReadTerraformIAMResources"
        Effect = "Allow"

        Action = [
          "iam:GetRole",
          "iam:GetRolePolicy",
          "iam:ListRolePolicies",
          "iam:ListAttachedRolePolicies",
          "iam:ListRoleTags",
          "iam:GetOpenIDConnectProvider"
        ]

        Resource = concat(
          local.cicd_roles,
          [
            aws_iam_role.cicd[0].arn,
            local.cicd_provider
          ],
          [
            for r in aws_iam_role.cicd_infrastructure : r.arn
          ]
        )
      },
      {
        Sid    = "ReadProjectBuckets"
        Effect = "Allow"

        Action = [
          "s3:Get*",
          "s3:ListBucket"
        ]

        Resource = concat(
          [
            "arn:aws:s3:::${var.cicd_state_bucket}",
            aws_s3_bucket.alb_logs.arn
          ],
          [
            for b in aws_s3_bucket.cicd_plans : b.arn
          ]
        )
      },
      {
        Sid    = "TerraformState"
        Effect = "Allow"

        Action = [
          "s3:GetObject",
          "s3:PutObject"
        ]

        Resource = [
          "arn:aws:s3:::${var.cicd_state_bucket}/${var.cicd_state_key}"
        ]
      },
      {
        Sid    = "TerraformLock"
        Effect = "Allow"

        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject"
        ]

        Resource = [
          "arn:aws:s3:::${var.cicd_state_bucket}/${var.cicd_state_key}.tflock"
        ]
      },
      {
        Sid      = "ECRLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PushProjectImages"
        Effect = "Allow"

        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:PutImage"
        ]

        Resource = [
          for r in aws_ecr_repository.service : r.arn
        ]
      },
      {
        Sid      = "RegisterTaskDefinitions"
        Effect   = "Allow"
        Action   = ["ecs:RegisterTaskDefinition"]
        Resource = "*"
      },
      {
        Sid    = "ManageProjectTaskRevisions"
        Effect = "Allow"

        Action = [
          "ecs:DeregisterTaskDefinition",
          "ecs:TagResource",
          "ecs:UntagResource"
        ]

        Resource = [
          for suffix in ["frontend", "backend", "app1", "app2", "init"] :
          "${local.cicd_prefix}:task-definition/${local.name}-${suffix}:*"
        ]
      },
      {
        Sid    = "DeployProjectServices"
        Effect = "Allow"

        Action = [
          "ecs:CreateService",
          "ecs:UpdateService",
          "ecs:TagResource",
          "ecs:UntagResource"
        ]

        Resource = [
          for suffix in ["frontend", "backend", "app1", "app2"] :
          "${local.cicd_prefix}:service/${local.name}/${local.name}-${suffix}"
        ]
      },
      {
        Sid      = "RunPrivateDatabaseInitializer"
        Effect   = "Allow"
        Action   = ["ecs:RunTask"]
        Resource = ["${local.cicd_prefix}:task-definition/${local.name}-init:*"]

        Condition = {
          ArnEquals = {
            "ecs:cluster" = aws_ecs_cluster.main.arn
          }
        }
      },
      {
        Sid      = "PassOnlyProjectTaskRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = local.cicd_roles

        Condition = {
          StringEquals = {
            "iam:PassedToService" = "ecs-tasks.amazonaws.com"
          }
        }
      },
      {
        Sid    = "RuntimeSecretInitialization"
        Effect = "Allow"

        Action = [
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetResourcePolicy",
          "secretsmanager:ListSecretVersionIds",
          "secretsmanager:GetSecretValue",
          "secretsmanager:PutSecretValue"
        ]

        Resource = [
          aws_secretsmanager_secret.runtime.arn
        ]
      },

      # ssm:DescribeParameters is a list/describe operation and cannot be
      # restricted to the individual SSM parameter ARNs below.
      {
        Sid      = "DescribeSSMParameters"
        Effect   = "Allow"
        Action   = ["ssm:DescribeParameters"]
        Resource = "*"
      },
      {
        Sid    = "DatabaseInitializationCheckpoint"
        Effect = "Allow"

        Action = [
          "ssm:GetParameter",
          "ssm:PutParameter",
          "ssm:ListTagsForResource"
        ]

        Resource = [
          aws_ssm_parameter.cicd_init[0].arn,
          aws_ssm_parameter.cicd_revision[0].arn
        ]
      },
      {
        Sid    = "EnableExistingTargetAlarms"
        Effect = "Allow"
        Action = ["cloudwatch:PutMetricAlarm"]

        Resource = [
          for suffix in ["frontend", "backend"] :
          "arn:aws:cloudwatch:${var.region}:${local.cicd_account}:alarm:${local.name}-${suffix}-healthy-hosts"
        ]
      }
    ]
  })
}

output "cicd_role_arn" {
  value = try(aws_iam_role.cicd[0].arn, "")
}

output "cicd_init_parameter" {
  value = try(aws_ssm_parameter.cicd_init[0].name, "")
}

output "cicd_db_resource_id" {
  value = aws_db_instance.oracle.resource_id
}

output "cicd_task_definitions" {
  value = merge(
    {
      for k, t in aws_ecs_task_definition.core :
      "${local.name}-${k}" => t.arn
    },
    {
      for k, t in aws_ecs_task_definition.app :
      "${local.name}-${k}" => t.arn
    }
  )
}
