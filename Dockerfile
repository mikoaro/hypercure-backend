# Use Python 3.11 for maximum compatibility with Confluent Kafka wheels
FROM python:3.14-slim

WORKDIR /code

# 1. Install System Dependencies (CRITICAL for Confluent Kafka)
# 'gcc', 'libc-dev', and 'librdkafka-dev' are required to compile the C extension
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc-dev \
    librdkafka-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Install Python Dependencies
COPY ./requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir --upgrade -r /code/requirements.txt

# 3. Copy Application Code
COPY ./app /code/app

# 4. Run Command (Use $PORT provided by Cloud Run)
# --proxy-headers is required for Cloud Run Load Balancer
CMD ["sh", "-c", "ddtrace-run uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]