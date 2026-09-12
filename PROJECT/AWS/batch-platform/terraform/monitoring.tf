resource "aws_sns_topic" "alarms" {
  name = "${local.name}-alarms"
}
resource "aws_sns_topic_subscription" "email" {
  endpoint  = var.alarm_email
  protocol  = "email"
  topic_arn = aws_sns_topic.alarms.arn
  count = var.alarm_email == "" ? 0:1
}
resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name = "${local.name}-rds-cpu"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 3
  namespace = "AWS/RDS"
  metric_name = "CPUUtilization"
  dimensions = {DBInstanceIdentifier = aws_db_instance.oracle.identifier}
  statistic = "Average"
  period = 300
  threshold = 80
  alarm_actions = [aws_sns_topic.alarms.arn]
  treat_missing_data = "missing"
}
resource "aws_cloudwatch_metric_alarm" "target_health" {
  for_each            = aws_lb_target_group.main
  alarm_name          = "${local.name}-${each.key}-healthy-hosts"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HealthyHostCount"
  dimensions          = { TargetGroup = each.value.arn_suffix, LoadBalancer = aws_lb.main[each.key == "frontend" ? "public" : "internal"].arn_suffix }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 3
  comparison_operator = "LessThanThreshold"
  threshold           = 1
  alarm_actions       = [aws_sns_topic.alarms.arn]
  treat_missing_data  = var.deploy_services ? "breaching" : "notBreaching"
}
resource "aws_cloudwatch_log_metric_filter" "unknown" {
  name           = "unknown-jobs"
  log_group_name = aws_cloudwatch_log_group.service["backend"].name
  pattern        = "?\"status=UNKNOWN\" ?\"recovered_unknown=\""
  metric_transformation {
    name      = "UnknownJobs"
    namespace = "BatchPlatform/${local.name}"
    value     = "1"
  }
}
resource "aws_cloudwatch_metric_alarm" "unknown" {
  alarm_name          = "${local.name}-unknown-jobs"
  namespace           = "BatchPlatform/${local.name}"
  metric_name         = "UnknownJobs"
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alarms.arn]
}
