FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# The app runs as an ordinary user; media is created here so a new volume gets its ownership.
RUN useradd --create-home --uid 1000 app && mkdir -p /app/media && chown app:app /app/media
COPY . .
# Settings refuse to load without SECRET_KEY; this one exists only for the build step.
RUN SECRET_KEY=collectstatic-only-not-used-at-runtime python manage.py collectstatic --noinput
USER app
EXPOSE 8000
# WEB_CONCURRENCY (read by gunicorn) sets the number of worker processes.
CMD ["gunicorn", "config.wsgi", "--bind", "0.0.0.0:8000", "--access-logfile", "-", "--timeout", "60"]
