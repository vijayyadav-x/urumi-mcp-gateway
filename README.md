# Urumi MCP Gateway on Kubernetes

An MCP gateway connecting calculator, datetime, and WooCommerce MCP servers through a single gateway, with a dashboard and chat agent.

## Architecture
- MCP Gateway: central tool discovery and routing.
- Calculator MCP Server: calculator tools.
- Datetime MCP Server: date and time tools.
- WooCommerce MCP Server: WooCommerce tools.
- Dashboard: web interface for gateway and chat agent.
- Kubernetes and Helm: local deployment.

## Prerequisites
- Docker Desktop with Kubernetes enabled
- kubectl
- Helm
- Python
- Git

## Repository Setup
```bash
git clone https://github.com/vijayyadav-x/urumi-mcp-gateway.git
cd urumi-mcp-gateway
```

## Check Kubernetes
```bash
kubectl get pods -A
kubectl get services
```

## Security
- Do not commit API keys, passwords, or other secrets.
- Keep local credentials in environment variables or Kubernetes Secrets.
