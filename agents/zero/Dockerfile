FROM python:3.11-slim

RUN DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        git curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir slither-analyzer solc-select && \
    solc-select install \
        0.8.28 0.8.24 0.8.20 0.8.17 0.8.13 0.8.10 0.8.7 0.8.4 0.8.0 \
        0.7.6 0.7.4 0.7.0 \
        0.6.12 0.6.8 0.6.6 \
        0.5.17 0.5.16 && \
    solc-select use 0.8.28

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY agent.py .

EXPOSE 8080

CMD ["python", "agent.py"]
