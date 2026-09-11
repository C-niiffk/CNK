variable "region" {
  type = string
  default = "us-east-1"
}
variable "project" {
  type = string
  default = "batch-platform"
  validation {
    condition = can(regex("^[a-z][a-z0-9-]{2,17}$", var.project))
    error_message = "Use 3-18 lowercase letters, digits or hyphens, starting with a letter"
  }
}
variable "environment" {
  type = string
  default = "lab"
  validation {
    condition = can(regex("^[a-z0-9]{2,6}$", var.environment))
    error_message = "Use 2-6 lowercase alphanumeric characters."
  }
}
variable "vpc_cidr" {
  type = string
  default = "10.42.0.0/16"
}
variable "domain_name" {
  type = string
  description = "FQDN in an existing publicly delegated Route53 hosted zone; e.g. batch.example.com"
}
variable "hosted_zone_id" {
  type = string
  description = "Existing public Route53 hosted zone ID in this AWS account"
}
variable "allowed_cidrs" {
  type = list(string)
  description = "Trusted public IPv4 egress CIDRs allowed into HTTPS ALB"

  validation {
    condition = (
    length(var.allowed_cidrs) > 0 &&
    alltrue([
      for c in var.allowed_cidrs :
      (can(cidrnetmask(c)) && c != "0.0.0.0/0")
    ])
    )

    error_message = "Provide trusted IPv4 CIDRs; this template refuses 0.0.0.0/0."
  }
}
variable "oracle_engine_version" {
  type = string
  description = "Exact available Oracle SE2 19c engine version; discover with AWS CLI before apply"
  validation {
    condition = startswith(var.oracle_engine_version, "19.")
    error_message = "This template uses Oracle 19c non-CDB (oracle-se2)."
  }
}
variable "db_instance_class" {
  type = string
  default = "db.m6i.large"
}
variable "image_tag" {
  type = string
  default = "jdk21-v1"
  validation {
    condition = can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", var.image_tag)) && var.image_tag != "latest"
    error_message = "Use an immutable release tag, not latest."
  }
}
variable "deploy_services" {
  type = bool
  default = false
  description = "Set true ONLY after image push and successful init-db task"
}
variable "protect_data" {
  type = bool
  default = true
  description = "RDS and ALB deletion protection; final RDS snapshot is always required"
}
variable "final_snapshot_identifier" {
  type = string
  default = "batch-platform-final"
  description = "Unique final snapshot name; change if that snapshot name already exists"
}
variable "alarm_email" {
  type = string
  default = ""
  description = "Optional SNS email; confirm subscription to receive alarms"
}
