from celery import shared_task
from django.core.management import call_command


@shared_task
def flush_expired_tokens():
    # Every refresh and logout adds a blacklisted token; expired ones can no longer be used anyway.
    call_command('flushexpiredtokens')
