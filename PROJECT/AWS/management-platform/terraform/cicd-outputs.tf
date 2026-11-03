output "cicd" {
  value = var.enable_cicd ? {
    codebuild_project = aws_codebuild_project.deploy[0].name
    artifact_bucket   = local.cicd_artifact_bucket
    deploy_role_arn   = local.cicd_deploy_role_arn
  } : null
}

output "cicd_bootstrap" {
  value = var.enable_cicd ? {
    roles           = { for key, role in aws_iam_role.cicd_github : key => role.arn }
    artifact_bucket = aws_s3_bucket.cicd_artifacts[0].id
    github_oidc_arn = local.cicd_github_oidc_arn
  } : null
}
