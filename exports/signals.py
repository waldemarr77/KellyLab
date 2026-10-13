from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import Export


@receiver(post_delete, sender=Export)
def delete_export_file(sender, instance, **kwargs):
    """Remove the file once the row is gone for good (session or account deleted)."""
    if instance.file:
        # After commit: a rolled-back delete must not lose the file.
        name, storage = instance.file.name, instance.file.storage
        transaction.on_commit(lambda: storage.delete(name))
