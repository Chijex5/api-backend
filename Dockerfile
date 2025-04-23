# Use official Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install OS-level deps (if needed later)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
 && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Set environment variables (optional)
ENV PYTHONUNBUFFERED=1

# Load .env automatically (you MUST use python-dotenv in your code)
# Otherwise you'll need to set env vars via Fly.io secrets

# Expose the port Flask runs on
EXPOSE 5000

# Run the app
CMD ["python", "server.py"]
