# Dave IT Guy — Azure OpenClaw Pro Tier Outputs

output "public_ip" {
  description = "Public IP address of the VM"
  value       = azurerm_public_ip.main.ip_address
}

output "ssh_command" {
  description = "SSH command to connect to the VM"
  value       = "ssh ${var.admin_username}@${azurerm_public_ip.main.ip_address}"
}

output "gateway_url" {
  description = "OpenClaw gateway URL"
  value       = "http://${azurerm_public_ip.main.ip_address}:${var.gateway_port}"
}

output "resource_group" {
  description = "Resource group name (for easy cleanup: az group delete -n <name>)"
  value       = azurerm_resource_group.main.name
}
