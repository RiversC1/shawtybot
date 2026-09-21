variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.micro"
}

variable "key_pair_name" {
  description = "Name for the newly created EC2 key pair"
  type        = string
  default     = "shawtybot-web-key"
}

variable "ssh_allowed_cidr" {
  description = "CIDR block allowed to SSH into the instance (tighten this to your own IP/32 when possible)"
  type        = string
  default     = "0.0.0.0/0"
}
