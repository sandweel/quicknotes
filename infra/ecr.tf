resource "aws_ecr_repository" "resource_ecr" {
  name                 = var.project_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }
  tags = local.common_tags
}
