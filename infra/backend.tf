terraform {
  backend "s3" {
    bucket  = "tf-states-s3bucket"
    key     = "quicknote/terraform.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}
