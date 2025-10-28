# ============================================================================
# DOCKERFILE - Cloud Run Container
# ============================================================================

FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY api_service.py .
COPY vertex_ai_integration.py .

# Expose port
EXPOSE 8080

# Run the application
CMD ["python", "api_service.py"]