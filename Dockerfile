FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY 01_data/ 01_data/

ENV PYTHONPATH=src

CMD python -m sincro.web --host 0.0.0.0 --port ${PORT:-8080}
