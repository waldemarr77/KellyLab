import json
from unittest.mock import Mock, patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings
from google.genai import types

from .ai import (
    AnswerParts,
    EmptyAIResponse,
    IncompleteAIResponse,
    InvalidAIResponse,
    answer_generator,
    render_answer,
)

PARTS = {
    'explanation': 'ORM maps tables to classes.',
    'example': 'User.objects.filter(active=True)',
    'interview_answer': 'ORM lets you work with the database through objects.',
    'common_mistake': 'N+1 queries.',
}
REPLY = json.dumps(PARTS)


def provider(client_class, finish_reason=types.FinishReason.STOP):
    client = client_class.return_value.__enter__.return_value
    client.models.generate_content.return_value.candidates = [Mock(finish_reason=finish_reason)]
    return client


def generate_one(question, position='Developer', level='junior'):
    with answer_generator(position, level) as generate:
        return generate(question)


@override_settings(GEMINI_API_KEY='test-key', GEMINI_MODEL='test-model')
class GeminiProviderTests(SimpleTestCase):
    def test_missing_configuration_raises_without_calling_provider(self):
        for key, model in [('', 'test-model'), ('test-key', ''), ('', '')]:
            with (
                self.subTest(key=key, model=model),
                override_settings(GEMINI_API_KEY=key, GEMINI_MODEL=model),
                patch('google.genai.Client') as client,
            ):
                with self.assertRaisesMessage(ImproperlyConfigured, 'GEMINI_API_KEY and GEMINI_MODEL'):
                    generate_one('Question')
                client.assert_not_called()

    @patch('google.genai.Client')
    def test_returns_provider_answer_and_passes_context(self, client_class):
        client = provider(client_class)
        client.models.generate_content.return_value.text = json.dumps({**PARTS, 'example': '  code\n'})
        self.assertEqual(generate_one('What is ORM?', 'Python Developer', 'junior'), {**PARTS, 'example': 'code'})
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request['model'], 'test-model')
        self.assertEqual(request['config'].response_mime_type, 'application/json')
        self.assertIs(request['config'].response_schema, AnswerParts)
        for value in ['What is ORM?', 'Python Developer', 'junior']:
            self.assertIn(value, request['contents'])

    @patch('google.genai.Client')
    def test_empty_profile_is_described_instead_of_none(self, client_class):
        client = provider(client_class)
        client.models.generate_content.return_value.text = REPLY
        generate_one('Question', '', '')
        contents = client.models.generate_content.call_args.kwargs['contents']
        self.assertNotIn('None', contents)
        self.assertIn('не вказана', contents)

    @patch('google.genai.Client')
    def test_one_client_serves_many_questions(self, client_class):
        client = provider(client_class)
        client.models.generate_content.return_value.text = REPLY
        with answer_generator('Developer', 'junior') as generate:
            for question in ['First', 'Second', 'Third']:
                generate(question)
        client_class.assert_called_once()
        self.assertEqual(client.models.generate_content.call_count, 3)

    @patch('google.genai.Client')
    def test_empty_provider_response_is_an_error(self, client_class):
        client = provider(client_class)
        for text in [None, '', '  ']:
            with self.subTest(text=text):
                client.models.generate_content.return_value.text = text
                with self.assertRaisesMessage(EmptyAIResponse, 'empty response'):
                    generate_one('Question')

    @patch('google.genai.Client')
    def test_provider_failure_is_not_replaced_with_an_answer(self, client_class):
        client = provider(client_class)
        client.models.generate_content.side_effect = RuntimeError('Provider unavailable')
        with self.assertRaisesMessage(RuntimeError, 'Provider unavailable'):
            generate_one('Question')

    @patch('google.genai.Client')
    def test_answer_not_finished_normally_is_an_error(self, client_class):
        for reason in [types.FinishReason.MAX_TOKENS, types.FinishReason.SAFETY]:
            with self.subTest(reason=reason):
                client = provider(client_class, reason)
                client.models.generate_content.return_value.text = 'Partial answer'
                with self.assertRaises(IncompleteAIResponse) as raised:
                    generate_one('Question')
                self.assertEqual(raised.exception.reason, reason.name)

    @patch('google.genai.Client')
    def test_blocked_prompt_without_candidates_is_empty(self, client_class):
        client = provider(client_class)
        client.models.generate_content.return_value.candidates = []
        client.models.generate_content.return_value.text = None
        with self.assertRaises(EmptyAIResponse):
            generate_one('Question')

    @override_settings(AI_MAX_OUTPUT_TOKENS=4096, AI_THINKING_BUDGET=0)
    @patch('google.genai.Client')
    def test_output_limit_and_thinking_budget_come_from_settings(self, client_class):
        provider(client_class).models.generate_content.return_value.text = REPLY
        generate_one('Question')
        config = client_class.return_value.__enter__.return_value.models.generate_content.call_args.kwargs['config']
        self.assertEqual(config.max_output_tokens, 4096)
        self.assertEqual(config.thinking_config.thinking_budget, 0)

    @patch('google.genai.Client')
    def test_reply_not_matching_the_schema_is_an_error(self, client_class):
        client = provider(client_class)
        for text in ['plain text', '{"explanation": "x"}', json.dumps({**PARTS, 'example': None}), '[1]']:
            with self.subTest(text=text):
                client.models.generate_content.return_value.text = text
                with self.assertRaises(InvalidAIResponse):
                    generate_one('Question')

    @patch('google.genai.Client')
    def test_blank_required_part_is_empty(self, client_class):
        client = provider(client_class)
        for key in ['explanation', 'interview_answer']:
            with self.subTest(key=key):
                client.models.generate_content.return_value.text = json.dumps({**PARTS, key: '  '})
                with self.assertRaises(EmptyAIResponse):
                    generate_one('Question')
        # Example and common mistake may be left out for some questions.
        client.models.generate_content.return_value.text = json.dumps({**PARTS, 'example': '', 'common_mistake': ''})
        self.assertEqual(generate_one('Question')['example'], '')


class RenderAnswerTests(SimpleTestCase):
    def test_sections_in_order_without_empty_ones(self):
        text = render_answer({**PARTS, 'example': ''})
        self.assertEqual(text, (
            'Пояснення\nORM maps tables to classes.\n\n'
            'Відповідь на співбесіді\nORM lets you work with the database through objects.\n\n'
            'Типова помилка\nN+1 queries.'
        ))
