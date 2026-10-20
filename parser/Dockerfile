FROM python:3.12-slim

# Звичайний користувач: сервіс розбирає чужі файли, root йому не потрібен
RUN useradd -m -u 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Код належить root, тож процес сервісу не може його змінити
COPY . .

USER appuser

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
