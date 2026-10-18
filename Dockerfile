# One image, two jobs:
#   web    (default) the BatchWatch site and API, for Nebius Serverless Endpoints or a Hugging Face Docker Space
#   watch  one smart-update pass, for a Nebius Serverless Job; publishes to the repo's "data" branch
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=7860
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd --create-home bw && chown -R bw /app
USER bw

EXPOSE 7860
ENTRYPOINT ["sh", "deploy/entrypoint.sh"]
CMD ["web"]
