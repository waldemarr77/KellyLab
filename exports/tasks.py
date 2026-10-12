import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import Export, StatusExport
from .services import build_export_payload, content_hash

logger = logging.getLogger(__name__)


@shared_task(soft_time_limit=5 * 60, time_limit=6 * 60)
def generate_export(export_id):
    export = Export.objects.select_related('session').filter(
        pk=export_id,
        status=StatusExport.PENDING).first()

    if export is None:
        return
    export.status = StatusExport.PROCESSING
    export.save(update_fields=['status', 'updated_at'])

    payload = build_export_payload(export.session, export.format)
    try:
        response = httpx.post(
            f'{settings.EXPORTER_URL}/export', json=payload, timeout=settings.EXPORTER_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        export.file.save(f'kellylab-{export.session_id}.{export.format}', ContentFile(response.content), save=False)
    except Exception as exc:  # noqa: BLE001 -- any failure (Exporter, disk, time limit) must end in failed
        logger.warning('Export %s failed: %s', export_id, type(exc).__name__)
        export.status = StatusExport.FAILED
        export.error_message = 'Не вдалося створити файл. Спробуйте ще раз пізніше.'
        export.save(update_fields=['status', 'error_message', 'updated_at'])
        return

    # The session may have changed since the request; store the hash of what is really in the file.
    export.content_hash = content_hash(payload)
    export.status = StatusExport.READY
    export.error_message = ''
    export.completed_at = timezone.now()
    export.save()
