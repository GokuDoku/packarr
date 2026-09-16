FROM python:3.12-slim

# ffprobe is the only system dependency: Packarr verifies files, it never trusts a release name
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY packarr ./packarr
RUN pip install --no-cache-dir .

ENV PACKARR_CONFIG=/config/packarr.yml \
    PYTHONUNBUFFERED=1
VOLUME ["/config", "/downloads"]
EXPOSE 7979

# default: pipeline tick every 5 minutes. Override with `serve` for the webhook listener.
CMD ["packarr", "run", "--interval", "300"]
