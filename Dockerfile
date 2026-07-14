FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

EXPOSE 8080

CMD ["minibridge", "serve", "--host", "0.0.0.0", "--config", "/config/minibridge.demo.json", "--state-file", "/data/minibridge-state.json", "--signing-key-file", "/data/minibridge-signing.key", "--public-key-file", "/data/minibridge-signing.key.pub"]
