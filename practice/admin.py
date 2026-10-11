from django.contrib import admin

from .models import CodingAttempt, CodingTask, QuestionPractice


@admin.register(QuestionPractice)
class QuestionPracticeAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'title',
        'question',
        'difficulty',
        'status',
        'created_at',
        'updated_at',
    ]
    list_filter = ['difficulty', 'status']
    search_fields = [
        'title',
        'question__text',
        'question__session__user__email',
    ]
    # Question.__str__ shows the session, and Session.__str__ shows its user.
    list_select_related = ['question__session__user']
    raw_id_fields = ['question']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Основне', {
            'fields': ('question', 'title', 'description', 'difficulty'),
        }),
        ('Рішення', {
            'fields': ('status', 'user_solution', 'feedback'),
        }),
        ('Дати', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(CodingTask)
class CodingTaskAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'title',
        'difficulty',
        'language',
        'created_at',
        'updated_at',
    ]
    list_filter = ['difficulty', 'language']
    search_fields = ['title', 'description']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Основне', {
            'fields': ('title', 'description', 'difficulty', 'language'),
        }),
        ('Код і перевірка', {
            'fields': ('skeleton', 'test_cases'),
        }),
        ('Дати', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(CodingAttempt)
class CodingAttemptAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'task',
        'user',
        'status',
        'created_at',
        'updated_at',
    ]
    list_filter = ['status', 'task__difficulty']
    search_fields = [
        'task__title',
        'user__email',
    ]
    list_select_related = ['task', 'user']
    raw_id_fields = ['task', 'user']
    readonly_fields = ['created_at', 'updated_at']
    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Основне', {
            'fields': ('task', 'user', 'status'),
        }),
        ('Рішення', {
            'fields': ('user_solution', 'feedback'),
        }),
        ('Дати', {
            'fields': ('created_at', 'updated_at'),
        }),
    )
