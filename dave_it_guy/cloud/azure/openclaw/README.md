# Dave IT Guy — Azure OpenClaw Pro Tier

Deploy the full **OpenClaw + Ollama + Qdrant** stack to an Azure VM with a single `terraform apply`.

## What You Get

| Component | Description |
|-----------|-------------|
| **OpenClaw** | AI gateway on port 18789 |
| **Ollama** | Local LLM inference (GPU optional) |
| **Qdrant** | Vector database for RAG |
| **Managed Disk** | Persistent storage for models & data |

## Prerequisites

- [Terraform](https://terraform.io) ≥ 1.5
- [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) authenticated (`az login`)
- An SSH key pair

## Quick Start

```bash
# 1. Clone and navigate
cd dave_it_guy/cloud/azure/openclaw/

# 2. Create a terraform.tfvars file
cat > terraform.tfvars <<'EOF'
ssh_public_key  = "ssh-rsa AAAA..."
allowed_ips     = ["YOUR_IP/32"]
models_to_pull  = ["llama3.2:3b"]
anthropic_api_key = "sk-ant-..."
EOF

# 3. Deploy
terraform init
terraform plan
terraform apply
```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `region` | `eastus` | Azure region |
| `vm_size` | `Standard_B2s` | VM SKU (see GPU section) |
| `gpu_enabled` | `false` | Install NVIDIA drivers + container toolkit |
| `admin_username` | `daveitguy` | SSH username |
| `ssh_public_key` | *(required)* | Your SSH public key |
| `allowed_ips` | `["0.0.0.0/0"]` | CIDRs allowed for SSH + gateway |
| `models_to_pull` | `["llama3.2:3b"]` | Ollama models to download on boot |
| `anthropic_api_key` | `""` | Anthropic API key for OpenClaw |
| `gateway_port` | `18789` | OpenClaw gateway port |
| `data_disk_size_gb` | `64` | Managed disk size |
| `project_name` | `dave-it-guy-openclaw` | Prefix for all Azure resources |

## GPU Support

For GPU inference, set:

```hcl
vm_size     = "Standard_NC6s_v3"   # 1x V100 16GB
gpu_enabled = true
```

Other GPU options:
- `Standard_NC4as_T4_v3` — 1x T4 (cheapest GPU)
- `Standard_NC6s_v3` — 1x V100
- `Standard_NC24s_v3` — 4x V100

> **Note:** GPU VMs require quota approval in your Azure subscription. Request quota via Azure Portal → Subscriptions → Usage + quotas.

## Outputs

After `terraform apply`, you'll see:

```
public_ip    = "20.xx.xx.xx"
ssh_command  = "ssh daveitguy@20.xx.xx.xx"
gateway_url  = "http://20.xx.xx.xx:18789"
```

## Post-Deploy

```bash
# SSH into the VM
ssh daveitguy@$(terraform output -raw public_ip)

# Check stack status
docker compose -f /opt/dave-it-guy/docker-compose.yml ps

# Watch model download progress
tail -f /var/log/dave-it-guy-models.log

# Check cloud-init progress
tail -f /var/log/cloud-init-output.log
```

## Security Notes

- **Lock down `allowed_ips`** — Default is `0.0.0.0/0` (open to all). Set to your IP: `["1.2.3.4/32"]`
- Only ports 22 (SSH) and 18789 (gateway) are exposed
- All other inbound traffic is denied by NSG
- The `anthropic_api_key` is marked sensitive in Terraform state

## Cleanup

```bash
# Remove everything
terraform destroy

# Or via Azure CLI
az group delete -n dave-it-guy-openclaw-rg --yes --no-wait
```

## Cost Estimates

| VM Size | Monthly Est. | Use Case |
|---------|-------------|----------|
| `Standard_B2s` | ~$30 | Testing, light use |
| `Standard_B4ms` | ~$60 | Production, no GPU |
| `Standard_NC4as_T4_v3` | ~$350 | GPU inference (T4) |
| `Standard_NC6s_v3` | ~$700 | GPU inference (V100) |

> Costs vary by region. Use [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/) for exact estimates.
