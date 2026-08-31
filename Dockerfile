FROM python:3.14-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services ./services

CMD ["python", "-m", "uvicorn", "services.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]