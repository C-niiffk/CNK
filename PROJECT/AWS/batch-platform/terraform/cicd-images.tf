variable "service_image_tags" {
  description = "Per-module image tags. CI passes the complete map and preserves unselected modules."
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([
      for name, tag in var.service_image_tags :
      (contains(["frontend-service", "backend-service", "agent-service", "application-service"], name) &&
      can(regex("^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$", tag)) && tag != "latest")
    ])
    error_message = "Use known module names and explicit image tags."
  }
}
locals {
  service_image_tags = merge(
    { for name in ["frontend-service", "backend-service", "agent-service", "application-service"] : name => var.image_tag },
    var.service_image_tags
  )
}
output "cicd_image_tags" {
  value = local.service_image_tags
}
