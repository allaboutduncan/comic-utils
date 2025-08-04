# syntax = docker/dockerfile:experimental

FROM python:3.9-slim

# Create a system group and user (using lower-case names avoids naming issues)
RUN addgroup --system comics && \
    adduser --system --ingroup comics cluser

# Prevents Python from writing pyc files.
ENV PYTHONDONTWRITEBYTECODE=1

# Keeps Python from buffering stdout and stderr
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    unar \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip
RUN pip3 install --upgrade pip --user

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies from requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the source code into the container.
COPY . .

# Create the required directories and set ownership
RUN mkdir -p /app/logs /app/static && \
    touch /app/logs/monitor.log && \
    chown -R cluser:comics /app/logs /app/static

# Expose the port that the application listens on.
EXPOSE 5577

# Switch to non-root user AFTER setting permissions
USER cluser

# Start the Flask app directly
CMD ["python", "app.py"]
