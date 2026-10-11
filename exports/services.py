import hashlib
import json


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
