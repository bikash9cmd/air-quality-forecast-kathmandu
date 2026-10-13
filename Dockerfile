# Local image: pipeline jobs and the HTTP API.
FROM python:3.12-slim
# LightGBM needs the OpenMP runtime.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY hawa ./hawa
ENV PYTHONUNBUFFERED=1 HAWA_DATA_DIR=/app/data HAWA_ARTIFACT_DIR=/app/artifacts
RUN useradd --create-home hawa && mkdir -p /app/data /app/artifacts && chown -R hawa /app
USER hawa
EXPOSE 8000
CMD ["python", "-m", "hawa", "serve", "--host", "0.0.0.0", "--port", "8000"]
