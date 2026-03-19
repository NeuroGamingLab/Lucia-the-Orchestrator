# MasterClaw: API server image. Worker runs in the same image with different entrypoint.
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn pydantic httpx docker

COPY app.py worker.py ./

# Default: run API. Worker is invoked with: docker run ... entrypoint ["python3", "/app/worker.py"]
EXPOSE 8090
CMD ["python3", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
