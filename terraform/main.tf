terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  # us-east-1e in this account doesn't support t3.micro — stick to AZs that do.
  filter {
    name   = "availability-zone"
    values = ["us-east-1a", "us-east-1b", "us-east-1c", "us-east-1d", "us-east-1f"]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "tls_private_key" "shawtybot_web" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "aws_key_pair" "shawtybot_web" {
  key_name   = var.key_pair_name
  public_key = tls_private_key.shawtybot_web.public_key_openssh
}

resource "aws_security_group" "shawtybot_web" {
  name        = "shawtybot-web-sg"
  description = "ShawtyBot Pokemon web app - SSH + HTTP/HTTPS"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_allowed_cidr]
  }

  ingress {
    description = "HTTP"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "shawtybot-web-sg"
  }
}

resource "aws_instance" "shawtybot_web" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.shawtybot_web.key_name
  subnet_id              = data.aws_subnets.default.ids[0]
  vpc_security_group_ids = [aws_security_group.shawtybot_web.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
  }

  tags = {
    Name = "shawtybot-web"
  }
}

resource "aws_eip" "shawtybot_web" {
  instance = aws_instance.shawtybot_web.id
  domain   = "vpc"

  tags = {
    Name = "shawtybot-web-eip"
  }
}
