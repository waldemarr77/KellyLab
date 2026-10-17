from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import AIUsage, GlobalAIUsage


@shared_task
def delete_old_ai_usage():
    before = timezone.localdate() - settings.AI_USAGE_KEEP_FOR
    AIUsage.objects.filter(date__lt=before).delete()
    GlobalAIUsage.objects.filter(date__lt=before).delete()
