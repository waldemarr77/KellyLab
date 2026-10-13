import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from interviews.models import Session
from questions.models import Question
from users.models import CustomUser

from .models import Export

FAKE_FILE = b'%PDF-1.4 fake file'


def exporter_response(status_code=200, content=FAKE_FILE):
    return httpx.Response(status_code, content=content, request=httpx.Request('POST', 'http://exporter/export'))


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, EXPORTER_URL='http://exporter')
class ExportTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle counters would otherwise carry over between tests.
        # Keep generated files out of the real media folder.
        media_root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, media_root, ignore_errors=True)
        media_settings = override_settings(MEDIA_ROOT=media_root)
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        # Mock only the HTTP call to the Exporter service; our task code runs for real.
        exporter_patch = patch('exports.tasks.httpx.post', return_value=exporter_response())
        self.exporter = exporter_patch.start()
        self.addCleanup(exporter_patch.stop)
        self.user = CustomUser.objects.create_user(username='owner', email='owner@example.com')
        self.other = CustomUser.objects.create_user(username='other', email='other@example.com')
        self.client.force_authenticate(self.user)
        self.session = Session.objects.create(user=self.user, name='Python Junior')
        Question.objects.create(session=self.session, text='Що таке ORM?', ai_answer='Відповідь')
        self.url = f'/api/sessions/{self.session.pk}/exports/'

    def test_pdf_and_docx_are_created_and_downloaded(self):
        for export_format in ['pdf', 'docx']:
            with self.subTest(format=export_format):
                created = self.client.post(self.url, {'format': export_format}, format='json')
                self.assertEqual(created.status_code, 202)
                self.assertEqual(created.data['status'], 'ready')
                status = self.client.get(f'{self.url}{created.data["id"]}/')
                self.assertEqual(status.data['download_url'], created.data['download_url'])
                downloaded = self.client.get(created.data['download_url'])
                self.assertEqual(downloaded.status_code, 200)
                self.assertEqual(b''.join(downloaded.streaming_content), FAKE_FILE)
                self.assertIn(f'.{export_format}', downloaded['Content-Disposition'])
        sent = self.exporter.call_args.kwargs['json']
        self.assertEqual(sent['questions'], [{'order': 1, 'text': 'Що таке ORM?', 'answer': 'Відповідь'}])

    def test_repeat_request_reuses_ready_file(self):
        first = self.client.post(self.url, {'format': 'pdf'}, format='json')
        second = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['id'], first.data['id'])
        self.assertEqual(self.exporter.call_count, 1)

    def test_changed_answer_creates_new_file(self):
        self.client.post(self.url, {'format': 'pdf'}, format='json')
        Question.objects.filter(session=self.session).update(user_answer='Нова відповідь')
        self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(self.exporter.call_count, 2)

    def test_other_user_cannot_see_export(self):
        export_id = self.client.post(self.url, {'format': 'pdf'}, format='json').data['id']
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.url, {'format': 'pdf'}, format='json').status_code, 404)
        self.assertEqual(self.client.get(f'{self.url}{export_id}/').status_code, 404)
        self.assertEqual(self.client.get(f'{self.url}{export_id}/download/').status_code, 404)

    def test_exporter_error_marks_export_failed(self):
        self.exporter.return_value = exporter_response(status_code=500, content=b'')
        created = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(created.data['status'], 'failed')
        self.assertIsNone(created.data['download_url'])
        self.assertFalse(Export.objects.get(pk=created.data['id']).file)
        self.assertEqual(self.client.get(f'{self.url}{created.data["id"]}/download/').status_code, 404)

    def test_failed_export_can_be_retried(self):
        self.exporter.return_value = exporter_response(status_code=500, content=b'')
        self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.exporter.return_value = exporter_response()
        retried = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(retried.status_code, 202)
        self.assertEqual(retried.data['status'], 'ready')

    def test_unknown_format_and_empty_session_are_rejected(self):
        self.assertEqual(self.client.post(self.url, {'format': 'txt'}, format='json').status_code, 400)
        Question.objects.filter(session=self.session).delete()
        self.assertEqual(self.client.post(self.url, {'format': 'pdf'}, format='json').status_code, 400)
        self.exporter.assert_not_called()

    def test_stuck_export_does_not_block_new_one(self):
        first = self.client.post(self.url, {'format': 'pdf'}, format='json')
        old = timezone.now() - settings.EXPORT_STALE_AFTER - timedelta(minutes=1)
        Export.objects.filter(pk=first.data['id']).update(status='processing', updated_at=old)
        second = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(second.data['id'], first.data['id'])
        self.assertEqual(second.data['status'], 'ready')

    def test_running_export_is_reused(self):
        first = self.client.post(self.url, {'format': 'pdf'}, format='json')
        Export.objects.filter(pk=first.data['id']).update(status='processing')
        second = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.data['id'], first.data['id'])

    def test_unexpected_error_marks_export_failed(self):
        self.exporter.side_effect = RuntimeError('boom')
        created = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(created.data['status'], 'failed')

    def test_missing_file_gives_404_and_is_regenerated(self):
        first = self.client.post(self.url, {'format': 'pdf'}, format='json')
        Export.objects.get(pk=first.data['id']).file.delete(save=False)
        self.assertEqual(self.client.get(first.data['download_url']).status_code, 404)
        second = self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(second.status_code, 202)
        self.assertEqual(self.exporter.call_count, 2)

    def test_frequent_exports_are_throttled(self):
        limit = int(settings.REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']['export'].split('/')[0])
        for _ in range(limit):
            self.client.post(self.url, {'format': 'pdf'}, format='json')
        self.assertEqual(self.client.post(self.url, {'format': 'pdf'}, format='json').status_code, 429)

    def test_deleting_session_or_account_removes_export_files(self):
        created = self.client.post(self.url, {'format': 'pdf'}, format='json')
        export = Export.objects.get(pk=created.data['id'])
        storage, name = export.file.storage, export.file.name
        self.assertTrue(storage.exists(name))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.client.delete(f'/api/sessions/{self.session.pk}/').status_code, 204)
        self.assertFalse(storage.exists(name))
        session = Session.objects.create(user=self.user, name='Second')
        Question.objects.create(session=session, text='Q', ai_answer='A')
        created = self.client.post(f'/api/sessions/{session.pk}/exports/', {'format': 'docx'}, format='json')
        name = Export.objects.get(pk=created.data['id']).file.name
        with self.captureOnCommitCallbacks(execute=True):
            self.user.delete()
        self.assertFalse(storage.exists(name))
