from django.db import transaction
from drf_yasg.utils import swagger_auto_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from config.exceptions import Conflict
from interviews.models import Session, SessionStatus
from practice.serializers import PracticeCreateSerializer, QuestionPracticeSerializer
from practice.services import QueueUnavailable, start_generation
from practice.views import PracticeThrottle

from .models import Question, renumber
from .serializers import QuestionSerializer


class QuestionViewSet(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    serializer_class = QuestionSerializer
    # POST only for the practices action; questions themselves are created through the session.
    http_method_names = ['get', 'post', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Question.objects.none()
        return Question.objects.filter(session__user=self.request.user)

    def update(self, request, *args, **kwargs):
        question = self.get_object()
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=question.session_id)
            if session.is_busy:
                raise Conflict()
            question.refresh_from_db()
            serializer = self.get_serializer(question, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            if serializer.validated_data.get('text', question.text) != question.text:
                serializer.save(ai_answer=None, ai_parts=None, ai_error='')
                session.status = SessionStatus.CREATED
                session.error_message = ''
                session.save(update_fields=['status', 'error_message', 'updated_at'])
            else:
                serializer.save()
        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        question = self.get_object()
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=question.session_id)
            if session.is_busy:
                raise Conflict()
            Question.objects.filter(pk=question.pk).delete()
            # Close the gap so the numbering in the cards and exports stays 1..n.
            renumber(session.questions.order_by('order', 'id'))
        return Response(status=status.HTTP_204_NO_CONTENT)

    @swagger_auto_schema(
        method='post', request_body=PracticeCreateSerializer, responses={202: QuestionPracticeSerializer},
    )
    @action(detail=True, methods=['post'], throttle_classes=[PracticeThrottle])
    def practices(self, request, pk=None):
        """Generate a practice task for the question; the result appears in /api/practices/."""
        question = self.get_object()
        serializer = PracticeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            practice = start_generation(request.user, question, serializer.validated_data.get('difficulty'))
        except QueueUnavailable:
            return Response({'detail': 'Не вдалося передати задачу в чергу.'}, status=503)
        return Response(QuestionPracticeSerializer(practice).data, status=status.HTTP_202_ACCEPTED)
