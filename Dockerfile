FROM python:3.12-slim

WORKDIR /app

# Install batch-specific dependencies (includes shared base)
COPY requirements.txt requirements-batch.txt ./
RUN pip install --no-cache-dir -r requirements-batch.txt

# Copy application code
COPY app/ ./app/

# Set Python to run in unbuffered mode
ENV PYTHONUNBUFFERED=1

# Run batch ingestion
CMD ["python", "-m", "app.batch_ingestion"]
