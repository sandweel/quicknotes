output "ecr_repository_url" {
  value = aws_ecr_repository.resource_ecr.repository_url
  description = "ECR URL"
}

output "docker_login_command" {
  value       = "aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.resource_ecr.repository_url}"
  description = "Command for docker login ECR"
}

output "vpc_id" {
  value = module.eks_vpc.vpc_id
}
