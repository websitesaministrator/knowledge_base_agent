FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system deps (needed for sentence-transformers)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8080

# Start FastAPI with uvicorn
# PORT is set by Cloud Run; default to 8080
CMD uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}
