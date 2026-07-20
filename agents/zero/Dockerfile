FROM python:3.11-slim

RUN apt-get update -qq && \
    apt-get install -y --no-install-recommends git curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir slither-analyzer

WORKDIR /app
COPY agent.py .

EXPOSE 8080

CMD ["python", "agent.py"]
