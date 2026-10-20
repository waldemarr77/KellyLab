from unittest.mock import patch

import httpx
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from users.models import CustomUser

from .models import Session

QUESTIONS = ['Що таке ORM?', 'Поясни GIL']


def parser_reply(status_code=200, **body):
    return httpx.Response(status_code, json=body, request=httpx.Request('POST', 'http://parser/parse'))


@override_settings(PARSER_URL='http://parser')
class DocumentImportTests(APITestCase):
    def setUp(self):
        cache.clear()  # Throttle counters would otherwise carry over between tests.
        # Mock only the HTTP call to the Parser service; validation and saving run for real.
        parser_patch = patch('interviews.parser.httpx.post', return_value=parser_reply(questions=QUESTIONS))
        self.parser = parser_patch.start()
        self.addCleanup(parser_patch.stop)
        self.user = CustomUser.objects.create_user(username='owner', email='owner@example.com')
        self.client.force_authenticate(self.user)
        self.session = Session.objects.create(user=self.user, name='Python Junior')
        self.url = f'/api/sessions/{self.session.pk}/import/'

    def upload(self, name='questions.pdf', content=b'%PDF-1.4 file'):
        return self.client.post(self.url, {'file': SimpleUploadedFile(name, content)}, format='multipart')

    def test_pdf_and_docx_questions_come_from_parser(self):
        for name, sent_name in [('Питання.pdf', 'questions.pdf'), ('Questions.DOCX', 'questions.docx')]:
            with self.subTest(name=name):
                self.session.questions.all().delete()
                response = self.upload(name, b'file content')
                self.assertEqual(response.status_code, 201)
                self.assertEqual([question['text'] for question in response.data], QUESTIONS)
                self.assertEqual(self.parser.call_args.args, ('http://parser/parse',))
                # The whole file goes to the Parser, under a neutral name with the right extension.
                self.assertEqual(self.parser.call_args.kwargs['files'], {'file': (sent_name, b'file content')})
        self.assertEqual(list(self.session.questions.values_list('order', flat=True)), [1, 2])

    def test_txt_is_still_read_without_parser(self):
        response = self.upload('questions.txt', 'Що таке ORM?\nЩо таке GIL?'.encode())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.data), 2)
        self.parser.assert_not_called()

    def test_other_file_types_are_rejected_without_parser(self):
        for name in ['questions.doc', 'questions', 'questions.pdf.exe']:
            with self.subTest(name=name):
                response = self.upload(name)
                self.assertEqual(response.status_code, 400)
                self.assertIn('PDF чи DOCX', str(response.data['file']))
        self.parser.assert_not_called()

    @override_settings(DOCUMENT_UPLOAD_MAX_SIZE=2000)
    def test_documents_have_their_own_size_limit(self):
        response = self.upload('big.pdf', b'x' * 2001)
        self.assertEqual(response.status_code, 400)
        self.assertIn('2 KB', response.data['detail'])
        self.assertEqual(self.upload('fits.pdf', b'x' * 2000).status_code, 201)
        # A TXT file keeps its 100 KB limit, whatever the limit for documents is.
        with override_settings(DOCUMENT_UPLOAD_MAX_SIZE=5_000_000):
            response = self.upload('big.txt', b'q\n' * 60_000)
        self.assertEqual(response.status_code, 400)
        self.assertIn('100 KB', response.data['detail'])
        self.assertEqual(self.parser.call_count, 1)

    def test_parser_reason_for_bad_file_is_shown(self):
        for status_code in (400, 413):
            with self.subTest(status_code=status_code):
                self.parser.return_value = parser_reply(status_code, detail='PDF має забагато сторінок')
                response = self.upload()
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.data['file'], ['PDF має забагато сторінок'])
        # No detail, or a non-JSON body (e.g. a proxy's HTML error page): a generic message.
        self.parser.return_value = parser_reply(400)
        self.assertEqual(self.upload().data['file'], ['Не вдалося прочитати файл.'])
        self.parser.return_value = httpx.Response(
            413, content=b'<html>Request Entity Too Large</html>',
            request=httpx.Request('POST', 'http://parser/parse'),
        )
        self.assertEqual(self.upload().data['file'], ['Не вдалося прочитати файл.'])
        self.assertEqual(self.session.questions.count(), 0)

    def test_oversized_document_is_rejected_before_parsing(self):
        # The upload handler cuts an oversized file off first; this is the serializer's own guard.
        from .serializers import ImportSerializer
        upload = SimpleUploadedFile('big.pdf', b'%PDF-' + b'x' * 100)
        with override_settings(DOCUMENT_UPLOAD_MAX_SIZE=10):
            serializer = ImportSerializer(data={'file': upload})
            self.assertFalse(serializer.is_valid())
        self.assertIn('PDF або DOCX', str(serializer.errors['file']))
        self.parser.assert_not_called()

    def test_unavailable_parser_gives_503(self):
        replies = {
            'connection refused': httpx.ConnectError('refused'),
            'timeout': httpx.ReadTimeout('slow'),
            'server error': parser_reply(500, detail='Внутрішня помилка сервера'),
            'not json': httpx.Response(200, content=b'<html>', request=httpx.Request('POST', 'http://parser/parse')),
            'no questions field': parser_reply(total=2),
            'questions not a list': parser_reply(questions='Що таке ORM?'),
            'question not a string': parser_reply(questions=['Що таке ORM?', 5]),
        }
        for name, reply in replies.items():
            # Django logs every 5xx response as an error; here it is expected.
            with self.subTest(name), self.assertLogs('interviews.parser', 'WARNING'), self.assertLogs('django.request'):
                self.parser.side_effect = reply if isinstance(reply, Exception) else None
                self.parser.return_value = reply
                response = self.upload()
                self.assertEqual(response.status_code, 503)
                self.assertIn('завантаж TXT', response.data['detail'])
        self.assertEqual(self.session.questions.count(), 0)

    def test_parser_result_passes_the_same_limits(self):
        for questions in ([], [' '], [f'Питання {number}?' for number in range(51)], ['x' * 2001], ['bad\x00?']):
            with self.subTest(questions=str(questions)[:30]):
                self.parser.return_value = parser_reply(questions=questions)
                self.assertEqual(self.upload().status_code, 400)
        self.parser.return_value = parser_reply(questions=[])
        self.assertIn('знайдено 0', str(self.upload().data))
        self.assertEqual(self.session.questions.count(), 0)
