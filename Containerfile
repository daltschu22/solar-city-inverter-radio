FROM docker.io/library/python:3.12-slim AS dashboard
WORKDIR /app
COPY server.py ./
COPY static/ ./static/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOLAR_BIND=0.0.0.0 SOLAR_COLLECTOR_URL=http://solar-city-collector:8766
USER 10001:10001
EXPOSE 8765
CMD ["python", "server.py"]

FROM docker.io/library/python:3.12-slim AS radio-runtime
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py config.py coordinator.py daylight.py history.py inverter.py radio_protocol.py smlight_collector.py ./
COPY tools/*.py ./tools/
# Retain the existing JSON mount path without requiring a file for env-only runs.
RUN mkdir /data /config && chown 10001:10001 /data && ln -s /config/radio.local.json /app/radio.local.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOLAR_HISTORY_PATH=/data/solar-history.sqlite3
USER 10001:10001

FROM radio-runtime AS combined
COPY combined.py server.py ./
COPY static/ ./static/
ENV SOLAR_API_BIND=127.0.0.1 SOLAR_BIND=0.0.0.0
EXPOSE 8765
CMD ["python", "combined.py"]

# The default build remains collector-only.
FROM radio-runtime AS collector
ENV SOLAR_API_BIND=0.0.0.0
EXPOSE 8766
CMD ["python", "collector.py"]
