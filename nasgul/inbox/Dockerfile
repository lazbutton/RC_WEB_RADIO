FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci
COPY ui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

RUN groupadd -g 568 apps && useradd -u 568 -g 568 -M -d /nonexistent -s /usr/sbin/nologin apps

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src
COPY --from=ui /ui/dist /app/src/inboxzero/static/app
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV INBOXZERO_DATA=/data

USER 568:568
EXPOSE 30128
CMD ["python", "-m", "uvicorn", "inboxzero.web:app", "--host", "0.0.0.0", "--port", "30128"]
