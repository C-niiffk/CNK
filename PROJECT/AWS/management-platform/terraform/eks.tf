locals {
  repositories = toset([
    "management-server", "gateway", "application-service", "global-lb", "grdc", "rule-sync"
  ])
}

resource "aws_eks_cluster" "main" {
  name     = local.name
  role_arn = aws_iam_role.cluster.arn
  version  = var.eks_version
  access_config {
    authentication_mode = "API"
  }
  vpc_config {
    subnet_ids              = aws_subnet.private[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.allowed_cidrs
  }
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]
  depends_on                = [aws_iam_role_policy_attachment.cluster]
}

resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.operator_arn
}

resource "aws_eks_access_policy_association" "operator" {
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = var.operator_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope {
    type = "cluster"
  }
  depends_on = [aws_eks_access_entry.operator]
}

resource "aws_eks_node_group" "sites" {
  for_each = {
    a = 0, b = 1
  }
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = each.key
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = [aws_subnet.private[each.value].id]
  ami_type        = "AL2023_x86_64_STANDARD"
  instance_types  = [var.node_instance_type]
  disk_size       = 80
  labels = {
    site = each.key
  }
  scaling_config {
    min_size     = 1
    max_size     = 4
    desired_size = 2
  }
  update_config {
    max_unavailable = 1
  }
  depends_on = [aws_iam_role_policy_attachment.node, aws_route_table_association.private, aws_nat_gateway.main]
}

resource "aws_eks_addon" "ebs" {
  cluster_name             = aws_eks_cluster.main.name
  addon_name               = "aws-ebs-csi-driver"
  service_account_role_arn = aws_iam_role.ebs.arn
  depends_on               = [aws_eks_node_group.sites, aws_iam_role_policy_attachment.ebs]
}

resource "aws_ecr_repository" "images" {
  for_each             = local.repositories
  name                 = "${local.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}
