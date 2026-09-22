FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        curl \
        bubblewrap \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY development_agents/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir -r requirements.txt

COPY development_agents /app

CMD ["python", "-m", "main"]
