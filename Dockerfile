FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Kolkata

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Keep SQLite data outside the ephemeral container filesystem.
VOLUME ["/app/data"]
EXPOSE 8080

CMD ["python", "bot.py"]
