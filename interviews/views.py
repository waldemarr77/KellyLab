
from uuid import uuid4

from django.db import transaction
from django.http import HttpResponse
from drf_yasg import openapi
from drf_yasg.utils import no_body, swagger_auto_schema
from kombu.exceptions import OperationalError
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from config.exceptions import Conflict
from questions.models import Question
from questions.serializers import QuestionSerializer
from questions.tasks import generate_session_answers

from .models import Session
from .serializers import ImportSerializer, SessionSerializer


class SessionViewSet(viewsets.ModelViewSet):
    serializer_class = SessionSerializer
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Session.objects.none()
        return Session.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def update(self, request, *args, **kwargs):
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            serializer = self.get_serializer(session, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=self.get_object().pk)
            if session.status == 'processing':
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
            if session.status == 'processing':
                raise Conflict()
            if session.questions.exists():
                raise ValidationError('Імпорт доступний лише для порожньої сесії. Створи нову.')
            questions = Question.objects.bulk_create([
                Question(session=session, text=text, order=index)
                for index, text in enumerate(serializer.validated_data['questions'], start=1)
            ])
            session.status = 'created'
            session.error_message = ''
            session.save(update_fields=['status', 'error_message', 'updated_at'])
        return Response(QuestionSerializer(questions, many=True).data, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(method='get', responses={200: QuestionSerializer(many=True)})
    @action(detail=True, methods=['get'])
    def questions(self, request, pk=None):
        queryset = self.get_object().questions.order_by('order', 'id')
        page = self.paginate_queryset(queryset)
        return self.get_paginated_response(QuestionSerializer(page, many=True).data)

    @swagger_auto_schema(method='post', request_body=no_body, responses={202: SessionSerializer})
    @action(detail=True, methods=['post'])
    def generate(self, request, pk=None):
        session = self.get_object()
        run_id = uuid4().hex
        with transaction.atomic():
            session = self.get_queryset().select_for_update().get(pk=session.pk)
            if session.status == 'processing':
                raise Conflict()
            if not session.questions.exists():
                raise ValidationError('Спочатку імпортуй питання.')
            session.status = 'processing'
            session.celery_task_id = run_id
            session.error_message = ''
            session.save(update_fields=['status', 'celery_task_id', 'error_message', 'updated_at'])
        # Commit before publishing: even a fast worker must see the new state.
        try:
            generate_session_answers.apply_async(args=[session.pk, run_id], task_id=run_id)
        except (OperationalError, ConnectionError):
            self.get_queryset().filter(pk=session.pk, celery_task_id=run_id, status='processing').update(
                status='failed', error_message='Черга недоступна. Перевір Redis та Celery.',
            )
            return Response({'detail': 'Не вдалося передати задачу в чергу.'}, status=503)
        session.refresh_from_db()
        return Response(self.get_serializer(session).data, status=status.HTTP_202_ACCEPTED)

    @swagger_auto_schema(method='get', responses={200: openapi.Response('UTF-8 TXT download')})
    @action(detail=True, methods=['get'], url_path='export')
    def export_txt(self, request, pk=None):
        session = self.get_object()
        if session.status == 'processing':
            raise Conflict()
        lines = [session.name, '']
        for question in session.questions.order_by('order', 'id'):
            answer = question.user_answer if question.user_answer is not None else question.ai_answer
            lines.extend([f'{question.order}. {question.text}', answer or '', ''])
        response = HttpResponse('\n'.join(lines), content_type='text/plain; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="kellylab-{session.pk}.txt"'
        return response
