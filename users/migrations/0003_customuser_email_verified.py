from django.db import migrations, models


def verify_existing_users(apps, schema_editor):
    # Accounts created before verification existed keep access to generation.
    apps.get_model('users', 'CustomUser').objects.update(email_verified=True)


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0002_profile_cleanup'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='email_verified',
            field=models.BooleanField(default=False, verbose_name='Пошту підтверджено'),
        ),
        migrations.RunPython(verify_existing_users, migrations.RunPython.noop),
    ]
