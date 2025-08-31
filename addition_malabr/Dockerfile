# Use official Python slim image
FROM python:3.11-slim

# Disable bytecode generation & buffer output
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies (optional but helps in many cases)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first
RUN pip install --upgrade pip

# Set working directory
WORKDIR /app

# Copy your Flask app code
COPY modelserver/server.py .
COPY modelserver/myrequirements.txt .

# Install Python packages
RUN pip install -r myrequirements.txt
RUN pip install gunicorn

# Expose the Flask app port
EXPOSE 5000

# Ensure debug is off
# ENV FLASK_ENV=production
# ENV FLASK_DEBUG=0

# Run the Python Flask app
# CMD ["python3", "server.py"]
CMD ["gunicorn", "-k", "gthread", "-w", "1", "--threads", "5", "-b", "0.0.0.0:5000", "server:app"]

