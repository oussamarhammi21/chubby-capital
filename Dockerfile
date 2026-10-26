FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 TZ=America/Toronto
WORKDIR /app
COPY pyproject.toml README.md ./
COPY chubby ./chubby
RUN pip install --no-cache-dir .

VOLUME /app/data
CMD ["chubby", "serve"]
