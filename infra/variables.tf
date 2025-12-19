variable "project_name" {
  description = "Name of a project"
  type        = string
  default     = "quicknote"
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Environment"
  type        = string
  default     = "pet"
}
