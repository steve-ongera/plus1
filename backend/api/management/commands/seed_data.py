from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from api.models import Status, User, Venue
from api.services import status_expiry

VENUES = [
    # name, city, country, address, lat, lng, sponsored, description
    ("The Soda Bar", "Mombasa", "Kenya", "Nyali Road", -4.0295, 39.7149, True,
     "Relaxed spot for sodas, mocktails and good conversation."),
    ("Sundowner Deck", "Mombasa", "Kenya", "Mama Ngina Drive", -4.0640, 39.6770, False,
     "Sea-view deck, perfect at sunset."),
    ("Old Town Cafe", "Mombasa", "Kenya", "Old Town", -4.0620, 39.6800, False,
     "Coffee, juices and board games in the heart of Old Town."),
    ("Tembo Lounge", "Mombasa", "Kenya", "Digo Road", -4.0560, 39.6690, False,
     "Cocktails and live music on weekends."),
    ("Coral Beach Bar", "Diani", "Kenya", "Diani Beach Road", -4.2800, 39.5900, False,
     "Beachfront bar with a laid-back crowd."),
    ("Westlands Wine House", "Nairobi", "Kenya", "Westlands", -1.2676, 36.8108, False,
     "Wine flights and small plates."),
]

DEMO_USERS = [
    ("amina", "Amina", "SODA"),
    ("brian", "Brian", "ANY"),
    ("chloe", "Chloe", "WINE"),
    ("david", "David", "COFFEE"),
    ("esther", "Esther", "SODA"),
    ("farid", "Farid", "COCKTAILS"),
]


class Command(BaseCommand):
    help = "Load demo venues and users (development only)."

    def add_arguments(self, parser):
        parser.add_argument("--password", default="demo12345!", help="Password for demo users.")
        parser.add_argument("--force", action="store_true", help="Allow running when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Refusing to seed demo data when DEBUG is off (use --force).")

        venues = []
        for name, city, country, address, lat, lng, sponsored, description in VENUES:
            venue = Venue.objects.filter(name=name, city=city).first()
            if not venue:
                venue = Venue.objects.create(
                    name=name, city=city, country=country, address=address,
                    latitude=lat, longitude=lng, is_sponsored=sponsored,
                    description=description,
                )
            venues.append(venue)

        users = []
        for username, display, vibe in DEMO_USERS:
            user = User.objects.filter(username=username).first()
            if not user:
                user = User.objects.create_user(
                    username=username,
                    email=f"{username}@example.com",
                    password=options["password"],
                    display_name=display,
                    bio="Here for good company and a cold drink.",
                    birth_date=date(1996, 5, 17),
                )
            users.append((user, vibe))

        # Put the first four demo users out at the first two venues.
        for index, (user, vibe) in enumerate(users[:4]):
            Status.objects.update_or_create(
                user=user,
                defaults={
                    "intent": Status.Intent.OUT if index % 2 == 0 else Status.Intent.SOON,
                    "vibe": vibe,
                    "venue": venues[index % 2],
                    "note": "Come say hi!",
                    "expires_at": status_expiry(),
                },
            )

        self.stdout.write(self.style.SUCCESS(
            f"Seeded {len(venues)} venues and {len(users)} users."
        ))
        self.stdout.write(
            f"Demo logins: {', '.join(u for u, _, _ in DEMO_USERS)} / password: {options['password']}"
        )
