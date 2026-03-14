# Dave IT Guy — Azure OpenClaw Pro Tier Variables
# https://github.com/NeuroGamingLab/dave-it-guy

variable "region" {
  description = "Azure region for all resources"
  type        = string
  default     = "eastus"
}

variable "vm_size" {
  description = "Azure VM SKU. Default: Standard_B2s (cheap). GPU: Standard_NC6s_v3"
  type        = string
  default     = "Standard_B2s"
}

variable "gpu_enabled" {
  description = "Enable GPU support (installs NVIDIA drivers + container toolkit)"
  type        = bool
  default     = false
}

variable "admin_username" {
  description = "SSH admin username for the VM"
  type        = string
  default     = "daveitguy"
}

variable "ssh_public_key" {
  description = "SSH public key for VM access"
  type        = string
}

variable "allowed_ips" {
  description = "List of CIDR blocks allowed to access SSH and the gateway"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "models_to_pull" {
  description = "Ollama models to pull on first boot"
  type        = list(string)
  default     = ["llama3.2:3b"]
}

variable "anthropic_api_key" {
  description = "Anthropic API key for OpenClaw"
  type        = string
  default     = ""
  sensitive   = true
}

variable "gateway_port" {
  description = "OpenClaw gateway port"
  type        = number
  default     = 18789
}

variable "data_disk_size_gb" {
  description = "Size of the managed data disk in GB"
  type        = number
  default     = 64
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "dave-it-guy-openclaw"
}
