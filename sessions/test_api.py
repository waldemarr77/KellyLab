from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from questions.models import Question
from questions.tasks import generate_session_answers
from users.models import CustomUser

from .models import Session


@override_settings(CELERY_TASK_ALWAYS_EAGER=True, AI_BACKEND='demo')
class InterviewFlowTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='owner', email='owner@example.com', password='Strong-password-762!',
        )
        self.other = CustomUser.objects.create_user(username='other', email='other@example.com')
        self.client.force_authenticate(self.user)
        self.session = Session.objects.create(user=self.user, name='Python Junior')
        self.url = f'/api/sessions/{self.session.pk}/'

    def import_text(self, text='Що таке ORM?\nЩо таке транзакція?'):
        return self.client.post(self.url + 'import/', {'text': text}, format='json')

    def test_complete_flow(self):
        created = self.client.post('/api/sessions/', {'name': 'Backend'}, format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(Session.objects.get(pk=created.data['id']).user, self.user)
        imported = self.import_text()
        self.assertEqual(imported.status_code, 201)
        generated = self.client.post(self.url + 'generate/')
        self.assertEqual(generated.status_code, 202)
        self.assertEqual(generated.data['status'], 'ready')
        cards = self.client.get(self.url + 'questions/').data['results']
        self.assertEqual(len(cards), 2)
        self.assertIn('[ДЕМО', cards[0]['ai_answer'])
        edited = self.client.patch(
            f'/api/questions/{cards[0]["id"]}/', {'user_answer': 'Моя відповідь'}, format='json',
        )
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.data['answer'], 'Моя відповідь')
        exported = self.client.get(self.url + 'export/')
        self.assertEqual(exported.status_code, 200)
        self.assertIn('Моя відповідь', exported.content.decode())
        self.assertIn('attachment;', exported['Content-Disposition'])

    def test_anonymous_access_is_denied(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/sessions/').status_code, 401)
        self.assertEqual(self.client.get(self.url + 'export/').status_code, 401)

    def test_other_user_cannot_read_or_modify_objects(self):
        question = Question.objects.create(session=self.session, text='Private')
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get('/api/sessions/').data['count'], 0)
        for suffix in ['', 'questions/', 'export/']:
            self.assertEqual(self.client.get(self.url + suffix).status_code, 404)
        for suffix in ['import/', 'generate/']:
            self.assertEqual(self.client.post(self.url + suffix, {'text': 'x'}).status_code, 404)
        self.assertEqual(self.client.delete(self.url).status_code, 404)
        self.assertEqual(self.client.patch(self.url, {'name': 'stolen'}).status_code, 404)
        self.assertEqual(self.client.get(f'/api/questions/{question.pk}/').status_code, 404)
        self.assertEqual(self.client.patch(f'/api/questions/{question.pk}/', {'text': 'x'}).status_code, 404)

    def test_client_cannot_assign_owner_or_status(self):
        response = self.client.post('/api/sessions/', {
            'name': 'Mine', 'user': self.other.pk, 'status': 'ready', 'celery_task_id': 'spoof',
        })
        session = Session.objects.get(pk=response.data['id'])
        self.assertEqual(session.user, self.user)
        self.assertEqual(session.status, 'created')
        self.assertIsNone(session.celery_task_id)

    def test_empty_session_cannot_generate(self):
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 400)

    def test_import_does_not_replace_existing_work(self):
        self.import_text()
        self.assertEqual(self.import_text('replacement').status_code, 400)
        self.assertEqual(self.session.questions.count(), 2)

    def test_import_validates_limits(self):
        for text in [' ', '\n'.join(['q'] * 51), 'x' * 2001, 'bad\x00question']:
            with self.subTest(text=text[:20]):
                self.assertEqual(self.import_text(text).status_code, 400)
        self.assertEqual(self.session.questions.count(), 0)

    def test_utf8_bom_file_import(self):
        upload = SimpleUploadedFile('questions.txt', '\ufeffЩо таке Python?\n\nЩо таке GIL?'.encode())
        response = self.client.post(self.url + 'import/', {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data[0]['text'], 'Що таке Python?')

    def test_invalid_files_and_ambiguous_input(self):
        for name, content in [('bad.pdf', b'pdf'), ('bad.txt', b'\xff'), ('huge.txt', b'x' * 100001)]:
            response = self.client.post(self.url + 'import/', {
                'file': SimpleUploadedFile(name, content),
            }, format='multipart')
            self.assertEqual(response.status_code, 400)
        response = self.client.post(self.url + 'import/', {
            'file': SimpleUploadedFile('q.txt', b'q'), 'text': 'q',
        }, format='multipart')
        self.assertEqual(response.status_code, 400)

    def test_processing_blocks_duplicate_runs_and_question_edits(self):
        self.import_text()
        self.session.status = 'processing'
        self.session.save()
        question = self.session.questions.first()
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 409)
        self.assertEqual(self.import_text().status_code, 409)
        self.assertEqual(self.client.delete(self.url).status_code, 409)
        self.assertEqual(self.client.get(self.url + 'export/').status_code, 409)
        self.assertEqual(self.client.patch(f'/api/questions/{question.pk}/', {'text': 'new'}).status_code, 409)

    @patch('sessions.views.generate_session_answers.apply_async', side_effect=ConnectionError('private'))
    def test_broker_failure_is_visible_and_retryable(self, publish):
        self.import_text()
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 503)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'failed')
        self.assertNotIn('private', self.session.error_message)

    @patch('questions.tasks.generate_answer', side_effect=['first', RuntimeError('secret-key')])
    def test_provider_failure_does_not_partially_write_answers(self, provider):
        self.import_text()
        self.client.post(self.url + 'generate/')
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'failed')
        self.assertNotIn('secret-key', self.session.error_message)
        self.assertFalse(self.session.questions.exclude(ai_answer=None).exists())
        provider.side_effect = None
        provider.return_value = 'Recovered'
        self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')

    @patch('questions.tasks.generate_answer')
    def test_old_task_cannot_overwrite_new_run(self, provider):
        self.session.status = 'processing'
        self.session.celery_task_id = 'new-run'
        self.session.save()
        generate_session_answers(self.session.pk, 'old-run')
        provider.assert_not_called()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'processing')

    def test_regeneration_preserves_user_answer(self):
        self.import_text()
        question = self.session.questions.first()
        question.user_answer = 'My own answer'
        question.save()
        self.client.post(self.url + 'generate/')
        self.client.post(self.url + 'generate/')
        question.refresh_from_db()
        self.assertEqual(question.user_answer, 'My own answer')

    def test_editing_question_invalidates_ai_answer(self):
        self.import_text()
        self.client.post(self.url + 'generate/')
        question = self.session.questions.first()
        result = self.client.patch(f'/api/questions/{question.pk}/', {'text': 'Нове питання?'})
        self.assertIsNone(result.data['ai_answer'])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'created')

    def test_blank_override_is_distinct_from_null(self):
        question = Question.objects.create(session=self.session, text='Q', ai_answer='AI')
        url = f'/api/questions/{question.pk}/'
        blank = self.client.patch(url, {'user_answer': ''}, format='json')
        self.assertEqual(blank.data['answer'], '')
        self.assertNotIn('AI', self.client.get(self.url + 'export/').content.decode())
        reset = self.client.patch(url, {'user_answer': None}, format='json')
        self.assertEqual(reset.data['answer'], 'AI')

    def test_schema_is_available(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/schema/?format=openapi').status_code, 200)
