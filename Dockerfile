FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements-runtime.txt .
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY . .
# Local development only. Use a production application server for deployment.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000", "--noreload"]
