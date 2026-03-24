FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# Optional at runtime: OPENAI_API_KEY, AYRSHARE_API_KEY, JWT_SECRET_KEY, DATABASE_URL

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Render and other hosts inject PORT at runtime (default 8000 for local Docker).
EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
