"""Signals: create notifications automatically and clean up uploaded files."""
import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import LinkUp, Message, Moment, Notification, VenueMedia
from .services import notify, push_to_user

logger = logging.getLogger(__name__)


@receiver(post_save, sender=LinkUp)
def linkup_saved(sender, instance, created, update_fields=None, **kwargs):
    if created:
        notify(
            instance.receiver,
            Notification.Kind.LINKUP_RECEIVED,
            f"{instance.sender.display_name} wants to link up",
            instance.message or f"They're at {instance.venue.name}.",
            {
                "linkup_id": instance.id,
                "venue": instance.venue.slug,
                "sender": instance.sender.slug,
            },
        )
    elif (
        update_fields
        and "status" in update_fields
        and instance.status == LinkUp.State.ACCEPTED
    ):
        # Only acceptances are announced. Declines stay quiet on purpose.
        notify(
            instance.sender,
            Notification.Kind.LINKUP_ACCEPTED,
            f"{instance.receiver.display_name} accepted your link up",
            f"Say hi - you're both at {instance.venue.name}.",
            {
                "linkup_id": instance.id,
                "venue": instance.venue.slug,
                "receiver": instance.receiver.slug,
            },
        )


@receiver(post_save, sender=Message)
def message_created(sender, instance, created, **kwargs):
    if not created:
        return
    from .serializers import MessageSerializer  # local import: avoids a cycle at startup

    linkup = instance.linkup
    other = linkup.receiver if instance.sender_id == linkup.sender_id else linkup.sender

    # Live chat event for both open chat windows...
    payload = dict(MessageSerializer(instance).data)
    push_to_user(other.id, "chat.message", payload)
    push_to_user(instance.sender_id, "chat.message", payload)

    # ...and a stored notification for the recipient.
    notify(
        other,
        Notification.Kind.MESSAGE_NEW,
        instance.sender.display_name,
        instance.body[:80],
        {"linkup_id": linkup.id, "message_id": instance.id},
    )


def _delete_file(field_file):
    if field_file:
        try:
            field_file.delete(save=False)
        except Exception:  # pragma: no cover
            logger.warning("Could not delete file %s", field_file.name)


@receiver(post_delete, sender=Moment)
def moment_deleted(sender, instance, **kwargs):
    _delete_file(instance.file)


@receiver(post_delete, sender=VenueMedia)
def venue_media_deleted(sender, instance, **kwargs):
    _delete_file(instance.file)
