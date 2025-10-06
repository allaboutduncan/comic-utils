# syntax = docker/dockerfile:1

FROM python:3.9-slim

# Avoid interactive tzdata etc.
ENV DEBIAN_FRONTEND=noninteractive

# Install system deps + tini (init) + gosu (priv drop) + Playwright dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
      git \
      unar \
      poppler-utils \
      tini \
      gosu \
      wget \
      gnupg \
      ca-certificates \
      fonts-liberation \
      libasound2 \
      libatk-bridge2.0-0 \
      libatk1.0-0 \
      libc6 \
      libcairo2 \
      libcups2 \
      libdbus-1-3 \
      libexpat1 \
      libfontconfig1 \
      libgbm1 \
      libgcc1 \
      libglib2.0-0 \
      libgtk-3-0 \
      libnspr4 \
      libnss3 \
      libpango-1.0-0 \
      libpangocairo-1.0-0 \
      libstdc++6 \
      libx11-6 \
      libx11-xcb1 \
      libxcb1 \
      libxcomposite1 \
      libxcursor1 \
      libxdamage1 \
      libxext6 \
      libxfixes3 \
      libxi6 \
      libxrandr2 \
      libxrender1 \
      libxss1 \
      libxtst6 \
      lsb-release \
      xdg-utils \
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

# Install Playwright browsers (chromium only for scraping)
# Note: We skip playwright install-deps because it fails on Debian Trixie
# All required dependencies are already installed in the earlier apt-get step
RUN playwright install chromium

# Copy source
COPY . .

# Create runtime dirs that the app writes to
# (keep root here; we'll chown at runtime based on PUID/PGID)
RUN mkdir -p /app/logs /app/static /config /data /downloads/temp /downloads/processed

# Ensure /app/templates is readable by all users (fix for non-root access)
RUN chmod -R 755 /app/templates

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
