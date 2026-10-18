import logging
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

logger = logging.getLogger(__name__)


def check_database():
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')


def check_cache():
    # Throttles and login locks live in the cache; a round trip shows Redis really answers.
    key = f'health:{uuid4().hex}'
    cache.set(key, 1, 10)
    if cache.get(key) != 1:
        raise RuntimeError('cache did not return the value')
    cache.delete(key)


@require_GET
def health(request):
    """For Docker and monitoring: no auth, no throttle; 503 if the database or the cache is down."""
    checks = {'database': check_database}
    # Without CACHE_URL the cache is in-process memory, which cannot be down.
    if settings.CACHE_URL:
        checks['cache'] = check_cache
    results = {}
    for name, check in checks.items():
        try:
            check()
        except Exception as exc:  # noqa: BLE001 -- any failure means unhealthy
            # The reason goes to the log only: the response must not reveal hosts or credentials.
            logger.error('Health check %s failed: %s', name, exc)
            results[name] = 'error'
        else:
            results[name] = 'ok'
    healthy = all(result == 'ok' for result in results.values())
    return JsonResponse(results, status=200 if healthy else 503)
