#!/bin/bash
# Dave IT Guy Test Container — run this on your Mac
# Usage: bash test-container.sh

set -e

echo "Setting up Dave IT Guy test container..."

docker run --rm -it \
  -v /var/run/docker.sock:/var/run/docker.sock \
  python:3.11-slim bash -c '
set -e

echo "📦 Installing dependencies..."
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq git curl ca-certificates gnupg > /dev/null 2>&1

echo "🐳 Installing Docker CLI + Compose..."
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg 2>/dev/null
ARCH=$(dpkg --print-architecture)
echo "deb [arch=${ARCH} signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian bookworm stable" > /etc/apt/sources.list.d/docker.list
apt-get update -qq > /dev/null 2>&1
apt-get install -y -qq docker-ce-cli docker-compose-plugin > /dev/null 2>&1

echo "🐳 Docker version:"
docker --version
docker compose version

echo ""
echo "📥 Installing Dave IT Guy..."
pip install --quiet git+https://github.com/NeuroGamingLab/dave-it-guy.git

echo ""
echo "✅ Dave IT Guy installed!"
dave-it-guy version
echo ""

dave-it-guy deploy openclaw --port 19000
'
