from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from kombu.exceptions import OperationalError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle, UserRateThrottle

from config.exceptions import Conflict
from exports.models import Export, StatusExport
from exports.serializers import ExportCreateSerializer, ExportSerializer
from exports.services import build_export_payload, content_hash, reusable_export
from exports.tasks import generate_export
from questions.models import MAX_QUESTIONS_PER_SESSION, Question, renumber
from questions.serializers import QuestionSerializer
from questions.tasks import generate_session_answers
from users.models import CustomUser

from .models import Session, SessionStatus
from .quota import charge_daily_quota, refund_daily_quota
from .serializers import (
    GenerateSerializer,
    ImportSerializer,
    ReorderSerializer,
    SessionSerializer,
)


class ExportThrottle(UserRateThrottle):
    # The viewset's throttle_scope belongs to generate, so exports get their own scope.
    scope = 'export'


class SessionWriteThrottle(UserRateThrottle):
    scope = 'session_write'


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']
    throttle_scope = 'generate'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Session.objects.none()
        return Session.objects.filter(user=self.request.user)

    def get_throttles(self):
        if self.action == 'create':
            return [SessionWriteThrottle()]
        return super().get_throttles()

    def perform_create(self, serializer):
        with transaction.atomic():
            # Parallel requests wait on the user row, so they cannot all pass the count check.
            CustomUser.objects.select_for_update().get(pk=self.request.user.pk)
            if self.request.user.sessions.count() >= settings.MAX_SESSIONS_PER_USER:
                raise ValidationError(
                    f'Можна мати до {settings.MAX_SESSIONS_PER_USER} сесій. Видали непотрібні, щоб створити нову.'
                )
            serializer.save(user=self.request.user)

    def lock_idle_session(self, session):
        """Lock the session row for this transaction; 409 while a generation run is active."""
        session = self.get_queryset().select_for_update().get(pk=session.pk)
        if session.is_busy:
            raise Conflict()
        return session

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

    @action(
        detail=True, methods=['post'], url_path='import', serializer_class=ImportSerializer,
        throttle_classes=[SessionWriteThrottle],
    )
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
    @swagger_auto_schema(method='post', request_body=QuestionSerializer, responses={201: QuestionSerializer})
    @action(detail=True, methods=['get', 'post'])
    def questions(self, request, pk=None):
        session = self.get_object()
        if request.method == 'POST':
            return self.add_question(request, session)
        page = self.paginate_queryset(session.questions.order_by('order', 'id'))
        return self.get_paginated_response(QuestionSerializer(page, many=True).data)

    def add_question(self, request, session):
        """Append one question to the end of the session."""
        serializer = QuestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            session = self.lock_idle_session(session)
            orders = list(session.questions.values_list('order', flat=True))
            if len(orders) >= MAX_QUESTIONS_PER_SESSION:
                raise ValidationError(f'У сесії може бути до {MAX_QUESTIONS_PER_SESSION} питань.')
            serializer.save(session=session, order=max(orders, default=0) + 1)
            # The new question has no answer yet, so the session needs a generation run again.
            session.status = SessionStatus.CREATED
            session.error_message = ''
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(method='post', request_body=ReorderSerializer, responses={200: QuestionSerializer(many=True)})
    @action(detail=True, methods=['post'])
    def reorder(self, request, pk=None):
        session = self.get_object()
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ids = serializer.validated_data['ids']
        with transaction.atomic():
            session = self.lock_idle_session(session)
            questions = session.questions.in_bulk()
            if len(ids) != len(set(ids)) or set(ids) != set(questions):
                raise ValidationError({'ids': 'Передай id усіх питань сесії, кожне один раз.'})
            ordered = [questions[question_id] for question_id in ids]
            renumber(ordered)
        return Response(QuestionSerializer(ordered, many=True).data)

    @swagger_auto_schema(method='post', request_body=GenerateSerializer, responses={202: SessionSerializer})
    @action(detail=True, methods=['post'], throttle_classes=[ScopedRateThrottle])
    def generate(self, request, pk=None):
        session = self.get_object()
        # Registration is open, so only confirmed accounts may spend the shared Gemini quota.
        if not request.user.email_verified:
            raise PermissionDenied('Підтверди пошту, щоб генерувати відповіді.')
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
            charge = charge_daily_quota(request.user, questions.count())
            session.status = SessionStatus.PROCESSING
            session.celery_task_id = run_id
            session.error_message = ''
            session.save(update_fields=['status', 'celery_task_id', 'error_message', 'updated_at'])
        # Commit before publishing: even a fast worker must see the new state.
        try:
            generate_session_answers.apply_async(
                args=[session.pk, run_id, mode], kwargs={'charge': charge}, task_id=run_id,
            )
        except (OperationalError, ConnectionError):
            # Nothing reached Gemini, so the run must not cost any quota.
            refund_daily_quota(charge, charge['questions'])
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
