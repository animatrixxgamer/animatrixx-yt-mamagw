FROM python:3.12-slim

# System deps: ffmpeg lets yt-dlp merge best video+audio (1080p) and
# lets the bot write video thumbnails.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer caching — dependencies change rarely).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy only what the bot needs. Nothing else belongs in the image.
COPY bot.py .
COPY Procfile .
COPY railway.toml .

RUN mkdir -p storage

EXPOSE 8080

CMD ["python", "bot.py"]
