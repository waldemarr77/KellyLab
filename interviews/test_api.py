import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import httpx
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from rest_framework.test import APITestCase

from questions.ai import render_answer
from questions.models import Question
from questions.tasks import generate_session_answers
from users.models import CustomUser

from .models import AIUsage, GlobalAIUsage, Session
from .tasks import delete_old_ai_usage


def parts(answer):
    return {'explanation': f'Пояснення до {answer}', 'example': '', 'interview_answer': answer, 'common_mistake': ''}


def rendered(answer):
    """The ai_answer text saved for a model reply built by ai_response(answer)."""
    return render_answer(parts(answer))


def ai_response(answer, finish_reason=genai_types.FinishReason.STOP):
    text = None if answer is None else json.dumps(parts(answer), ensure_ascii=False)
    return MagicMock(text=text, candidates=[MagicMock(finish_reason=finish_reason)])


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True, GEMINI_API_KEY='test-key', GEMINI_MODEL='test-model', AI_RETRY_DELAY_SECONDS=0,
    AI_MIN_INTERVAL_SECONDS=0, AI_RATE_LIMIT_RETRY_DELAY_SECONDS=0,
)
class InterviewFlowTests(APITestCase):
    def setUp(self):
        # Throttle counters live in the cache and would leak between tests.
        cache.clear()
        # Mock only the external SDK; exercise our real provider and task code.
        client_patch = patch('google.genai.Client')
        self.addCleanup(client_patch.stop)
        self.ai_client = client_patch.start().return_value.__enter__.return_value
        self.ai_client.models.generate_content.return_value = ai_response('Test provider answer')
        self.user = CustomUser.objects.create_user(
            username='owner', email='owner@example.com', password='Strong-password-762!', email_verified=True,
        )
        self.other = CustomUser.objects.create_user(
            username='other', email='other@example.com', email_verified=True,
        )
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
        self.assertEqual(cards[0]['ai_answer'], rendered('Test provider answer'))
        self.assertEqual(cards[0]['ai_parts'], parts('Test provider answer'))
        self.assertEqual(self.ai_client.models.generate_content.call_count, 2)
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

    @override_settings(GEMINI_API_KEY='', GEMINI_MODEL='')
    def test_missing_gemini_configuration_fails_without_saving_answers(self):
        self.import_text()
        with self.assertLogs('questions.tasks', 'WARNING') as logs:
            response = self.client.post(self.url + 'generate/')
        self.assertIn('question None: ImproperlyConfigured', '\n'.join(logs.output))
        self.assertEqual(response.data['status'], 'failed')
        self.assertFalse(self.session.questions.exclude(ai_answer=None).exists())
        self.ai_client.models.generate_content.assert_not_called()

    def test_import_does_not_replace_existing_work(self):
        self.import_text()
        self.assertEqual(self.import_text('replacement').status_code, 400)
        self.assertEqual(self.session.questions.count(), 2)

    def test_import_validates_limits(self):
        for text in [' ', '\n'.join(['q'] * 51), 'x' * 2001, 'bad\x00question']:
            with self.subTest(text=text[:20]):
                self.assertEqual(self.import_text(text).status_code, 400)
        self.assertEqual(self.session.questions.count(), 0)

    def test_oversized_file_is_cut_off_while_uploading(self):
        upload = SimpleUploadedFile('huge.txt', b'q\n' * 2_500_000)
        with patch('django.core.files.uploadhandler.TemporaryFileUploadHandler.receive_data_chunk') as to_disk:
            response = self.client.post(self.url + 'import/', {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 400)
        self.assertIn('100 KB', response.data['detail'])
        # At most the allowed 100 KB reached the temporary file, not the whole 5 MB body.
        self.assertLessEqual(sum(len(call.args[0]) for call in to_disk.call_args_list), 100_000)
        self.assertEqual(self.session.questions.count(), 0)

    def test_utf8_bom_file_import(self):
        upload = SimpleUploadedFile('questions.txt', '\ufeffЩо таке Python?\n\nЩо таке GIL?'.encode())
        response = self.client.post(self.url + 'import/', {'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data[0]['text'], 'Що таке Python?')

    def test_invalid_files_and_ambiguous_input(self):
        for name, content in [('notes.md', b'q'), ('bad.txt', b'\xff'), ('huge.txt', b'x' * 100001)]:
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

    @patch('interviews.views.generate_session_answers.apply_async', side_effect=ConnectionError('private'))
    def test_broker_failure_is_visible_and_retryable(self, publish):
        self.import_text()
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 503)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'failed')
        self.assertNotIn('private', self.session.error_message)
        # Nothing reached Gemini, so the quota is not spent.
        self.assertEqual(AIUsage.objects.get(user=self.user).questions, 0)
        self.assertEqual(GlobalAIUsage.objects.get().questions, 0)

    def test_provider_failure_keeps_finished_answers(self):
        provider = self.ai_client.models.generate_content
        server_error = genai_errors.ServerError(503, {'error': {'message': 'secret-key'}})
        provider.side_effect = [ai_response('first'), *[server_error] * 3]
        self.import_text()
        with self.assertLogs('questions.tasks', 'INFO') as logs:
            self.client.post(self.url + 'generate/')
        log = '\n'.join(logs.output)
        self.assertIn('ServerError (status 503), retrying (attempt 1 of 3)', log)
        for private in ('secret-key', 'test-key', 'Що таке ORM?', 'Що таке транзакція?'):
            self.assertNotIn(private, log)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'failed')
        self.assertNotIn('secret-key', self.session.error_message)
        first, second = self.session.questions.order_by('order')
        self.assertIn(f'session {self.session.pk}, question {second.pk}: ServerError (status 503)', log)
        self.assertEqual(first.ai_answer, rendered('first'))
        self.assertIsNone(second.ai_answer)
        provider.side_effect = None
        provider.return_value = ai_response('Recovered')
        self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')

    def test_transient_provider_error_is_retried(self):
        provider = self.ai_client.models.generate_content
        provider.side_effect = [httpx.ReadTimeout('timeout'), ai_response('A1'), ai_response('A2')]
        self.import_text()
        self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')
        self.assertEqual(provider.call_count, 3)
        self.assertEqual(list(self.session.questions.values_list('ai_answer', flat=True)), [rendered('A1'), rendered('A2')])

    def test_permanent_provider_error_fails_without_retry(self):
        provider = self.ai_client.models.generate_content
        provider.side_effect = genai_errors.ClientError(401, {'error': {'message': 'API key not valid'}})
        self.import_text()
        with self.assertLogs('questions.tasks', 'WARNING') as logs:
            self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'failed')
        log = '\n'.join(logs.output)
        self.assertIn('ClientError (status 401)', log)
        self.assertNotIn('API key not valid', log)
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(self.session.questions.exclude(ai_answer=None).exists())

    def test_empty_provider_response_is_logged_by_type(self):
        self.ai_client.models.generate_content.return_value.text = '   '
        self.import_text()
        with self.assertLogs('questions.tasks', 'WARNING') as logs:
            self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')
        self.assertIn('EmptyAIResponse', '\n'.join(logs.output))

    def test_bad_answer_for_one_question_does_not_block_the_rest(self):
        provider = self.ai_client.models.generate_content
        blocked = ai_response(None, genai_types.FinishReason.SAFETY)
        provider.side_effect = [ai_response('A1'), blocked, ai_response('A3')]
        self.import_text('Q1\nQ2\nQ3')
        with self.assertLogs('questions.tasks', 'WARNING') as logs:
            result = self.client.post(self.url + 'generate/')
        self.assertIn('IncompleteAIResponse (SAFETY)', '\n'.join(logs.output))
        self.assertEqual(result.data['status'], 'ready')
        self.assertIn('1 з 3', result.data['error_message'])
        cards = self.client.get(self.url + 'questions/').data['results']
        self.assertEqual([card['ai_answer'] for card in cards], [rendered('A1'), None, rendered('A3')])
        self.assertEqual([bool(card['ai_error']) for card in cards], [False, True, False])
        # The next run retries only the failed question and clears its error.
        provider.side_effect = None
        provider.return_value = ai_response('A2')
        self.assertEqual(self.client.post(self.url + 'generate/').data['error_message'], '')
        second = self.session.questions.get(order=2)
        self.assertEqual((second.ai_answer, second.ai_error), (rendered('A2'), ''))
        self.assertEqual(AIUsage.objects.get(user=self.user).questions, 4)

    def test_truncated_answer_is_not_saved(self):
        self.ai_client.models.generate_content.return_value = ai_response(
            'Половина відпов', genai_types.FinishReason.MAX_TOKENS,
        )
        self.import_text('Q1')
        with self.assertLogs('questions.tasks', 'WARNING'):
            self.client.post(self.url + 'generate/')
        question = self.session.questions.get()
        self.assertIsNone(question.ai_answer)
        self.assertIn('обрізалась', question.ai_error)

    def test_failed_regeneration_keeps_previous_answer(self):
        self.import_text('Q1')
        self.client.post(self.url + 'generate/')
        self.ai_client.models.generate_content.return_value = ai_response(None, genai_types.FinishReason.SAFETY)
        with self.assertLogs('questions.tasks', 'WARNING'):
            self.client.post(self.url + 'generate/', {'mode': 'all'}, format='json')
        question = self.session.questions.get()
        self.assertEqual(question.ai_answer, rendered('Test provider answer'))
        self.assertNotEqual(question.ai_error, '')

    def test_stopped_run_refunds_questions_it_never_sent(self):
        provider = self.ai_client.models.generate_content
        provider.side_effect = genai_errors.ClientError(401, {'error': {'message': 'API key not valid'}})
        self.import_text('Q1\nQ2\nQ3')
        with self.assertLogs('questions.tasks', 'WARNING'):
            self.client.post(self.url + 'generate/')
        # One question reached Gemini; the other two are given back.
        self.assertEqual(AIUsage.objects.get(user=self.user).questions, 1)
        self.assertEqual(GlobalAIUsage.objects.get().questions, 1)

    @override_settings(GEMINI_API_KEY='')
    def test_missing_configuration_refunds_the_whole_run(self):
        self.import_text()
        with self.assertLogs('questions.tasks', 'WARNING'):
            self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'failed')
        self.assertEqual(AIUsage.objects.get(user=self.user).questions, 0)
        self.assertEqual(GlobalAIUsage.objects.get().questions, 0)

    def test_stuck_processing_session_can_be_restarted_and_deleted(self):
        self.import_text()
        Session.objects.filter(pk=self.session.pk).update(
            status='processing', celery_task_id='dead-run', updated_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')
        Session.objects.filter(pk=self.session.pk).update(
            status='processing', updated_at=timezone.now() - timedelta(hours=1),
        )
        self.assertEqual(self.client.delete(self.url).status_code, 204)

    def test_old_task_cannot_overwrite_new_run(self):
        self.session.status = 'processing'
        self.session.celery_task_id = 'new-run'
        self.session.save()
        generate_session_answers(self.session.pk, 'old-run')
        self.ai_client.models.generate_content.assert_not_called()
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'processing')

    def test_task_stops_when_a_newer_run_takes_over(self):
        self.import_text()
        self.session.status = 'processing'
        self.session.celery_task_id = 'old-run'
        self.session.save()

        def take_over(**kwargs):
            Session.objects.filter(pk=self.session.pk).update(celery_task_id='new-run')
            return ai_response('stale answer')

        self.ai_client.models.generate_content.side_effect = take_over
        generate_session_answers(self.session.pk, 'old-run')
        self.assertEqual(self.ai_client.models.generate_content.call_count, 1)
        self.assertFalse(self.session.questions.exclude(ai_answer=None).exists())

    def test_run_that_waited_in_queue_is_alive_at_start(self):
        self.import_text()
        # Almost stale: the run spent most of GENERATION_STALE_AFTER waiting in the queue.
        Session.objects.filter(pk=self.session.pk).update(
            status='processing', celery_task_id='run',
            updated_at=timezone.now() - settings.GENERATION_STALE_AFTER + timedelta(seconds=1),
        )
        busy_during_first_answer = []

        def slow_first_answer(**kwargs):
            # The first answer outlives the time left before the run would look stale.
            with patch('interviews.models.timezone.now', return_value=timezone.now() + timedelta(minutes=1)):
                busy_during_first_answer.append(Session.objects.get(pk=self.session.pk).is_busy)
            return ai_response('answer')

        self.ai_client.models.generate_content.side_effect = slow_first_answer
        generate_session_answers(self.session.pk, 'run')
        self.assertTrue(busy_during_first_answer[0])

    def test_regeneration_preserves_user_answer(self):
        self.import_text()
        question = self.session.questions.first()
        question.user_answer = 'My own answer'
        question.save()
        self.client.post(self.url + 'generate/')
        self.client.post(self.url + 'generate/', {'mode': 'all'}, format='json')
        question.refresh_from_db()
        self.assertEqual(question.user_answer, 'My own answer')

    def test_generation_answers_only_edited_question_by_default(self):
        self.import_text('Q1\nQ2\nQ3')
        self.client.post(self.url + 'generate/')
        provider = self.ai_client.models.generate_content
        provider.reset_mock()
        provider.return_value = ai_response('New answer')
        second = self.session.questions.get(order=2)
        self.client.patch(f'/api/questions/{second.pk}/', {'text': 'Нове питання?'})
        self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(
            list(self.session.questions.values_list('ai_answer', flat=True)),
            [rendered('Test provider answer'), rendered('New answer'), rendered('Test provider answer')],
        )

    def test_mode_all_regenerates_every_answer(self):
        self.import_text('Q1\nQ2\nQ3')
        self.client.post(self.url + 'generate/')
        provider = self.ai_client.models.generate_content
        provider.reset_mock()
        response = self.client.post(self.url + 'generate/', {'mode': 'all'}, format='json')
        self.assertEqual(response.data['status'], 'ready')
        self.assertEqual(provider.call_count, 3)

    def test_nothing_to_generate_is_rejected(self):
        self.import_text()
        self.client.post(self.url + 'generate/')
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 400)
        cache.clear()  # Third call in a minute would hit the generate throttle.
        self.assertEqual(self.client.post(self.url + 'generate/', {'mode': 'bad'}, format='json').status_code, 400)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'ready')

    def test_editing_question_invalidates_ai_answer(self):
        self.import_text()
        self.client.post(self.url + 'generate/')
        question = self.session.questions.first()
        result = self.client.patch(f'/api/questions/{question.pk}/', {'text': 'Нове питання?'})
        self.assertIsNone(result.data['ai_answer'])
        self.assertIsNone(result.data['ai_parts'])
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

    def test_frequent_generation_is_throttled(self):
        self.import_text()
        statuses = [self.client.post(self.url + 'generate/', {'mode': 'all'}).status_code for _ in range(3)]
        self.assertEqual(statuses, [202, 202, 429])

    @override_settings(AI_DAILY_QUESTION_LIMIT=1)
    def test_daily_quota_rejects_run_before_processing(self):
        self.import_text()
        result = self.client.post(self.url + 'generate/')
        self.assertEqual(result.status_code, 429)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'created')
        self.ai_client.models.generate_content.assert_not_called()
        self.assertFalse(AIUsage.objects.filter(questions__gt=0).exists())

    @override_settings(AI_DAILY_QUESTION_LIMIT=3)
    def test_daily_quota_counts_questions_across_sessions(self):
        self.import_text()
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 202)
        second = Session.objects.create(user=self.user, name='Second')
        self.client.post(f'/api/sessions/{second.pk}/import/', {'text': 'Питання 1\nПитання 2'}, format='json')
        self.assertEqual(self.client.post(f'/api/sessions/{second.pk}/generate/').status_code, 429)
        self.assertEqual(AIUsage.objects.get(user=self.user).questions, 2)

    @override_settings(AI_GLOBAL_DAILY_QUESTION_LIMIT=3)
    def test_global_quota_rejects_every_user(self):
        self.import_text()
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 202)
        session = Session.objects.create(user=self.other, name='Other')
        self.client.force_authenticate(self.other)
        self.client.post(f'/api/sessions/{session.pk}/import/', {'text': 'Q1\nQ2'}, format='json')
        self.assertEqual(self.client.post(f'/api/sessions/{session.pk}/generate/').status_code, 429)
        session.refresh_from_db()
        self.assertEqual(session.status, 'created')
        self.assertEqual(GlobalAIUsage.objects.get().questions, 2)
        self.assertFalse(AIUsage.objects.filter(user=self.other, questions__gt=0).exists())

    def test_unverified_account_cannot_spend_quota(self):
        self.import_text()
        self.user.email_verified = False
        self.user.save(update_fields=['email_verified'])
        self.assertEqual(self.client.post(self.url + 'generate/').status_code, 403)
        self.assertFalse(GlobalAIUsage.objects.exists())
        self.ai_client.models.generate_content.assert_not_called()

    @override_settings(MAX_SESSIONS_PER_USER=2)
    def test_session_count_is_limited(self):
        statuses = [self.client.post('/api/sessions/', {'name': 'S'}, format='json').status_code for _ in range(2)]
        self.assertEqual(statuses, [201, 400])
        self.assertEqual(self.user.sessions.count(), 2)

    @override_settings(MAX_SESSIONS_PER_USER=1000)
    def test_session_creation_and_import_are_throttled(self):
        for _ in range(59):
            self.client.post('/api/sessions/', {'name': 'S'}, format='json')
        self.assertEqual(self.import_text().status_code, 201)
        self.assertEqual(self.import_text().status_code, 429)
        self.assertEqual(self.client.post('/api/sessions/', {'name': 'S'}, format='json').status_code, 429)

    def texts(self):
        return list(self.session.questions.order_by('order').values_list('text', flat=True))

    def orders(self):
        return list(self.session.questions.order_by('order').values_list('order', flat=True))

    def test_add_question_to_the_end(self):
        self.import_text()
        self.client.post(self.url + 'generate/')
        added = self.client.post(self.url + 'questions/', {'text': '  Що таке індекс?  '}, format='json')
        self.assertEqual(added.status_code, 201)
        self.assertEqual((added.data['text'], added.data['order'], added.data['ai_answer']), ('Що таке індекс?', 3, None))
        self.assertEqual(self.texts(), ['Що таке ORM?', 'Що таке транзакція?', 'Що таке індекс?'])
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'created')
        # Default mode answers only the new question.
        provider = self.ai_client.models.generate_content
        provider.reset_mock()
        self.client.post(self.url + 'generate/')
        self.assertEqual(provider.call_count, 1)

    def test_added_question_is_validated_and_limited(self):
        for text in ['', '   ', 'x' * 2001, 'bad\x00question']:
            with self.subTest(text=text[:20]):
                response = self.client.post(self.url + 'questions/', {'text': text}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post(self.url + 'questions/', {}, format='json').status_code, 400)
        self.import_text('\n'.join(f'Q{number}' for number in range(1, 51)))
        response = self.client.post(self.url + 'questions/', {'text': 'Q51'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.session.questions.count(), 50)

    def test_delete_question_closes_the_gap(self):
        self.import_text('Q1\nQ2\nQ3')
        second = self.session.questions.get(order=2)
        self.assertEqual(self.client.delete(f'/api/questions/{second.pk}/').status_code, 204)
        self.assertEqual(self.texts(), ['Q1', 'Q3'])
        self.assertEqual(self.orders(), [1, 2])
        added = self.client.post(self.url + 'questions/', {'text': 'Q4'}, format='json')
        self.assertEqual(added.data['order'], 3)

    def test_reorder_questions(self):
        self.import_text('Q1\nQ2\nQ3')
        q1, q2, q3 = self.session.questions.order_by('order')
        response = self.client.post(self.url + 'reorder/', {'ids': [q3.pk, q1.pk, q2.pk]}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(item['id'], item['order']) for item in response.data], [(q3.pk, 1), (q1.pk, 2), (q2.pk, 3)])
        self.assertEqual(self.texts(), ['Q3', 'Q1', 'Q2'])
        self.assertIn('1. Q3', self.client.get(self.url + 'export/').content.decode())

    def test_reorder_needs_every_question_once(self):
        self.import_text('Q1\nQ2\nQ3')
        q1, q2, q3 = self.session.questions.order_by('order')
        foreign = Question.objects.create(session=Session.objects.create(user=self.other, name='O'), text='X')
        for ids in [[q1.pk, q2.pk], [q1.pk, q2.pk, q2.pk], [q1.pk, q2.pk, q3.pk, foreign.pk],
                    [q1.pk, q2.pk, foreign.pk], [], 'bad']:
            with self.subTest(ids=ids):
                response = self.client.post(self.url + 'reorder/', {'ids': ids}, format='json')
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.texts(), ['Q1', 'Q2', 'Q3'])

    def test_other_users_questions_are_not_found(self):
        session = Session.objects.create(user=self.other, name='Other')
        question = Question.objects.create(session=session, text='Чуже')
        url = f'/api/sessions/{session.pk}/'
        self.assertEqual(self.client.post(url + 'questions/', {'text': 'Q'}, format='json').status_code, 404)
        self.assertEqual(self.client.post(url + 'reorder/', {'ids': [question.pk]}, format='json').status_code, 404)
        self.assertEqual(self.client.delete(f'/api/questions/{question.pk}/').status_code, 404)
        self.assertEqual(session.questions.count(), 1)

    def test_question_changes_are_blocked_during_generation(self):
        self.import_text()
        self.session.status = 'processing'
        self.session.save()
        first = self.session.questions.order_by('order').first()
        self.assertEqual(self.client.post(self.url + 'questions/', {'text': 'Q'}, format='json').status_code, 409)
        self.assertEqual(self.client.delete(f'/api/questions/{first.pk}/').status_code, 409)
        ids = list(self.session.questions.order_by('-order').values_list('id', flat=True))
        self.assertEqual(self.client.post(self.url + 'reorder/', {'ids': ids}, format='json').status_code, 409)
        self.assertEqual(self.texts(), ['Що таке ORM?', 'Що таке транзакція?'])

    @override_settings(DEBUG=True)
    def test_schema_lists_question_endpoints(self):
        paths = self.client.get('/api/schema/?format=openapi').json()['paths']
        self.assertIn('post', paths['/sessions/{id}/questions/'])
        self.assertIn('post', paths['/sessions/{id}/reorder/'])
        self.assertIn('delete', paths['/questions/{id}/'])

    def test_old_ai_usage_is_deleted(self):
        today = timezone.localdate()
        old = today - settings.AI_USAGE_KEEP_FOR - timedelta(days=1)
        for date in (old, today):
            AIUsage.objects.create(user=self.user, date=date, questions=1)
            GlobalAIUsage.objects.create(date=date, questions=1)
        delete_old_ai_usage()
        self.assertEqual(list(AIUsage.objects.values_list('date', flat=True)), [today])
        self.assertEqual(list(GlobalAIUsage.objects.values_list('date', flat=True)), [today])

    @override_settings(DEBUG=True)
    def test_schema_is_open_in_development(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/schema/?format=openapi').status_code, 200)

    def test_schema_is_staff_only_in_production(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get('/api/schema/?format=openapi').status_code, 403)
        self.assertEqual(self.client.get('/api/docs/').status_code, 403)
        self.client.force_authenticate(self.user)
        self.assertEqual(self.client.get('/api/schema/?format=openapi').status_code, 403)
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.assertEqual(self.client.get('/api/schema/?format=openapi').status_code, 200)


    def test_invalid_json_fails_only_its_question(self):
        provider = self.ai_client.models.generate_content
        broken = ai_response('unused')
        broken.text = '{"explanation": "no other fields"}'
        provider.side_effect = [broken, ai_response('A2')]
        self.import_text()
        with self.assertLogs('questions.tasks', 'WARNING') as logs:
            self.assertEqual(self.client.post(self.url + 'generate/').data['status'], 'ready')
        self.assertIn('InvalidAIResponse', '\n'.join(logs.output))
        self.assertNotIn('no other fields', '\n'.join(logs.output))
        first, second = self.session.questions.order_by('order')
        self.assertIsNone(first.ai_parts)
        self.assertNotEqual(first.ai_error, '')
        self.assertEqual(second.ai_parts, parts('A2'))

    def test_user_answer_and_export_use_the_joined_text(self):
        self.import_text('Q1')
        self.client.post(self.url + 'generate/')
        question = self.session.questions.get()
        self.assertEqual(self.client.get(f'/api/questions/{question.pk}/').data['answer'], rendered('Test provider answer'))
        self.assertIn('Відповідь на співбесіді\nTest provider answer', self.client.get(self.url + 'export/').content.decode())
        self.client.patch(f'/api/questions/{question.pk}/', {'user_answer': 'Моя'}, format='json')
        self.assertEqual(self.client.get(f'/api/questions/{question.pk}/').data['answer'], 'Моя')
