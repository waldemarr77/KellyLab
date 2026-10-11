from django.contrib import admin

from .models import Question


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = [
        'id',
        'short_text',
        'session',
        'order',
        'has_ai_answer',
        'has_user_answer',
        'created_at',
    ]
    list_filter = ['session__status']
    search_fields = [
        'text',
        'session__name',
        'session__user__email',
    ]
    # Question.__str__ shows the session, and Session.__str__ shows its user.
    list_select_related = ['session__user']
    raw_id_fields = ['session']
    readonly_fields = ['created_at']
    ordering = ['-created_at', '-pk']
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Основне', {
            'fields': ('session', 'order', 'text'),
        }),
        ('Відповіді', {
            'fields': ('ai_answer', 'user_answer'),
        }),
        ('Дати', {
            'fields': ('created_at',),
        }),
    )

    @admin.display(description='Питання')
    def short_text(self, obj):
        return obj.text[:80]

    @admin.display(boolean=True, description='Є відповідь AI')
    def has_ai_answer(self, obj):
        return obj.ai_answer is not None

    @admin.display(boolean=True, description='Є власна відповідь')
    def has_user_answer(self, obj):
        return obj.user_answer is not None


class QuestionInline(admin.TabularInline):
    """Read-only list of a session's questions; edit them on their own page."""

    model = Question
    fields = ['order', 'text']
    readonly_fields = ['order', 'text']
    ordering = ['order']
    extra = 0
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False
