from pathlib import Path

from django.conf import settings
from rest_framework import serializers

from config.uploads import DOCUMENT_SUFFIXES, format_size
from questions.models import MAX_QUESTIONS_PER_SESSION

from .models import Session
from .parser import parse_document


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = ('id', 'name', 'status', 'error_message', 'created_at', 'updated_at')
        read_only_fields = ('id', 'status', 'error_message', 'created_at', 'updated_at')


class GenerateSerializer(serializers.Serializer):
    # 'missing' answers only questions without an AI answer; 'all' regenerates every one.
    mode = serializers.ChoiceField(choices=['missing', 'all'], default='missing')


class ImportSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, max_length=100_000)
    file = serializers.FileField(required=False, write_only=True)

    def validate(self, attrs):
        if ('text' in attrs) == ('file' in attrs):
            raise serializers.ValidationError('Передай або text, або один файл: TXT, PDF чи DOCX.')
        upload = attrs.get('file')
        suffix = Path(upload.name).suffix.lower() if upload is not None else ''
        if suffix in DOCUMENT_SUFFIXES:
            if upload.size > settings.DOCUMENT_UPLOAD_MAX_SIZE:
                raise serializers.ValidationError(
                    {'file': f'PDF або DOCX має бути до {format_size(settings.DOCUMENT_UPLOAD_MAX_SIZE)}.'}
                )
            questions = parse_document(upload, suffix)
            if not 1 <= len(questions) <= MAX_QUESTIONS_PER_SESSION:
                raise serializers.ValidationError(
                    f'Потрібно від 1 до {MAX_QUESTIONS_PER_SESSION} питань, а у файлі знайдено {len(questions)}. '
                    'Питанням вважається рядок зі знаком «?» у кінці або зі словом на кшталт «Поясни» на початку.'
                )
        else:
            if upload is not None:
                if suffix != '.txt' or upload.size > settings.FILE_UPLOAD_MAX_SIZE:
                    raise serializers.ValidationError({'file': (
                        f'Потрібен TXT до {format_size(settings.FILE_UPLOAD_MAX_SIZE)} у UTF-8 '
                        f'або PDF чи DOCX до {format_size(settings.DOCUMENT_UPLOAD_MAX_SIZE)}.'
                    )})
                try:
                    text = upload.read().decode('utf-8-sig')
                except UnicodeDecodeError as exc:
                    raise serializers.ValidationError({'file': 'Кодування файлу має бути UTF-8.'}) from exc
            else:
                text = attrs['text']
            questions = [line.strip() for line in text.splitlines() if line.strip()]
            if not 1 <= len(questions) <= MAX_QUESTIONS_PER_SESSION:
                raise serializers.ValidationError(
                    f'Потрібно від 1 до {MAX_QUESTIONS_PER_SESSION} питань: одне на рядок.'
                )
        if any(len(question) > 2000 or '\x00' in question for question in questions):
            raise serializers.ValidationError('Питання: до 2000 символів, без нульових байтів.')
        return {'questions': questions}


class ReorderSerializer(serializers.Serializer):
    # Every question of the session, in the new order.
    ids = serializers.ListField(
        child=serializers.IntegerField(), min_length=1, max_length=MAX_QUESTIONS_PER_SESSION,
    )
