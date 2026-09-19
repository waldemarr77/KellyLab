from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = [
        'username', 
        'email', 
        'target_position', 
        'experience_level',
        'is_active',
        'is_staff',
        'is_superuser',
        'last_login',
    ]

    list_filter = [
        'experience_level',
        'is_active',
        'is_staff',
        'is_superuser',
    ]

    search_fields = [
        'username', 
        'email', 
        'target_position',
    ]
    
    readonly_fields = ['date_joined', 'last_login',]
    ordering = ['username',]

    fieldsets = UserAdmin.fieldsets + (
        ('Підготовка до співбесіди', {
            'fields': ('target_position', 'experience_level'),
        }),
        ('Аватар', {
            'fields': ('avatar',),
        }),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Додаткова інформація', {
            'fields': ('email', 'target_position', 'experience_level'),
        }),
    )