from pathlib import Path

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler
from django.http.multipartparser import MultiPartParserError

# Read by the Parser service; every other file is treated as TXT.
DOCUMENT_SUFFIXES = ('.pdf', '.docx')


def upload_limit(file_name):
    if Path(file_name or '').suffix.lower() in DOCUMENT_SUFFIXES:
        return settings.DOCUMENT_UPLOAD_MAX_SIZE
    return settings.FILE_UPLOAD_MAX_SIZE


def format_size(size):
    return f'{size // 1_000_000} MB' if size >= 1_000_000 else f'{size // 1000} KB'


class FileSizeLimitUploadHandler(FileUploadHandler):
    """Aborts the request as soon as a file exceeds its limit instead of receiving it whole.

    Runs first, so the memory and temporary-file handlers never get more than the limit.
    DRF turns MultiPartParserError into a 400 response.
    """

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.limit = upload_limit(self.file_name)

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > self.limit:
            raise MultiPartParserError(f'файл більший за {format_size(self.limit)}.')
        return raw_data

    def file_complete(self, file_size):
        return None
