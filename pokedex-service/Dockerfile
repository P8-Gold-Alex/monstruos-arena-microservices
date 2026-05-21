# pokemon-arena-microservices/pokedex-service/Dockerfile

FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para SQLite
RUN apt-get update && apt-get install -y --no-install-recommends \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Crear directorio de datos persistente
RUN mkdir -p ./src/data

EXPOSE 5001

CMD ["python", "src/app.py"]