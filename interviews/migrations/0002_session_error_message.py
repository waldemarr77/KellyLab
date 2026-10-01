from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('interview_sessions', '0001_initial')]
    operations = [migrations.AddField(
        model_name='session', name='error_message',
        field=models.TextField(blank=True, verbose_name='Пояснення помилки'),
    )]
