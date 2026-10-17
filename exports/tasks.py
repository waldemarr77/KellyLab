import logging

import httpx
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone

from .models import Export, FormatExport, StatusExport
from .services import build_export_payload, content_hash

logger = logging.getLogger(__name__)

# Any other reply with status 200 (e.g. a proxy's HTML error page) must not be saved as a file.
FILE_SIGNATURES = {FormatExport.PDF: b'%PDF-', FormatExport.DOCX: b'PK\x03\x04'}


class BadExporterResponse(Exception):
    pass


def download_file(payload, export_format):
    """Ask the Exporter for the file; read at most EXPORT_MAX_BYTES of the reply."""
    with httpx.stream(
        'POST', f'{settings.EXPORTER_URL}/export', json=payload, timeout=settings.EXPORTER_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes():
            content += chunk
            if len(content) > settings.EXPORT_MAX_BYTES:
                raise BadExporterResponse('file too large')
    if not content.startswith(FILE_SIGNATURES[export_format]):
        raise BadExporterResponse('unexpected file type')
    return bytes(content)


@shared_task(soft_time_limit=5 * 60, time_limit=6 * 60)
def generate_export(export_id):
    export = Export.objects.select_related('session').filter(
        pk=export_id,
        status=StatusExport.PENDING).first()

    if export is None:
        return
    # The session can be deleted at any moment, so the row is changed with update(), never save():
    # save() on a deleted row raises instead of telling us the export is gone.
    row = Export.objects.filter(pk=export_id)
    if not row.filter(status=StatusExport.PENDING).update(status=StatusExport.PROCESSING, updated_at=timezone.now()):
        return

    payload = build_export_payload(export.session, export.format)
    try:
        content = download_file(payload, export.format)
        export.file.save(f'kellylab-{export.session_id}.{export.format}', ContentFile(content), save=False)
    except Exception as exc:  # noqa: BLE001 -- any failure (Exporter, disk, time limit) must end in failed
        logger.warning('Export %s failed: %s', export_id, type(exc).__name__)
        row.update(
            status=StatusExport.FAILED,
            error_message='Не вдалося створити файл. Спробуйте ще раз пізніше.',
            updated_at=timezone.now(),
        )
        return

    try:
        saved = row.update(
            file=export.file.name,
            # The session may have changed since the request; store the hash of what is really in the file.
            content_hash=content_hash(payload),
            status=StatusExport.READY,
            error_message='',
            completed_at=timezone.now(),
            updated_at=timezone.now(),
        )
    except Exception:
        export.file.storage.delete(export.file.name)
        raise
    if not saved:
        # The session was deleted while the Exporter worked: no row points to the file any more.
        export.file.storage.delete(export.file.name)
