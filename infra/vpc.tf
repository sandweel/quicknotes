module "eks_vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.5.1"

  name = "eks-vpc"
  cidr = "192.168.0.0/16"

  azs             = ["us-east-1a", "us-east-1b"]
  public_subnets  = ["192.168.0.0/24", "192.168.1.0/24"]

  enable_nat_gateway = false

  tags = local.common_tags

  public_subnet_tags = {
    "kubernetes.io/role/elb" = "1"
  }
}
