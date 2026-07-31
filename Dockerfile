FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    build-essential \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
