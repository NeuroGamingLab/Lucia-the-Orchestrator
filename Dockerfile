# Dave IT Guy — CLI image for deploy from Docker (mount Docker socket + data dir).
# Build:  docker build -t dave-it-guy:local .
# Deploy: docker run --rm -it -v /var/run/docker.sock:/var/run/docker.sock \
#           -v ~/.dave_it_guy:/root/.dave_it_guy dave-it-guy:local deploy openclaw --force

FROM python:3.11-slim

WORKDIR /app

# docker.io + docker-compose (v1) so `docker compose` or `docker-compose` works when socket is mounted
RUN apt-get update && apt-get install -y --no-install-recommends \
    docker.io \
    docker-compose \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && test -x /usr/bin/docker && ln -sf /usr/bin/docker /usr/local/bin/docker

COPY pyproject.toml README.md ./
COPY dave_it_guy ./dave_it_guy

RUN pip install --no-cache-dir .

ENTRYPOINT ["dave-it-guy"]
CMD ["--help"]
