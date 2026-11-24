from django.core.management.base import BaseCommand

from api import services


class Command(BaseCommand):
    help = (
        "Expire old statuses, moments, pending link ups and notifications. "
        "Run every 10-15 minutes (cron, systemd timer or a scheduler)."
    )

    def handle(self, *args, **options):
        counts = services.expire_stale()
        self.stdout.write(
            self.style.SUCCESS(
                "Expired: {statuses} statuses, {moments} moments, "
                "{linkups} pending link ups; deleted {notifications} old notifications.".format(
                    **counts
                )
            )
        )
