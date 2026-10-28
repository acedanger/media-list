FROM python:3.13-alpine

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY app.py index.html ./

RUN adduser -D -H -u 10001 app
USER app

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD wget -qO- http://127.0.0.1:${PORT}/healthz || exit 1

CMD ["python", "app.py"]
