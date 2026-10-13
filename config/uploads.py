from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler
from django.http.multipartparser import MultiPartParserError


class FileSizeLimitUploadHandler(FileUploadHandler):
    """Aborts the request as soon as a file exceeds FILE_UPLOAD_MAX_SIZE instead of receiving it whole.

    Runs first, so the memory and temporary-file handlers never get more than the limit.
    DRF turns MultiPartParserError into a 400 response.
    """

    def receive_data_chunk(self, raw_data, start):
        if start + len(raw_data) > settings.FILE_UPLOAD_MAX_SIZE:
            raise MultiPartParserError(f'файл більший за {settings.FILE_UPLOAD_MAX_SIZE // 1000} KB.')
        return raw_data

    def file_complete(self, file_size):
        return None
