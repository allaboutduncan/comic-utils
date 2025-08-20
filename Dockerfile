# syntax = docker/dockerfile:1

FROM python:3.9-slim

# Avoid interactive tzdata etc.
ENV DEBIAN_FRONTEND=noninteractive

# Install system deps + tini (init) + gosu (priv drop)
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      unar \
      poppler-utils \
      tini \
      gosu \
    && rm -rf /var/lib/apt/lists/*

# Prevent Python from writing .pyc & buffer issues
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# App workdir
WORKDIR /app

# Copy only requirements first for better layer caching
COPY requirements.txt .

# Upgrade pip and install deps
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy source
COPY . .

# Create runtime dirs that the app writes to
# (keep root here; we'll chown at runtime based on PUID/PGID)
RUN mkdir -p /app/logs /app/static /config /data /downloads/temp /downloads/processed

# Expose Flask port
EXPOSE 5577

# Set sane defaults for Unraid, but allow override for WSL/others
ENV PUID=99 \
    PGID=100 \
    UMASK=022 \
    FLASK_ENV=development \
    MONITOR=no

# Add entrypoint
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Use tini as PID 1, then our entrypoint will drop privileges via gosu
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]

# Default command runs the app; entrypoint will exec under gosu user
CMD ["python", "app.py"]
