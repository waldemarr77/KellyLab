from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .ai import generate_answer


@override_settings(GEMINI_API_KEY='test-key', GEMINI_MODEL='test-model')
class GeminiProviderTests(SimpleTestCase):
    def test_missing_configuration_raises_without_calling_provider(self):
        for key, model in [('', 'test-model'), ('test-key', ''), ('', '')]:
            with (
                self.subTest(key=key, model=model),
                override_settings(GEMINI_API_KEY=key, GEMINI_MODEL=model),
                patch('google.genai.Client') as client,
            ):
                with self.assertRaisesMessage(ValueError, 'GEMINI_API_KEY and GEMINI_MODEL'):
                    generate_answer('Question', 'Developer', 'junior')
                client.assert_not_called()

    @patch('google.genai.Client')
    def test_returns_provider_answer_and_passes_context(self, client_class):
        client = client_class.return_value.__enter__.return_value
        client.models.generate_content.return_value.text = '  Provider answer\n'
        self.assertEqual(generate_answer('What is ORM?', 'Python Developer', 'junior'), 'Provider answer')
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(request['model'], 'test-model')
        for value in ['What is ORM?', 'Python Developer', 'junior']:
            self.assertIn(value, request['contents'])

    @patch('google.genai.Client')
    def test_empty_provider_response_is_an_error(self, client_class):
        client = client_class.return_value.__enter__.return_value
        for text in [None, '', '  ']:
            with self.subTest(text=text):
                client.models.generate_content.return_value.text = text
                with self.assertRaisesMessage(ValueError, 'empty response'):
                    generate_answer('Question', 'Developer', 'junior')

    @patch('google.genai.Client')
    def test_provider_failure_is_not_replaced_with_an_answer(self, client_class):
        client = client_class.return_value.__enter__.return_value
        client.models.generate_content.side_effect = RuntimeError('Provider unavailable')
        with self.assertRaisesMessage(RuntimeError, 'Provider unavailable'):
            generate_answer('Question', 'Developer', 'junior')
