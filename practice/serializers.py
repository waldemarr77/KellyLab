from django.conf import settings
from rest_framework import serializers

from .models import CodingAttempt, CodingTask


class CodingTaskListSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodingTask
        fields = ('id', 'title', 'difficulty', 'language')


class CodingTaskSerializer(serializers.ModelSerializer):
    # Only the public tests: the hidden ones are what makes a check meaningful.
    examples = serializers.SerializerMethodField()
    tests_count = serializers.SerializerMethodField()

    class Meta:
        model = CodingTask
        fields = ('id', 'title', 'description', 'difficulty', 'language', 'skeleton', 'examples', 'tests_count')

    def get_examples(self, task):
        return [
            {'input': case.get('input', ''), 'expected_output': case['expected_output']}
            for case in task.public_test_cases
        ]

    def get_tests_count(self, task):
        return len(task.test_cases)


class CodingAttemptCreateSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=settings.MAX_SOLUTION_LENGTH, trim_whitespace=False)


class CodingAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = CodingAttempt
        fields = (
            'id', 'task', 'user_solution', 'status', 'feedback',
            'passed_tests', 'total_tests', 'results', 'created_at', 'updated_at',
        )
        read_only_fields = fields
