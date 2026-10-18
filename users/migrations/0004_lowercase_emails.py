from django.db import migrations
from django.db.models.functions import Lower

import users.models


def lowercase_emails(apps, schema_editor):
    users = apps.get_model('users', 'CustomUser').objects
    taken = set(users.values_list(Lower('email'), flat=True).filter(email=Lower('email')))
    for user in users.exclude(email=Lower('email')):
        email = user.email.strip().lower()
        # Two accounts differing only in case: the one already in lower case keeps the address,
        # the other is left as is for the admin to resolve.
        if email in taken:
            continue
        taken.add(email)
        user.email = email
        user.save(update_fields=['email'])


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0003_customuser_email_verified'),
    ]

    operations = [
        migrations.AlterModelManagers(
            name='customuser',
            managers=[
                ('objects', users.models.CustomUserManager()),
            ],
        ),
        migrations.RunPython(lowercase_emails, migrations.RunPython.noop),
    ]
