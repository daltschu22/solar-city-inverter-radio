FROM docker.io/library/python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-cache --python /usr/local/bin/python
ENV PATH="/app/.venv/bin:$PATH"
COPY collector/ ./collector/
COPY dashboard/ ./dashboard/
COPY runtime/ ./runtime/
COPY tools/*.py ./tools/
# Retain the existing JSON mount path without requiring a file for env-only runs.
RUN mkdir /data /config && chown 10001:10001 /data && ln -s /config/radio.local.json /app/radio.local.json
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SOLAR_HISTORY_PATH=/data/solar-history.sqlite3 SOLAR_API_BIND=0.0.0.0 SOLAR_BIND=0.0.0.0 SOLAR_DASHBOARD=false
USER 10001:10001
EXPOSE 8766 8765
CMD ["python", "-m", "runtime"]
