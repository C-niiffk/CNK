output "deployment" {
  value = {
    region       = var.region, project = var.project, environment = var.environment, name = local.name, vpc_id = aws_vpc.main.id,
    cluster_name = aws_eks_cluster.main.name,
    public_url   = "${var.certificate_arn == "" ? "http" : "https"}://${var.public_hostname != "" ? var.public_hostname : aws_lb.public.dns_name}",
    internal_alb = aws_lb.internal.dns_name, agents_nlb = aws_lb.agents.dns_name,
    ecr = {
      for k, v in aws_ecr_repository.images : k => v.repository_url
    },
    http_targets = {
      for k, v in aws_lb_target_group.http : k => v.arn
    },
    tcp_targets = {
      for k, v in aws_lb_target_group.tcp : k => v.arn
    },
    rules = {
      for k, v in aws_lb_listener_rule.services : k => v.arn
    },
    pod_roles = {
      for k, v in aws_iam_role.pod : k => v.arn
    },
    discovery_nlbs    = { for k, v in aws_lb.discovery : k => v.dns_name },
    discovery_targets = { for k, v in aws_lb_target_group.discovery : k => v.arn },
    azs               = local.azs,
    es_hosts          = [for i in aws_instance.elasticsearch : i.private_ip],
    es_instances      = { for site, i in local.sites : site => aws_instance.elasticsearch[i].id },
    db = {
      for k, v in aws_db_instance.oracle : k => {
        host       = v.address
        port       = v.port
        service    = v.db_name
        secret_arn = v.master_user_secret[0].secret_arn
        jdbc_url   = "jdbc:oracle:thin:@//${v.address}:${v.port}/${v.db_name}"
      }
    },
    runtime_secret_arn = aws_secretsmanager_secret.platform.arn, redis_secret_arn = aws_secretsmanager_secret.redis.arn,
    redis_host         = aws_elasticache_replication_group.rate.primary_endpoint_address,
    routing_lock_table = aws_dynamodb_table.routing.name,
    asgs               = {}
    app2 = { for site, host in aws_instance.app2 : site => {
      instance_id = host.id
      private_ip  = host.private_ip
      volume_id   = aws_ebs_volume.app2_data[site].id
      site        = site
      az          = host.availability_zone
      ssh_user    = "ec2-user"
    } }
  }
}
