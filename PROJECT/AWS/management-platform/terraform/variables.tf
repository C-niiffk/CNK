variable "region" {
  type    = string
  default = "us-east-1"
}

variable "project" {
  type    = string
  default = "management-platform"
}

variable "environment" {
  type    = string
  default = "lab"
}

variable "vpc_cidr" {
  type    = string
  default = "10.70.0.0/16"
}

variable "allowed_cidrs" {
  description = "Trusted operator IPv4 CIDRs for ALB and the EKS public API."
  type        = list(string)
  validation {
    condition = (
      length(var.allowed_cidrs) > 0 &&
      alltrue([for cidr in var.allowed_cidrs : can(cidrnetmask(cidr))]) &&
      !contains(var.allowed_cidrs, "0.0.0.0/0")
    )
    error_message = "Use at least one valid trusted IPv4 CIDR; 0.0.0.0/0 is not allowed."
  }
}

variable "operator_arn" {
  type = string
}

variable "eks_version" {
  type    = string
  default = "1.35"
}

variable "node_instance_type" {
  type    = string
  default = "m6i.large"
}

variable "ec2_instance_type" {
  type    = string
  default = "t3.small"
}

variable "es_instance_type" {
  type    = string
  default = "t3.medium"
}

variable "db_instance_class" {
  type    = string
  default = "db.t3.small"
}

variable "oracle_engine_version" {
  type = string
}

variable "single_nat_gateway" {
  description = "Compatibility input; two-AZ HA requires one NAT per AZ."
  type        = bool
  default     = false
  validation {
    condition     = !var.single_nat_gateway
    error_message = "single_nat_gateway must be false for two-AZ failover."
  }
}

variable "deploy_ec2_apps" {
  type    = bool
  default = true
}

variable "image_tag" {
  type        = string
  default     = "v1"
  description = "Immutable image tag for container workloads; App2 uses a versioned JAR bundle."
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$", var.image_tag))
    error_message = "Use a valid immutable ECR image tag."
  }
}

variable "certificate_arn" {
  type    = string
  default = ""
}

variable "protect_data" {
  type    = bool
  default = true
}

variable "public_hostname" {
  type        = string
  default     = ""
  description = "Custom DNS name matching certificate_arn. Empty for HTTP lab mode."
  validation {
    condition     = var.certificate_arn == "" || var.public_hostname != ""
    error_message = "Set public_hostname to the DNS name covered by certificate_arn."
  }
}

variable "final_snapshot_identifiers" {
  description = "Final snapshot name keyed by management; omitted uses PROJECT-ENV-management-final."
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([
      for key, name in var.final_snapshot_identifiers : (
        key == "management" &&
        can(regex("^[A-Za-z][A-Za-z0-9-]{0,254}$", name)) &&
        !endswith(name, "-") &&
        !strcontains(name, "--")
      )
    ])
    error_message = "Use the management key and valid RDS snapshot names: start with a letter, up to 255 letters/digits/hyphens, no trailing or consecutive hyphens."
  }
}

variable "app2_ami_id" {
  description = "Verified RHEL 9 x86_64 AMI in this region. Required when deploy_ec2_apps=true."
  type        = string
  default     = ""
}

variable "app2_ami_owner" {
  description = "AMI owner account; default is Red Hat commercial AWS account. Override for your approved enterprise AMI."
  type        = string
  default     = "309956199498"
}

variable "app2_key_name" {
  description = "Existing EC2 key pair for SSH through SSM (no public SSH ingress)."
  type        = string
  default     = ""
}

variable "app2_data_gib" {
  type    = number
  default = 20
  validation {
    condition     = var.app2_data_gib >= 10 && floor(var.app2_data_gib) == var.app2_data_gib
    error_message = "Use an integer of at least 10 GiB."
  }
}

variable "es_ami_id" {
  description = "Reviewed RHEL 9 x86_64 AMI in this AWS region, with cloud-init and working RHUI/repos."
  type        = string
  validation {
    condition     = can(regex("^ami-[0-9a-f]+$", var.es_ami_id))
    error_message = "Set es_ami_id to an available RHEL 9 x86_64 AMI ID in this region."
  }
}

variable "es_ami_owner" {
  description = "Owner of the reviewed ES AMI; override for an approved enterprise RHEL AMI."
  type        = string
  default     = "309956199498"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.es_ami_owner))
    error_message = "es_ami_owner must be a 12-digit AWS account ID."
  }
}

variable "app2_root_gib" {
  description = "RHEL App2 root disk; must also be at least the selected AMI root snapshot size."
  type        = number
  default     = 30
  validation {
    condition     = var.app2_root_gib >= 20 && floor(var.app2_root_gib) == var.app2_root_gib
    error_message = "app2_root_gib must be an integer of at least 20 GiB."
  }
}

variable "es_root_gib" {
  description = "RHEL ES root disk, including /var/lib/elasticsearch; retained on instance termination."
  type        = number
  default     = 50
  validation {
    condition     = var.es_root_gib >= 30 && floor(var.es_root_gib) == var.es_root_gib
    error_message = "es_root_gib must be an integer of at least 30 GiB."
  }
}
