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

RUN apt-get update && apt-get install -y git
RUN pip3 install --upgrade pip --user
RUN apt-get install unar -y
RUN apt-get install poppler-utils -y
RUN pip install --no-cache-dir flask rarfile pillow pdf2image watchdog self psutil requests flask-cors mega.py Pixeldrain

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
