output "public_ip" {
  description = "Elastic IP address of the instance"
  value       = aws_eip.shawtybot_web.public_ip
}

output "instance_id" {
  value = aws_instance.shawtybot_web.id
}

output "ssh_command" {
  value = "ssh -i shawtybot-web-key.pem ubuntu@${aws_eip.shawtybot_web.public_ip}"
}

output "private_key_pem" {
  value     = tls_private_key.shawtybot_web.private_key_pem
  sensitive = true
}
