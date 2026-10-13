from django.db import transaction
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response

from config.exceptions import Conflict
from interviews.models import Session, SessionStatus

from .models import Question, renumber
from .serializers import QuestionSerializer


class QuestionViewSet(
    mixins.RetrieveModelMixin, mixins.UpdateModelMixin, mixins.DestroyModelMixin, viewsets.GenericViewSet,
):
    serializer_class = QuestionSerializer
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

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
