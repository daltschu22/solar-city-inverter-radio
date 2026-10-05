FROM docker.io/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY collector.py config.py coordinator.py daylight.py history.py inverter.py radio_protocol.py smlight_collector.py container.py combined.py server.py ./
COPY static/ ./static/
COPY tools/*.py ./tools/
# Retain the existing JSON mount path without requiring a file for env-only runs.
RUN mkdir /data /config && chown 10001:10001 /data && ln -s /config/radio.local.json /app/radio.local.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOLAR_HISTORY_PATH=/data/solar-history.sqlite3 SOLAR_API_BIND=0.0.0.0 SOLAR_BIND=0.0.0.0 SOLAR_DASHBOARD=false
USER 10001:10001
EXPOSE 8766 8765
CMD ["python", "container.py"]
