FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py check_account.py ./

# state.json i bot.log se pisu ovdje — u Northflanku ovo mora biti
# trajni volume, inace se gube pri svakom redeployu/restartu.
VOLUME ["/app/data"]
ENV STATE_DIR=/app/data

CMD ["python", "bot.py"]
