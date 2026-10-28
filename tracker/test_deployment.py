import importlib.util
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase


class DeploymentSettingsTests(SimpleTestCase):
    def load(self, changes):
        with tempfile.TemporaryDirectory() as temp:
            environment = {'DATA_DIR': temp, **changes}
            with patch.dict(os.environ, environment, clear=True):
                path = Path(__file__).resolve().parent.parent / 'config/settings.py'
                spec = importlib.util.spec_from_file_location('isolated_settings', path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return module

    def production(self, **extra):
        return {'DJANGO_ENV': 'production', 'DJANGO_SECRET_KEY': 'fictional-test-only-' * 5,
                'DJANGO_ALLOWED_HOSTS': 'orders.example.com', 'POSTGRES_DB': 'fictional',
                'POSTGRES_USER': 'fictional', 'POSTGRES_PASSWORD': 'fictional', 'REDIS_URL': 'redis://redis:6379/0', **extra}

    def test_production_never_silently_falls_back_to_sqlite(self):
        with self.assertRaises(ImproperlyConfigured):
            self.load({'DJANGO_ENV': 'production'})

    def test_debug_wildcard_and_short_secret_are_rejected(self):
        for changes in [{'DJANGO_DEBUG': '1'}, {'DJANGO_ALLOWED_HOSTS': '*'}, {'DJANGO_SECRET_KEY': 'short'}]:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(ImproperlyConfigured):
                    self.load(self.production(**changes))

    def test_production_security_and_private_database_configuration(self):
        module = self.load(self.production(TRUST_PROXY='1', CSRF_TRUSTED_ORIGINS='https://orders.example.com'))
        self.assertTrue(module.SECURE_SSL_REDIRECT)
        self.assertTrue(module.SESSION_COOKIE_SECURE)
        self.assertTrue(module.CSRF_COOKIE_SECURE)
        self.assertEqual(module.DATABASES['default']['ENGINE'], 'django.db.backends.postgresql')
        self.assertEqual(module.SECURE_PROXY_SSL_HEADER, ('HTTP_X_FORWARDED_PROTO', 'https'))
        self.assertIn('redis.RedisCache', module.CACHES['default']['BACKEND'])

    def test_local_mode_still_supports_sqlite_and_http(self):
        module = self.load({})
        self.assertEqual(module.DATABASES['default']['ENGINE'], 'django.db.backends.sqlite3')
        self.assertFalse(module.PRODUCTION)
        self.assertFalse(hasattr(module, 'SECURE_SSL_REDIRECT'))
