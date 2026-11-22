FROM python:3.12-slim
WORKDIR /app
COPY backend/ backend/
COPY web/ web/
RUN pip install --no-cache-dir ./backend
ENV MG_DB=/data/meteoguilda.db
