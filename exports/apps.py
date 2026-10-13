from django.apps import AppConfig


class ExportsConfig(AppConfig):
    name = 'exports'

    def ready(self):
        from . import signals  # noqa: F401 -- registers the receivers
