FROM python:3.12-slim

# Tesseract habilita o OCR de provas escaneadas (opcional no código, útil no dia a dia).
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-por tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY api.py pyproject.toml ./
COPY src ./src
COPY frontend ./frontend

ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8787
CMD ["python", "api.py", "--host", "127.0.0.1", "--port", "8787"]
