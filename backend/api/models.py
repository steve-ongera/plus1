"""All Plus1 models live in this one file."""
import os
import uuid
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.text import slugify


# ---------------------------------------------------------------------------
# Helpers (module-level so migrations can reference them)
# ---------------------------------------------------------------------------
def _random_name(folder, filename):
    """Store uploads under a random name so original filenames never leak."""
    ext = os.path.splitext(filename)[1].lower()[:10]
    return f"{folder}/{uuid.uuid4().hex}{ext}"


def avatar_upload_to(instance, filename):
    return _random_name("avatars", filename)


def venue_cover_upload_to(instance, filename):
    return _random_name("venues/covers", filename)


def venue_media_upload_to(instance, filename):
    return _random_name("venues/gallery", filename)


def moment_upload_to(instance, filename):
    return _random_name("moments", filename)


def status_expiry():
    return timezone.now() + timedelta(hours=settings.STATUS_TTL_HOURS)


def moment_expiry():
    return timezone.now() + timedelta(hours=settings.MOMENT_TTL_HOURS)


_SLUG_CHARS = "abcdefghjkmnpqrstuvwxyz23456789"


def generate_unique_slug(model, source, always_suffix=False, max_length=80):
    """Readable unique slug, e.g. 'the-soda-bar' or 'amina-k3x9'."""
    base = slugify(source)[: max_length - 5].strip("-") or "item"
    candidate = f"{base}-{get_random_string(4, _SLUG_CHARS)}" if always_suffix else base
    while model._default_manager.filter(slug=candidate).exists():
        candidate = f"{base}-{get_random_string(4, _SLUG_CHARS)}"
    return candidate


class SlugMixin(models.Model):
    """Adds an auto-generated, unique, read-only `slug`."""

    slug = models.SlugField(max_length=80, unique=True, blank=True, editable=False)

    SLUG_ALWAYS_SUFFIX = False

    class Meta:
        abstract = True

    def get_slug_source(self):
        return str(self)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_unique_slug(
                type(self), self.get_slug_source(), self.SLUG_ALWAYS_SUFFIX
            )
        super().save(*args, **kwargs)


class MediaKind(models.TextChoices):
    IMAGE = "IMAGE", "Image"
    VIDEO = "VIDEO", "Video"


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
class User(SlugMixin, AbstractUser):
    SLUG_ALWAYS_SUFFIX = True

    class Role(models.TextChoices):
        USER = "USER", "User"
        VENUE_OWNER = "VENUE_OWNER", "Venue owner"
        ADMIN = "ADMIN", "Admin"

    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.USER)
    display_name = models.CharField(max_length=60, blank=True)
    bio = models.CharField(max_length=280, blank=True)
    avatar = models.ImageField(upload_to=avatar_upload_to, blank=True, null=True)
    birth_date = models.DateField(null=True, blank=True)
    # Ghost Mode: hidden from every list, map count and search.
    is_ghost = models.BooleanField(default=False)
    last_seen = models.DateTimeField(null=True, blank=True)

    def get_slug_source(self):
        return self.display_name or self.username

    def save(self, *args, **kwargs):
        if not self.display_name:
            self.display_name = self.username
        super().save(*args, **kwargs)

    @property
    def age(self):
        if not self.birth_date:
            return None
        today = timezone.localdate()
        b = self.birth_date
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))

    def __str__(self):
        return self.display_name or self.username


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------
class Venue(SlugMixin, models.Model):
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    address = models.CharField(max_length=300, blank=True)
    city = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(validators=[MinValueValidator(-90), MaxValueValidator(90)])
    longitude = models.FloatField(validators=[MinValueValidator(-180), MaxValueValidator(180)])
    cover_image = models.ImageField(upload_to=venue_cover_upload_to, blank=True, null=True)
    is_sponsored = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [models.Index(fields=["latitude", "longitude"])]

    def get_slug_source(self):
        return self.name

    def __str__(self):
        return f"{self.name} ({self.city})" if self.city else self.name


class VenueMedia(models.Model):
    """Gallery item (photo or video) shown on a venue page."""

    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="media")
    kind = models.CharField(max_length=10, choices=MediaKind.choices, default=MediaKind.IMAGE)
    file = models.FileField(upload_to=venue_media_upload_to)
    caption = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "venue media"

    def __str__(self):
        return f"{self.venue.name} - {self.kind}"


# ---------------------------------------------------------------------------
# Status / check-in
# ---------------------------------------------------------------------------
class Status(models.Model):
    """What a user is up to right now. One row per user; expires automatically."""

    class Intent(models.TextChoices):
        OUT = "OUT", "Out now"
        SOON = "SOON", "Going out soon"
        IN = "IN", "Staying in"

    class Vibe(models.TextChoices):
        ANY = "ANY", "Anything"
        SODA = "SODA", "Just soda"
        COFFEE = "COFFEE", "Coffee"
        WINE = "WINE", "Wine"
        COCKTAILS = "COCKTAILS", "Cocktails"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="status")
    intent = models.CharField(max_length=10, choices=Intent.choices, default=Intent.IN)
    vibe = models.CharField(max_length=12, choices=Vibe.choices, default=Vibe.ANY)
    venue = models.ForeignKey(
        Venue, on_delete=models.SET_NULL, null=True, blank=True, related_name="statuses"
    )
    note = models.CharField(max_length=140, blank=True)
    expires_at = models.DateTimeField(default=status_expiry)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "statuses"
        indexes = [models.Index(fields=["venue", "expires_at"])]

    @property
    def is_active(self):
        return self.intent != self.Intent.IN and self.expires_at > timezone.now()

    def __str__(self):
        return f"{self.user} - {self.intent}"


# ---------------------------------------------------------------------------
# Moments (photo / video posted at a venue, expires after 24h)
# ---------------------------------------------------------------------------
class Moment(SlugMixin, models.Model):
    SLUG_ALWAYS_SUFFIX = True

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="moments")
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="moments")
    kind = models.CharField(max_length=10, choices=MediaKind.choices)
    file = models.FileField(upload_to=moment_upload_to)
    caption = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=moment_expiry)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["venue", "expires_at"])]

    def get_slug_source(self):
        return f"{self.venue.name} moment"

    def __str__(self):
        return f"{self.author} @ {self.venue.name}"


# ---------------------------------------------------------------------------
# Link ups + chat
# ---------------------------------------------------------------------------
class LinkUp(models.Model):
    """A mutual opt-in request. Chat only exists once it is ACCEPTED."""

    class State(models.TextChoices):
        PENDING = "PENDING", "Pending"
        ACCEPTED = "ACCEPTED", "Accepted"
        DECLINED = "DECLINED", "Declined"
        EXPIRED = "EXPIRED", "Expired"
        CLOSED = "CLOSED", "Closed"

    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="linkups_sent")
    receiver = models.ForeignKey(User, on_delete=models.CASCADE, related_name="linkups_received")
    venue = models.ForeignKey(Venue, on_delete=models.CASCADE, related_name="linkups")
    message = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Only one open (pending/accepted) request per pair per venue.
            models.UniqueConstraint(
                fields=["sender", "receiver", "venue"],
                condition=Q(status__in=["PENDING", "ACCEPTED"]),
                name="unique_open_linkup",
            ),
            models.CheckConstraint(condition=~Q(sender=F("receiver")), name="linkup_not_self"),
        ]

    def __str__(self):
        return f"{self.sender} -> {self.receiver} ({self.status})"


class Message(models.Model):
    linkup = models.ForeignKey(LinkUp, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages_sent")
    body = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["linkup", "created_at"])]

    def __str__(self):
        return f"{self.sender}: {self.body[:30]}"


# ---------------------------------------------------------------------------
# Notifications (stored AND pushed over WebSocket)
# ---------------------------------------------------------------------------
class Notification(models.Model):
    class Kind(models.TextChoices):
        LINKUP_RECEIVED = "linkup.received", "Link up received"
        LINKUP_ACCEPTED = "linkup.accepted", "Link up accepted"
        MESSAGE_NEW = "message.new", "New message"
        SYSTEM = "system", "System"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.SYSTEM)
    title = models.CharField(max_length=140)
    body = models.CharField(max_length=300, blank=True)
    data = models.JSONField(default=dict, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return f"{self.user}: {self.title}"


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
class Block(models.Model):
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name="blocks_made")
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name="blocks_received")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["blocker", "blocked"], name="unique_block"),
            models.CheckConstraint(condition=~Q(blocker=F("blocked")), name="block_not_self"),
        ]

    def __str__(self):
        return f"{self.blocker} blocked {self.blocked}"


class Report(models.Model):
    class Reason(models.TextChoices):
        HARASSMENT = "HARASSMENT", "Harassment"
        UNSAFE = "UNSAFE", "Makes me feel unsafe"
        FAKE = "FAKE", "Fake profile"
        SPAM = "SPAM", "Spam"
        MEDIA = "MEDIA", "Inappropriate photo/video"
        OTHER = "OTHER", "Other"

    class State(models.TextChoices):
        OPEN = "OPEN", "Open"
        REVIEWING = "REVIEWING", "Reviewing"
        RESOLVED = "RESOLVED", "Resolved"

    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reports_made")
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reports_received")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    details = models.TextField(max_length=1000, blank=True)
    status = models.CharField(max_length=12, choices=State.choices, default=State.OPEN)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reporter} reported {self.reported_user} ({self.reason})"
