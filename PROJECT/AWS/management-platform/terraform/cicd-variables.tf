variable "enable_cicd" {
  type    = bool
  default = false
}
variable "cicd_repository" {
  type    = string
  default = ""
}
variable "cicd_connection_arn" {
  type    = string
  default = ""
}
variable "cicd_artifact_bucket" {
  type        = string
  default     = ""
  description = "Legacy input; the artifact bucket is now managed in this root module."
}
variable "cicd_deploy_role_arn" {
  type        = string
  default     = ""
  description = "Legacy input; the deployment role is now managed in this root module."
}
variable "cicd_enable_webhook" {
  type    = bool
  default = false
}

# Optional CI/CD inputs. Existing manual deployments keep enable_cicd=false.
variable "cicd_oidc_repository_claim" {
  type        = string
  default     = ""
  description = "Exact GitHub repository subject segment; include immutable owner/repository IDs when enabled."
}
variable "cicd_existing_github_oidc_arn" {
  type        = string
  default     = ""
  description = "Reuse the account GitHub OIDC provider when it is managed outside this root state."
}
variable "cicd_state_bucket" {
  type        = string
  default     = ""
  description = "Existing S3 bucket used by this root module backend."
}
variable "cicd_state_key" {
  type        = string
  default     = ""
  description = "Existing root module Terraform state object key."
}
variable "cicd_artifact_bucket_name" {
  type        = string
  default     = ""
  description = "Optional artifact bucket name; blank uses PROJECT-ENV-cicd-ACCOUNT-REGION."
}
