FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Непривілейований користувач, під яким виконується код; без дому і без shell.
RUN useradd -r -u 1001 -M -s /usr/sbin/nologin sandbox

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8003

# Сервер стартує root-ом лише щоб перед кожним запуском скинути права до sandbox (див. sandbox.py).
# Контейнер забирає всі capabilities, крім SETUID/SETGID, і забороняє їх підвищення (compose.yaml).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "2"]
