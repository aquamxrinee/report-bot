FROM python:3.13-slim

WORKDIR /app

# Копируем и устанавливаем Python-зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной проект
COPY . .

# Делаем start.sh исполняемым
RUN chmod +x start.sh

CMD ["bash", "start.sh"]
