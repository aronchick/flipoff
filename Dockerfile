FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.115.0 \
    uvicorn[standard]==0.32.0 \
    sse-starlette==2.1.3 \
    jinja2==3.1.4

COPY app /app/app
COPY static /app/static

ENV PYTHONUNBUFFERED=1
ENV STATE_PATH=/config/state.json
ENV AUTH_TOKEN=changeme

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
