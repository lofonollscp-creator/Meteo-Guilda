FROM python:3.12-slim
RUN useradd --create-home --uid 10001 app && mkdir /data && chown app /data
WORKDIR /app
COPY backend/ backend/
COPY web/ web/
RUN pip install --no-cache-dir ./backend
USER app
ENV MG_DB=/data/meteoguilda.db PYTHONUNBUFFERED=1
