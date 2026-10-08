data "aws_ami" "elasticsearch" {
  owners = [var.es_ami_owner]
  filter {
    name   = "image-id"
    values = [var.es_ami_id]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
  filter {
    name   = "root-device-type"
    values = ["ebs"]
  }
  filter {
    name   = "state"
    values = ["available"]
  }
}

data "aws_ami" "app2" {
  count  = var.deploy_ec2_apps ? 1 : 0
  owners = [var.app2_ami_owner]
  filter {
    name   = "image-id"
    values = [var.app2_ami_id]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_instance" "app2" {
  for_each                    = var.deploy_ec2_apps ? local.sites : {}
  ami                         = data.aws_ami.app2[0].id
  instance_type               = var.ec2_instance_type
  subnet_id                   = aws_subnet.private[each.value].id
  vpc_security_group_ids      = [aws_security_group.tier["ec2-app"].id]
  iam_instance_profile        = aws_iam_instance_profile.ec2.name
  key_name                    = var.app2_key_name
  associate_public_ip_address = false
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = <<-BOOT
    #!/bin/bash
    set -euo pipefail
    dnf install -y openssh-server curl
    if ! rpm -q amazon-ssm-agent; then
      dnf install -y https://s3.${var.region}.amazonaws.com/amazon-ssm-${var.region}/latest/linux_amd64/amazon-ssm-agent.rpm
    fi
    systemctl enable --now sshd amazon-ssm-agent
  BOOT
  tags = {
    Name        = "${local.name}-app2-${each.key}"
    Project     = var.project
    Environment = var.environment
    Site        = each.key
    Role        = "application"
  }
  lifecycle {
    precondition {
      condition     = can(regex("^ami-[0-9a-f]+$", var.app2_ami_id)) && var.app2_key_name != ""
      error_message = "Set a verified RHEL 9 x86_64 app2_ami_id and an existing EC2 app2_key_name."
    }
  }
  depends_on = [aws_route_table_association.private, aws_nat_gateway.main, aws_iam_role_policy_attachment.ec2]
}

resource "aws_ebs_volume" "app2_data" {
  for_each          = var.deploy_ec2_apps ? local.sites : {}
  availability_zone = aws_subnet.private[each.value].availability_zone
  type              = "gp3"
  size              = var.app2_data_gib
  encrypted         = true
  tags              = { Name = "${local.name}-app2-data-${each.key}", Project = var.project, Environment = var.environment }
  lifecycle { prevent_destroy = true }
}

resource "aws_volume_attachment" "app2_data" {
  for_each     = var.deploy_ec2_apps ? local.sites : {}
  device_name  = "/dev/sdf"
  volume_id    = aws_ebs_volume.app2_data[each.key].id
  instance_id  = aws_instance.app2[each.key].id
  force_detach = false
}

resource "aws_lb_target_group_attachment" "app2" {
  for_each         = var.deploy_ec2_apps ? local.sites : {}
  target_group_arn = aws_lb_target_group.http["app2"].arn
  target_id        = aws_instance.app2[each.key].id
  port             = 8080
}

resource "aws_instance" "elasticsearch" {
  count                       = 2
  ami                         = data.aws_ami.elasticsearch.id
  instance_type               = var.es_instance_type
  subnet_id                   = aws_subnet.private[count.index].id
  private_ip                  = cidrhost(aws_subnet.private[count.index].cidr_block, 20)
  vpc_security_group_ids      = [aws_security_group.tier["elasticsearch"].id]
  iam_instance_profile        = aws_iam_instance_profile.elasticsearch.name
  associate_public_ip_address = false
  metadata_options { http_tokens = "required" }
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = false
  }
  user_data = join("\n", [
    "#!/bin/bash",
    "export ES_VPC_CIDR=${var.vpc_cidr}",
    file("${path.module}/../management-plane/observability/deploy/ec2/es-user-data.sh")
  ])
  user_data_replace_on_change = true
  tags                        = { Name = "${local.name}-es-${count.index}", Role = "elasticsearch", Site = count.index == 0 ? "a" : "b" }
  depends_on                  = [aws_route_table_association.private, aws_nat_gateway.main, aws_iam_role_policy_attachment.elasticsearch_ssm]
}
