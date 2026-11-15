FROM python:3.11-slim

# Install ffmpeg, libass, and fonts for high-quality subtitle rendering
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-dejavu-core \
    fontconfig \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV LC_LAN=1 PYTHONUNBUFFERED=1
EXPOSE 8765

CMD ["python", "server.py"]
