#!/usr/bin/env bash
# Run after Terraform creates ECR. Uses the root build context for every module.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${AWS_REGION:?export AWS_REGION first}"
: "${IMAGE_TAG:?export IMAGE_TAG using an unused release tag}"
command -v docker >/dev/null
command -v jq >/dev/null
batch_repos=$(terraform -chdir=terraform output -json ecr_repositories)
batch_registry=$(jq -r '.["frontend-service"] | split("/")[0]' <<< "$batch_repos")
aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "$batch_registry"
for batch_service in frontend-service backend-service agent-service application-service; do
  batch_repository=$(jq -r --arg service "$batch_service" '.[$service]' <<< "$batch_repos")
  docker build --platform linux/amd64 -f "$batch_service/Dockerfile" \
    -t "$batch_repository:$IMAGE_TAG" .
  docker push "$batch_repository:$IMAGE_TAG"
done
printf 'Pushed four images with release tag %s\n' "$IMAGE_TAG"
