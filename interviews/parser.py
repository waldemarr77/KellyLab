import logging

import httpx
from django.conf import settings
from rest_framework import serializers
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)


class ParserUnavailable(APIException):
    status_code = 503
    default_detail = 'Сервіс розбору PDF і DOCX недоступний. Спробуй пізніше або завантаж TXT.'


def rejection_reason(response):
    """The Parser explains in Ukrainian what is wrong with the file (damaged, too many pages and so on)."""
    try:
        detail = response.json().get('detail')
    except (ValueError, AttributeError):
        detail = None
    return detail[:500] if isinstance(detail, str) and detail else 'Не вдалося прочитати файл.'


def parse_document(upload, suffix):
    """Questions that the Parser service found in a PDF or DOCX upload."""
    try:
        response = httpx.post(
            f'{settings.PARSER_URL}/parse',
            # The Parser needs only the extension; the user's file name stays out of its logs.
            files={'file': (f'questions{suffix}', upload.read())},
            timeout=settings.PARSER_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError as exc:
        logger.warning('Parser request failed: %s', type(exc).__name__)
        raise ParserUnavailable from exc
    if response.status_code in (400, 413):
        raise serializers.ValidationError({'file': rejection_reason(response)})
    try:
        response.raise_for_status()
        questions = response.json()['questions']
        if not isinstance(questions, list) or not all(isinstance(question, str) for question in questions):
            raise TypeError('questions must be a list of strings')
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        logger.warning('Parser reply %s is unusable: %s', response.status_code, type(exc).__name__)
        raise ParserUnavailable from exc
    return [question.strip() for question in questions if question.strip()]
