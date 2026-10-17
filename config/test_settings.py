from .settings import *

DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
SECRET_KEY = 'isolated-test-secret-key-at-least-32-characters'
ALLOWED_HOSTS = ['testserver', 'localhost']
# Tests talk plain http; HTTPS behaviour is tested with override_settings.
SECURE_SSL_REDIRECT = SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0
CELERY_TASK_ALWAYS_EAGER = True
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
# Tests must not depend on Redis even if CACHE_URL is set in .env.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
# Expected retries and failures are logged at INFO; keep test output to warnings, as before.
LOGGING['root']['level'] = 'WARNING'
# The manifest exists only after collectstatic; tests render admin pages without it.
STORAGES = {**STORAGES, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
