from .settings import *

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
SECRET_KEY = 'isolated-test-secret-key-at-least-32-characters'
ALLOWED_HOSTS = ['testserver', 'localhost']
CELERY_TASK_ALWAYS_EAGER = True
AI_BACKEND = 'demo'
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
