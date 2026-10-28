FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock requirements.server.txt ./
RUN pip install --no-cache-dir -r requirements.server.txt \
    && groupadd --gid 10001 tracker \
    && useradd --uid 10001 --gid tracker --no-create-home tracker \
    && mkdir -p /app/data /app/staticfiles /backups \
    && chown -R tracker:tracker /app /backups
COPY --chown=tracker:tracker . /app
USER tracker
EXPOSE 8000
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"]
