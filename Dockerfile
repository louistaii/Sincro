FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY 01_data/ 01_data/

ENV PYTHONPATH=src
ENV PYTHONUNBUFFERED=1
ENV SINCRO_SOLVER_WORKERS=1

CMD python -m sincro.web --host 0.0.0.0 --port ${PORT:-8080}
