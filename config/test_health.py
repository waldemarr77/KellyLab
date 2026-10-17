from unittest.mock import patch

from django.db import OperationalError
from django.test import TestCase, override_settings


class HealthTests(TestCase):
    url = '/api/health/'

    def test_healthy_without_auth(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'database': 'ok'})

    def test_database_down_returns_503(self):
        with patch('config.health.check_database', side_effect=OperationalError('could not connect to db-host')), \
                self.assertLogs('config.health', 'ERROR'):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'database': 'error'})
        self.assertNotIn('db-host', response.content.decode())

    @override_settings(CACHE_URL='redis://cache:6379/1')
    def test_cache_is_checked_when_configured(self):
        self.assertEqual(self.client.get(self.url).json(), {'database': 'ok', 'cache': 'ok'})
        with patch('config.health.cache.set', side_effect=ConnectionError('redis down')), \
                self.assertLogs('config.health', 'ERROR'):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {'database': 'ok', 'cache': 'error'})

    def test_only_get_is_allowed(self):
        self.assertEqual(self.client.post(self.url).status_code, 405)
