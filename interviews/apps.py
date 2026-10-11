from django.apps import AppConfig


class InterviewsConfig(AppConfig):
    name = 'interviews'
    # Keep the original label: tables and migrations are already named after it.
    label = 'interview_sessions'
