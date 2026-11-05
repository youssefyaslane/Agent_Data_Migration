FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/tmp

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY script/ script/
COPY app/ app/

# data/ (name_mapping.json) et output/ sont montés en volumes par docker compose
RUN mkdir -p data output && chmod 777 data output

EXPOSE 8080

CMD ["gunicorn", "-c", "script/gunicorn.conf.py"]
