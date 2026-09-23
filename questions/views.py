
from django.db import transaction
from rest_framework import mixins, viewsets
from rest_framework.response import Response

from config.exceptions import Conflict
from sessions.models import Session

from .models import Question
from .serializers import QuestionSerializer


class QuestionViewSet(mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet):
    serializer_class = QuestionSerializer
    http_method_names = ['get', 'patch', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Question.objects.none()
        return Question.objects.filter(session__user=self.request.user)

    def update(self, request, *args, **kwargs):
        question = self.get_object()
        with transaction.atomic():
            session = Session.objects.select_for_update().get(pk=question.session_id)
            if session.status == 'processing':
                raise Conflict()
            question.refresh_from_db()
            serializer = self.get_serializer(question, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            if serializer.validated_data.get('text', question.text) != question.text:
                serializer.save(ai_answer=None)
                session.status = 'created'
                session.save(update_fields=['status', 'updated_at'])
            else:
                serializer.save()
        return Response(serializer.data)
