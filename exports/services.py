import hashlib
import json

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import StatusExport


def reusable_export(session, export_format, payload_hash):
    """An export of the same content that is still running or has its file on disk."""
    stale_before = timezone.now() - settings.EXPORT_STALE_AFTER
    candidates = session.exports.filter(format=export_format, content_hash=payload_hash).filter(
        Q(status=StatusExport.READY)
        | Q(status__in=[StatusExport.PENDING, StatusExport.PROCESSING], updated_at__gt=stale_before)
    )
    for export in candidates:
        if export.status != StatusExport.READY or export.file.storage.exists(export.file.name):
            return export
    return None


def build_export_payload(session, export_format):
    """Everything that ends up in the file; the same dict is hashed and sent to the Exporter."""
    return {
        'format': export_format,
        'title': session.name,
        'questions': [
            {
                'order': question.order,
                'text': question.text,
                'answer': question.final_answer or '',
            }
            for question in session.questions.order_by('order', 'id')
        ],
    }


def content_hash(payload):
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
