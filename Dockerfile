# Dockerfile for cloud deployment with Playwright support
FROM python:3.11-slim

# Install system dependencies required for Playwright/Chromium
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libwayland-client0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Set environment variables for Playwright BEFORE installing browsers
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV DISPLAY=:99

# Create directory for Playwright browsers and set permissions
RUN mkdir -p /ms-playwright && chmod -R 755 /ms-playwright

# Copy application code
COPY . .

# Install Playwright browsers (this is critical for cloud deployment)
# Must be done after installing Python packages
# System dependencies already installed above, so just install browser
RUN playwright install chromium

# Expose port for API
EXPOSE 8000

# Default command: Run the web API
CMD ["python", "app.py"]

