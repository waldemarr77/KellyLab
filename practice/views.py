from django.conf import settings
from django.db import transaction
from django.utils import timezone
from drf_yasg.utils import swagger_auto_schema
from kombu.exceptions import OperationalError
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle

from config.exceptions import Conflict
from users.models import CustomUser

from .models import SUPPORTED_LANGUAGES, CodingAttempt, CodingTask, PracticeStatus
from .serializers import (
    CodingAttemptCreateSerializer,
    CodingAttemptSerializer,
    CodingTaskListSerializer,
    CodingTaskSerializer,
)
from .tasks import check_attempt


class CodeRunThrottle(UserRateThrottle):
    scope = 'code_run'


class CodingTaskViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = CodingTask.objects.all()

    def get_serializer_class(self):
        return CodingTaskListSerializer if self.action == 'list' else CodingTaskSerializer

    def get_queryset(self):
        queryset = super().get_queryset()
        difficulty = self.request.query_params.get('difficulty')
        if difficulty:
            queryset = queryset.filter(difficulty=difficulty)
        return queryset

    @swagger_auto_schema(
        method='post', request_body=CodingAttemptCreateSerializer, responses={202: CodingAttemptSerializer},
    )
    @action(detail=True, methods=['post'], throttle_classes=[CodeRunThrottle])
    def attempts(self, request, pk=None):
        task = self.get_object()
        serializer = CodingAttemptCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if task.language.lower() not in SUPPORTED_LANGUAGES or not task.test_cases:
            raise ValidationError('Цю задачу поки не можна перевірити.')
        with transaction.atomic():
            # Parallel requests wait on the user row, so only one attempt per user is checked at a time.
            CustomUser.objects.select_for_update().get(pk=request.user.pk)
            stale_before = timezone.now() - settings.CODE_CHECK_STALE_AFTER
            checking = request.user.coding_attempts.filter(status=PracticeStatus.CHECKING, updated_at__gt=stale_before)
            if checking.exists():
                raise Conflict('Дочекайся перевірки попередньої спроби.')
            attempt = CodingAttempt.objects.create(
                user=request.user, task=task, user_solution=serializer.validated_data['code'],
                status=PracticeStatus.CHECKING,
            )
        # Commit before publishing: even a fast worker must see the new attempt.
        try:
            check_attempt.delay(attempt.pk)
        except (OperationalError, ConnectionError):
            attempt.status = PracticeStatus.ERROR
            attempt.feedback = 'Черга недоступна. Перевір Redis та Celery.'
            attempt.save(update_fields=['status', 'feedback', 'updated_at'])
            return Response({'detail': 'Не вдалося передати задачу в чергу.'}, status=503)
        attempt.refresh_from_db()
        return Response(CodingAttemptSerializer(attempt).data, status=status.HTTP_202_ACCEPTED)


class CodingAttemptViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = CodingAttemptSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return CodingAttempt.objects.none()
        queryset = CodingAttempt.objects.filter(user=self.request.user)
        task = self.request.query_params.get('task')
        if task and task.isdigit():
            queryset = queryset.filter(task_id=task)
        return queryset
