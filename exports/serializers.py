from rest_framework import serializers
from rest_framework.reverse import reverse

from .models import Export, FormatExport, StatusExport


class ExportCreateSerializer(serializers.Serializer):
    format = serializers.ChoiceField(choices=[FormatExport.PDF, FormatExport.DOCX])


class ExportSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Export
        fields = (
            'id',
            'format',
            'status',
            'error_message',
            'created_at',
            'completed_at',
            'download_url',
        )

    def get_download_url(self, export):
        if export.status != StatusExport.READY:
            return None
        return reverse(
            'session-export-download', args=[export.session_id, export.pk], request=self.context.get('request'),
        )
