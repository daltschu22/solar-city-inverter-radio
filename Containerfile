FROM docker.io/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
COPY tools/*.py ./tools/
COPY static/ ./static/
RUN mkdir /data && chown 10001:10001 /data
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOLAR_HISTORY_PATH=/data/solar-history.sqlite3 SOLAR_BIND=0.0.0.0 SOLAR_CONFIG=/config/radio.local.json
USER 10001:10001
EXPOSE 8765
CMD ["python", "server.py"]
