from hashlib import sha256

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from rest_framework.throttling import BaseThrottle


def failure_keys(request, email):
    """Cache keys and limits for failed logins: per email (distributed attack) and per IP (admin has no throttle)."""
    digest = sha256(email.strip().lower().encode()).hexdigest()
    keys = [(f'login-failures:email:{digest}', settings.LOGIN_FAILURE_LIMIT_PER_EMAIL)]
    if request is not None:
        # Same client IP as the DRF throttles, so NUM_PROXIES is respected here too.
        ip = BaseThrottle().get_ident(request)
        keys.append((f'login-failures:ip:{ip}', settings.LOGIN_FAILURE_LIMIT_PER_IP))
    return keys


def login_locked(request, email):
    return any(cache.get(key, 0) >= limit for key, limit in failure_keys(request, email))


def record_failure(request, email):
    timeout = settings.LOGIN_FAILURE_WINDOW.total_seconds()
    for key, _ in failure_keys(request, email):
        # A fixed window from the first failure: add() does not extend an existing key.
        cache.add(key, 0, timeout)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout)


class LoginLimitBackend(ModelBackend):
    """ModelBackend that refuses to check passwords after too many failures; used by the API and the admin."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        email = username or kwargs.get(get_user_model().USERNAME_FIELD) or ''
        if login_locked(request, email):
            # Stops other backends as well; the caller sees an ordinary failed login.
            raise PermissionDenied
        user = super().authenticate(request, username, password, **kwargs)
        if user is None:
            record_failure(request, email)
        return user
