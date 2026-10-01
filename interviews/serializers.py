from rest_framework import serializers

from .models import Session


class SessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = ('id', 'name', 'status', 'celery_task_id', 'error_message', 'created_at', 'updated_at')
        read_only_fields = ('id', 'status', 'celery_task_id', 'error_message', 'created_at', 'updated_at')


class ImportSerializer(serializers.Serializer):
    text = serializers.CharField(required=False, max_length=100_000)
    file = serializers.FileField(required=False, write_only=True)

    def validate(self, attrs):
        if ('text' in attrs) == ('file' in attrs):
            raise serializers.ValidationError('Передай або text, або один TXT-файл.')
        if 'file' in attrs:
            upload = attrs['file']
            if not upload.name.lower().endswith('.txt') or upload.size > 100_000:
                raise serializers.ValidationError({'file': 'Потрібен TXT до 100 KB у UTF-8.'})
            try:
                text = upload.read().decode('utf-8-sig')
            except UnicodeDecodeError as exc:
                raise serializers.ValidationError({'file': 'Кодування файлу має бути UTF-8.'}) from exc
        else:
            text = attrs['text']
        questions = [line.strip() for line in text.splitlines() if line.strip()]
        if not 1 <= len(questions) <= 50:
            raise serializers.ValidationError('Потрібно від 1 до 50 питань: одне на рядок.')
        if any(len(question) > 2000 or '\x00' in question for question in questions):
            raise serializers.ValidationError('Питання: до 2000 символів, без нульових байтів.')
        return {'questions': questions}
