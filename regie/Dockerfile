# Contexte de build = dossier contenant regie/ et button/brand.json (voir deploy.sh).
FROM node:22-bookworm-slim AS ui
WORKDIR /ui
COPY regie/ui/package.json regie/ui/package-lock.json ./
RUN npm ci
COPY regie/ui/ ./
RUN npm run build

FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl gnupg \
  && install -d /usr/share/postgresql-common/pgdg \
  && curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
  && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" > /etc/apt/sources.list.d/pgdg.list \
  && apt-get update && apt-get install -y --no-install-recommends postgresql-client-18 \
  && apt-get purge -y curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/* \
  && groupadd -g 568 apps && useradd -u 568 -g 568 -M -d /nonexistent -s /usr/sbin/nologin apps

WORKDIR /app
COPY regie/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY regie/src /app/src
COPY regie/migrations /app/migrations
COPY --from=ui /ui/dist /app/src/regie/static/app
COPY button/brand.json /app/src/regie/brand.json
ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1
ENV REGIE_DATA=/data
ENV REGIE_MEDIA_ROOT=/media

USER 568:568
EXPOSE 30130
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:30130/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "regie", "serve"]
