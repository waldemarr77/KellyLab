from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from kombu.exceptions import OperationalError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from config.exceptions import Conflict
from questions.models import Question
from questions.serializers import QuestionSerializer
from questions.tasks import generate_session_answers

from .models import AIUsage, Session, SessionStatus
from .serializers import GenerateSerializer, ImportSerializer, SessionSerializer


def charge_daily_quota(user, question_count):
    """Count questions sent to Gemini today; reject the run if it exceeds the daily limit."""
    usage, _ = AIUsage.objects.get_or_create(user=user, date=timezone.localdate())
    # The row lock stops parallel runs of different sessions from both passing the check.
    usage = AIUsage.objects.select_for_update().get(pk=usage.pk)
    limit = settings.AI_DAILY_QUESTION_LIMIT
    if usage.questions + question_count > limit:
        raise Throttled(detail=(
            f'Денний ліміт генерації вичерпано: використано {usage.questions} з {limit} питань, '
            f'а запуск потребує {question_count}. Спробуй завтра.'
        ))
    usage.questions = F('questions') + question_count
    usage.save(update_fields=['questions'])


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    throttle_scope = 'generate'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Session.objects.none()
        return Session.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_update(self, serializer):
        session = serializer.instance
        session.name = serializer.validated_data.get('name', session.name)
        session.save(update_fields=['name'])
        session.refresh_from_db()

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if session.is_busy:
                raise Conflict()
            session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=['post'], url_path='import', serializer_class=ImportSerializer)
    def import_questions(self, request, pk=None):
        session = self.get_object()
        serializer = ImportSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=session.pk)
            if session.is_busy:
                raise Conflict()
            if session.questions.exists():
                raise ValidationError('Імпорт доступний лише для порожньої сесії. Створи нову.')
            questions = Question.objects.bulk_create([
                Question(session=session, text=text, order=index)
                for index, text in enumerate(serializer.validated_data['questions'], start=1)
            ])
            session.status = SessionStatus.CREATED
            session.error_message = ''
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        return Response(QuestionSerializer(questions, many=True).data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(method='get', responses={200: QuestionSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def questions(self, request, pk=None):
        queryset = self.get_object().questions.order_by('order', 'id')
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(QuestionSerializer(page, many=True).data)

    @swagger_auto_schema(method='post', request_body=GenerateSerializer, responses={202: SessionSerializer})
    @action(detail=True, methods=['post'], throttle_classes=[ScopedRateThrottle])
    def generate(self, request, pk=None):
        session = self.get_object()
        serializer = GenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        mode = serializer.validated_data['mode']
        run_id = uuid4().hex
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=session.pk)
            if session.is_busy:
                raise Conflict()
            questions = session.questions.all()
            if not questions.exists():
                raise ValidationError('Спочатку імпортуй питання.')
            if mode == 'missing':
                questions = questions.filter(ai_answer__isnull=True)
                if not questions.exists():
                    raise ValidationError('Усі відповіді вже згенеровані. Для повторної генерації передай mode=all.')
            # Raises before the status change, so a rejected run leaves the session untouched.
            charge_daily_quota(request.user, questions.count())
            session.status = SessionStatus.PROCESSING
            session.celery_task_id = run_id
            session.error_message = ''
            session.save(update_fields=['status', 'celery_task_id', 'error_message', 'updated_at'])
        # Commit before publishing: even a fast worker must see the new state.
        try:
            generate_session_answers.apply_async(args=[session.pk, run_id, mode], task_id=run_id)
        except (OperationalError, ConnectionError):
            self.get_queryset().filter(
                pk=session.pk, celery_task_id=run_id, status=SessionStatus.PROCESSING,
            ).update(
                status=SessionStatus.FAILED, error_message='Черга недоступна. Перевір Redis та Celery.',
            )
            return Response({'detail': 'Не вдалося передати задачу в чергу.'}, status=503)
        session.refresh_from_db()
        return Response(self.get_serializer(session).data, status=status.HTTP_202_ACCEPTED)

    @swagger_auto_schema(method='get', responses={200: openapi.Response('UTF-8 TXT download')})
    @action(detail=True, methods=['get'], url_path='export')
    def export_txt(self, request, pk=None):
        session = self.get_object()
        if session.is_busy:
            raise Conflict()
        lines = [session.name, '']
        for question in session.questions.order_by('order', 'id'):
            lines.extend([f'{question.order}. {question.text}', question.final_answer or '', ''])
        response = HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="kellylab-{session.pk}.txt"'
        return response
