FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Tsinghua mirror keeps builds fast on JD Cloud (China mainland)
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
RUN chmod +x docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)" || exit 1

CMD ["./docker-entrypoint.sh"]
