FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir fastapi uvicorn requests

COPY . /app

EXPOSE 8787

CMD ["uvicorn", "tokyo_llm_proxy:app", "--host", "0.0.0.0", "--port", "8787"]
