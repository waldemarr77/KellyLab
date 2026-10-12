from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from kombu.exceptions import OperationalError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import Throttled, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from config.exceptions import Conflict
from exports.models import Export, StatusExport
from exports.serializers import ExportCreateSerializer, ExportSerializer
from exports.services import build_export_payload, content_hash, reusable_export
from exports.tasks import generate_export
from questions.models import Question
from questions.serializers import QuestionSerializer
from questions.tasks import generate_session_answers

from .models import AIUsage, GlobalAIUsage, Session, SessionStatus
from .serializers import GenerateSerializer, ImportSerializer, SessionSerializer


def locked_usage(queryset, **lookup):
    usage, _ = queryset.get_or_create(**lookup)
    return queryset.select_for_update().get(pk=usage.pk)


def charge_daily_quota(user, question_count):
    """Count questions sent to Gemini today; reject the run if it exceeds the user or service limit."""
    # Every run locks the service row first, so parallel runs cannot deadlock or both pass the check.
    service = locked_usage(
        GlobalAIUsage.objects, date=timezone.localdate(timezone=settings.GEMINI_QUOTA_TIMEZONE),
    )
    usage = locked_usage(AIUsage.objects, user=user, date=timezone.localdate())
    limit = settings.AI_DAILY_QUESTION_LIMIT
    if usage.questions + question_count > limit:
        raise Throttled(detail=(
            f'Денний ліміт генерації вичерпано: використано {usage.questions} з {limit} питань, '
            f'а запуск потребує {question_count}. Спробуй завтра.'
        ))
    if service.questions + question_count > settings.AI_GLOBAL_DAILY_QUESTION_LIMIT:
        raise Throttled(detail='Сервіс сьогодні вичерпав ліміт генерації. Спробуй завтра.')
    for row in (usage, service):
        row.questions = F('questions') + question_count
        row.save(update_fields=['questions'])


class ExportThrottle(UserRateThrottle):
    # The viewset's throttle_scope belongs to generate, so exports get their own scope.
    scope = 'export'


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

    @swagger_auto_schema(
        method='post', request_body=ExportCreateSerializer, responses={200: ExportSerializer, 202: ExportSerializer},
    )
    @action(detail=True, methods=['post'], url_path='exports', throttle_classes=[ExportThrottle])
    def create_export(self, request, pk=None):
        session = self.get_object()
        serializer = ExportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        export_format = serializer.validated_data['format']
        task_id = uuid4().hex
        with transaction.atomic():
            # A parallel request waits on this lock and then finds the export created here.
            session = self.get_queryset().select_for_update().get(pk=session.pk)
            if session.is_busy:
                raise Conflict()
            if not session.questions.exists():
                raise ValidationError('Спочатку імпортуй питання.')
            payload_hash = content_hash(build_export_payload(session, export_format))
            # The same content was already exported or is being exported: reuse it instead of a new file.
            existing = reusable_export(session, export_format, payload_hash)
            if existing is not None:
                return Response(ExportSerializer(existing, context={'request': request}).data)
            export = Export.objects.create(
                session=session, format=export_format, content_hash=payload_hash, celery_task_id=task_id,
            )
        # Commit before publishing: even a fast worker must see the new export.
        try:
            generate_export.apply_async(args=[export.pk], task_id=task_id)
        except (OperationalError, ConnectionError):
            export.status = StatusExport.FAILED
            export.error_message = 'Черга недоступна. Перевір Redis та Celery.'
            export.save(update_fields=['status', 'error_message', 'updated_at'])
            return Response({'detail': 'Не вдалося передати задачу в чергу.'}, status=503)
        export.refresh_from_db()
        return Response(ExportSerializer(export, context={'request': request}).data, status=status.HTTP_202_ACCEPTED)

    @swagger_auto_schema(method='get', responses={200: ExportSerializer})
    @action(detail=True, methods=['get'], url_path=r'exports/(?P<export_id>\d+)', url_name='export-detail')
    def export_detail(self, request, pk=None, export_id=None):
        export = get_object_or_404(self.get_object().exports, pk=export_id)
        return Response(ExportSerializer(export, context={'request': request}).data)

    @swagger_auto_schema(method='get', responses={200: openapi.Response('PDF or DOCX download')})
    @action(
        detail=True, methods=['get'], url_path=r'exports/(?P<export_id>\d+)/download', url_name='export-download',
    )
    def export_download(self, request, pk=None, export_id=None):
        # get_object() only finds the user's own sessions, so other users get 404.
        export = get_object_or_404(self.get_object().exports, pk=export_id, status=StatusExport.READY)
        # The file can be gone (e.g. lost volume); a new POST then regenerates it.
        if not export.file.storage.exists(export.file.name):
            raise Http404
        return FileResponse(
            export.file.open('rb'), as_attachment=True, filename=f'kellylab-{export.session_id}.{export.format}',
        )
