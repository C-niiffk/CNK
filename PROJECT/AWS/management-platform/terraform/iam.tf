resource "aws_iam_role" "cluster" {
  name = "${local.name}-cluster"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "eks.amazonaws.com"
      },
      Action = "sts:AssumeRole"
      }
    ]
    }
  )
}

resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

resource "aws_iam_role" "node" {
  name = "${local.name}-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ec2.amazonaws.com"
      },
      Action = "sts:AssumeRole"
      }
    ]
    }
  )
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryReadOnly", "AmazonEKS_CNI_Policy", "AmazonSSMManagedInstanceCore"])
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}

data "tls_certificate" "eks" {
  url = aws_eks_cluster.main.identity[0].oidc[0].issuer
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.main.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks.certificates[length(data.tls_certificate.eks.certificates) - 1].sha1_fingerprint]
}

locals {
  oidc = replace(aws_eks_cluster.main.identity[0].oidc[0].issuer, "https://", "")
}

resource "aws_iam_role" "ebs" {
  name = "${local.name}-ebs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      },
      Action = "sts:AssumeRoleWithWebIdentity", Condition = {
        StringEquals = {
          "${local.oidc}:aud" = "sts.amazonaws.com", "${local.oidc}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
        }
      }
      }
    ]
    }
  )
}

resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.ebs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

locals {
  pod_roles = {
    load-balancer-controller = "system:serviceaccount:kube-system:aws-load-balancer-controller",
    routing-controller       = "system:serviceaccount:platform:routing-controller",
    prometheus               = "system:serviceaccount:observability:prometheus"
  }
}

resource "aws_iam_role" "pod" {
  for_each = local.pod_roles
  name     = "${local.name}-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      },
      Action = "sts:AssumeRoleWithWebIdentity", Condition = {
        StringEquals = {
          "${local.oidc}:aud" = "sts.amazonaws.com", "${local.oidc}:sub" = each.value
        }
      }
      }
    ]
    }
  )
}

resource "aws_iam_role_policy" "lbc" {
  name = "target-group-bindings-only"
  role = aws_iam_role.pod["load-balancer-controller"].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["ec2:Describe*", "elasticloadbalancing:Describe*"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["elasticloadbalancing:RegisterTargets", "elasticloadbalancing:DeregisterTargets"], Resource = concat([for x in aws_lb_target_group.http : x.arn], [for x in aws_lb_target_group.tcp : x.arn], [for x in aws_lb_target_group.discovery : x.arn])
      }
    ]
    }
  )
}

resource "aws_iam_role_policy" "routing" {
  role = aws_iam_role.pod["routing-controller"].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [
      {
        Effect = "Allow", Action = ["elasticloadbalancing:DescribeRules", "elasticloadbalancing:DescribeTargetHealth"], Resource = "*"
      },
      {
        Effect = "Allow", Action = ["elasticloadbalancing:ModifyRule"], Resource = [for r in aws_lb_listener_rule.services : r.arn]
      },
      {
        Effect = "Allow", Action = ["dynamodb:UpdateItem"], Resource = aws_dynamodb_table.routing.arn
      }
    ]
    }
  )
}

resource "aws_iam_role_policy" "prometheus" {
  role = aws_iam_role.pod["prometheus"].id
  policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*"
      }
    ]
    }
  )
}

resource "aws_iam_role" "ec2" {
  name = "${local.name}-ec2"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{
      Effect = "Allow", Principal = {
        Service = "ec2.amazonaws.com"
      },
      Action = "sts:AssumeRole"
      }
    ]
    }
  )
}

resource "aws_iam_instance_profile" "ec2" {
  name = local.name
  role = aws_iam_role.ec2.name
}

resource "aws_iam_role_policy_attachment" "ec2" {
  for_each   = toset(["AmazonSSMManagedInstanceCore", "AmazonEC2ContainerRegistryReadOnly"])
  role       = aws_iam_role.ec2.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}

resource "aws_iam_role_policy" "ec2" {
  role = aws_iam_role.ec2.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.platform.arn
    }]
  })
}

resource "aws_iam_role" "elasticsearch" {
  name               = "${local.name}-elasticsearch"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}

resource "aws_iam_role_policy_attachment" "elasticsearch_ssm" {
  role       = aws_iam_role.elasticsearch.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "elasticsearch" {
  name = "${local.name}-elasticsearch"
  role = aws_iam_role.elasticsearch.name
}
