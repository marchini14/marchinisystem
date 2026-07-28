FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY agent.py .

EXPOSE 8080

CMD ["python", "agent.py"]
