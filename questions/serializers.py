from rest_framework import serializers

from .models import Question


class QuestionSerializer(serializers.ModelSerializer):
    text = serializers.CharField(max_length=2000)
    user_answer = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=20000)
    answer = serializers.CharField(source='final_answer', read_only=True, allow_null=True)

    class Meta:
        model = Question
        fields = ('id', 'session', 'text', 'ai_answer', 'user_answer', 'answer', 'order', 'created_at')
        read_only_fields = ('id', 'session', 'ai_answer', 'order', 'created_at')
