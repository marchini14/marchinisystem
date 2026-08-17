FROM python:3.11-slim

WORKDIR /app

# Zavisnosti prvo, da se Docker cache ne invalidira na svaku promjenu koda.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY config.yaml .

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

# state/ i logs/ montiraj kao volume, inace bot izgubi otvorene pozicije
# i istoriju pri svakom restartu kontejnera.
VOLUME ["/app/state", "/app/logs"]

CMD ["python", "-m", "marchini", "run"]
