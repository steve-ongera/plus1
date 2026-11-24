"""Business rules for Plus1.

Views stay thin: every rule (who can link up with whom, when chat opens,
what expires, how notifications are pushed) lives here, so it can never be
bypassed from the frontend.
"""
import logging
import math
import os
from datetime import timedelta

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer 
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from PIL import Image
from rest_framework.exceptions import APIException

from .models import (
    Block,
    LinkUp,
    MediaKind,
    Message,
    Moment,
    Notification,
    Status,
    Venue,
    status_expiry,
)

logger = logging.getLogger(__name__)

VISIBLE_INTENTS = [Status.Intent.OUT, Status.Intent.SOON]
OPEN_LINKUP_STATES = [LinkUp.State.PENDING, LinkUp.State.ACCEPTED]
MAX_PENDING_LINKUPS = 10
MAX_MESSAGE_LENGTH = 1000
MB = 1024 * 1024


class ServiceError(APIException):
    """A rule was broken. DRF turns it into a JSON error response."""

    status_code = 400
    default_code = "error"

    def __init__(self, detail, code=None, status_code=None):
        if status_code:
            self.status_code = status_code
        super().__init__(detail=detail, code=code or self.default_code)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def calculate_age(born, today=None):
    today = today or timezone.localdate()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def haversine_km(lat1, lng1, lat2, lng2):
    """Great-circle distance between two points, in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def blocked_ids(user):
    """IDs of everyone the user blocked OR who blocked the user."""
    ids = set()
    pairs = Block.objects.filter(Q(blocker=user) | Q(blocked=user)).values_list(
        "blocker_id", "blocked_id"
    )
    for a, b in pairs:
        ids.add(b if a == user.id else a)
    return ids


# ---------------------------------------------------------------------------
# Media validation (images, videos)
# ---------------------------------------------------------------------------
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def media_kind_from_name(name):
    ext = os.path.splitext(name or "")[1].lower()
    if ext in IMAGE_EXTS:
        return MediaKind.IMAGE
    if ext in VIDEO_EXTS:
        return MediaKind.VIDEO
    return None


def validate_image(f):
    """Size limit + real image check (not just the file extension)."""
    if f.size > settings.MAX_IMAGE_MB * MB:
        raise DjangoValidationError(f"Image is too large (max {settings.MAX_IMAGE_MB} MB).")
    fmt = ""
    try:
        f.seek(0)
        img = Image.open(f)
        fmt = (img.format or "").upper()
        img.verify()
    except Exception:
        raise DjangoValidationError("This file isn't a valid image.")
    finally:
        f.seek(0)
    if fmt not in IMAGE_FORMATS:
        raise DjangoValidationError("Unsupported image type. Use JPG, PNG or WebP.")


def _looks_like_video(f):
    head = f.read(12)
    f.seek(0)
    if head[4:8] == b"ftyp":  # MP4 / MOV
        return True
    if head[:4] == b"\x1a\x45\xdf\xa3":  # WebM / Matroska
        return True
    return False


def validate_video(f):
    """Size limit + file signature check. (Duration limits need ffprobe - later.)"""
    if f.size > settings.MAX_VIDEO_MB * MB:
        raise DjangoValidationError(f"Video is too large (max {settings.MAX_VIDEO_MB} MB).")
    if not _looks_like_video(f):
        raise DjangoValidationError("This file isn't a valid video (use MP4, MOV or WebM).")


def validate_media(f):
    """Validate an uploaded photo/video and return its MediaKind."""
    kind = media_kind_from_name(getattr(f, "name", ""))
    if kind == MediaKind.IMAGE:
        validate_image(f)
    elif kind == MediaKind.VIDEO:
        validate_video(f)
    else:
        raise DjangoValidationError(
            "Unsupported file type. Use JPG, PNG, WebP, MP4, MOV or WebM."
        )
    return kind


# ---------------------------------------------------------------------------
# Real-time push + notifications
# ---------------------------------------------------------------------------
def push_to_user(user_id, event_type, payload):
    """Send an event to every open WebSocket of a user (never breaks the request)."""
    layer = get_channel_layer()
    if layer is None:
        return

    def _send():
        try:
            async_to_sync(layer.group_send)(
                f"user_{user_id}", {"type": event_type, "payload": payload}
            )
        except Exception:  # pragma: no cover - the DB row is the source of truth
            logger.exception("Could not push %s to user %s", event_type, user_id)

    transaction.on_commit(_send)


def serialize_notification(n):
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "body": n.body,
        "data": n.data,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat(),
    }


def notify(user, kind, title, body="", data=None):
    """Store a notification AND push it live over the user's WebSocket."""
    n = Notification.objects.create(
        user=user, kind=kind, title=title[:140], body=body[:300], data=data or {}
    )
    push_to_user(user.id, "notification.message", serialize_notification(n))
    return n


def mark_notifications_read(user, ids=None):
    qs = Notification.objects.filter(user=user, is_read=False)
    if ids:
        qs = qs.filter(id__in=ids)
    return qs.update(is_read=True)


# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------
def venues_with_headcount():
    """Active venues annotated with how many (non-ghost) people are there now."""
    now = timezone.now()
    return Venue.objects.filter(is_active=True).annotate(
        headcount=Count(
            "statuses",
            filter=Q(
                statuses__expires_at__gt=now,
                statuses__intent__in=VISIBLE_INTENTS,
                statuses__user__is_ghost=False,
                statuses__user__is_active=True,
            ),
            distinct=True,
        )
    )


def nearby_venues(lat=None, lng=None, radius_km=None, q=None):
    """Venues, optionally filtered by text and sorted by distance from lat/lng."""
    qs = venues_with_headcount()
    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(city__icontains=q) | Q(address__icontains=q)
        )
    if lat is None or lng is None:
        return qs.order_by("-is_sponsored", "name")

    radius_km = min(radius_km or settings.DEFAULT_RADIUS_KM, settings.MAX_RADIUS_KM)
    # Cheap bounding box first, exact Haversine second. (Ignores the +/-180 wrap.)
    dlat = radius_km / 111.0
    dlng = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    qs = qs.filter(
        latitude__range=(lat - dlat, lat + dlat),
        longitude__range=(lng - dlng, lng + dlng),
    )
    results = []
    for venue in qs:
        distance = haversine_km(lat, lng, venue.latitude, venue.longitude)
        if distance <= radius_km:
            venue.distance_km = round(distance, 2)
            results.append(venue)
    results.sort(key=lambda v: v.distance_km)
    return results


# ---------------------------------------------------------------------------
# Status + check-in
# ---------------------------------------------------------------------------
def get_active_status(user):
    """The user's status if they are out / going out and it hasn't expired."""
    return (
        Status.objects.filter(user=user, expires_at__gt=timezone.now())
        .exclude(intent=Status.Intent.IN)
        .select_related("venue")
        .first()
    )


def set_status(user, intent, vibe=None, note=None):
    status, _ = Status.objects.get_or_create(user=user)
    status.intent = intent
    if vibe is not None:
        status.vibe = vibe
    if note is not None:
        status.note = note
    if intent == Status.Intent.IN:
        status.venue = None
    status.expires_at = status_expiry()
    status.save()
    return status


def clear_status(user):
    Status.objects.filter(user=user).delete()


def check_in(user, venue):
    if not venue.is_active:
        raise ServiceError("This venue isn't available.", code="venue_inactive")
    if user.is_ghost:
        raise ServiceError("Turn off Ghost Mode to check in.", code="ghost_mode")
    status, _ = Status.objects.get_or_create(user=user)
    if status.intent == Status.Intent.IN:
        status.intent = Status.Intent.OUT
    status.venue = venue
    status.expires_at = status_expiry()
    status.save()
    return status


def check_out(user, venue=None):
    status = Status.objects.filter(user=user).first()
    if status and status.venue_id and (venue is None or status.venue_id == venue.id):
        status.venue = None
        status.save()
    return status


def ensure_checked_in(user, venue):
    status = get_active_status(user)
    if not status or status.venue_id != venue.id:
        raise ServiceError(
            "Check in at this venue first.", code="not_checked_in", status_code=403
        )
    return status


def people_at_venue(viewer, venue):
    """Who is checked in here. Only people who are checked in here can look."""
    mine = get_active_status(viewer)
    if not mine or mine.venue_id != venue.id:
        raise ServiceError(
            "Check in at this venue to see who's here.",
            code="not_checked_in",
            status_code=403,
        )
    return (
        Status.objects.filter(
            venue=venue,
            expires_at__gt=timezone.now(),
            intent__in=VISIBLE_INTENTS,
            user__is_ghost=False,
            user__is_active=True,
        )
        .exclude(user=viewer)
        .exclude(user_id__in=blocked_ids(viewer))
        .select_related("user")
        .order_by("-updated_at")
    )


# ---------------------------------------------------------------------------
# Moments
# ---------------------------------------------------------------------------
def visible_moments(user, venue_slug=None):
    qs = (
        Moment.objects.filter(expires_at__gt=timezone.now(), venue__is_active=True)
        .filter(Q(author__is_ghost=False) | Q(author=user))
        .exclude(author_id__in=blocked_ids(user))
        .select_related("author", "venue")
    )
    if venue_slug:
        qs = qs.filter(venue__slug=venue_slug)
    return qs


# ---------------------------------------------------------------------------
# Link ups
# ---------------------------------------------------------------------------
def send_linkup(sender, receiver, venue, message=""):
    if sender.id == receiver.id:
        raise ServiceError("You can't link up with yourself.", code="self")
    if sender.is_ghost:
        raise ServiceError("Turn off Ghost Mode to send link ups.", code="ghost_mode")

    mine = get_active_status(sender)
    if not mine or mine.venue_id != venue.id:
        raise ServiceError(
            "Check in at this venue first.", code="not_checked_in", status_code=403
        )

    theirs = get_active_status(receiver)
    unavailable = (
        not theirs
        or theirs.venue_id != venue.id
        or receiver.is_ghost
        or not receiver.is_active
        or receiver.id in blocked_ids(sender)
    )
    if unavailable:
        # One generic message on purpose: never reveal blocks or Ghost Mode.
        raise ServiceError("That person isn't available at this venue.", code="unavailable")

    if (
        LinkUp.objects.filter(sender=sender, status=LinkUp.State.PENDING).count()
        >= MAX_PENDING_LINKUPS
    ):
        raise ServiceError(
            "You have too many pending requests. Wait for replies first.",
            code="too_many_pending",
            status_code=429,
        )

    already_open = (
        LinkUp.objects.filter(venue=venue, status__in=OPEN_LINKUP_STATES)
        .filter(Q(sender=sender, receiver=receiver) | Q(sender=receiver, receiver=sender))
        .exists()
    )
    if already_open:
        raise ServiceError(
            "You already have an open link up with this person.",
            code="duplicate",
            status_code=409,
        )

    return LinkUp.objects.create(
        sender=sender, receiver=receiver, venue=venue, message=(message or "").strip()
    )


def respond_to_linkup(user, linkup, accept):
    if linkup.receiver_id != user.id:
        raise ServiceError(
            "Only the person who received the request can respond.",
            code="forbidden",
            status_code=403,
        )
    if linkup.status != LinkUp.State.PENDING:
        raise ServiceError(
            "This request has already been answered.", code="not_pending", status_code=409
        )

    now = timezone.now()
    if linkup.created_at < now - timedelta(hours=settings.LINKUP_TTL_HOURS):
        linkup.status = LinkUp.State.EXPIRED
        linkup.responded_at = now
        linkup.save(update_fields=["status", "responded_at"])
        raise ServiceError("This request has expired.", code="expired", status_code=410)

    if accept and linkup.sender_id in blocked_ids(user):
        raise ServiceError("That person isn't available.", code="unavailable")

    linkup.status = LinkUp.State.ACCEPTED if accept else LinkUp.State.DECLINED
    linkup.responded_at = now
    linkup.save(update_fields=["status", "responded_at"])
    return linkup


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def send_message(user, linkup, body):
    if user.id not in (linkup.sender_id, linkup.receiver_id):
        raise ServiceError("Not allowed.", code="forbidden", status_code=403)
    if linkup.status != LinkUp.State.ACCEPTED:
        raise ServiceError(
            "Chat opens once the link up is accepted.", code="chat_locked", status_code=403
        )
    other_id = linkup.receiver_id if user.id == linkup.sender_id else linkup.sender_id
    if other_id in blocked_ids(user):
        raise ServiceError("You can't message this person.", code="unavailable", status_code=403)

    body = (body or "").strip()
    if not body:
        raise ServiceError("Message can't be empty.", code="empty")
    if len(body) > MAX_MESSAGE_LENGTH:
        raise ServiceError(
            f"Message is too long (max {MAX_MESSAGE_LENGTH} characters).", code="too_long"
        )
    return Message.objects.create(linkup=linkup, sender=user, body=body)


def mark_messages_read(user, linkup):
    return (
        Message.objects.filter(linkup=linkup, read_at__isnull=True)
        .exclude(sender=user)
        .update(read_at=timezone.now())
    )


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
def block_user(blocker, target):
    if blocker.id == target.id:
        raise ServiceError("You can't block yourself.", code="self")
    block, _ = Block.objects.get_or_create(blocker=blocker, blocked=target)

    # Close any open link up between the two. `.update()` skips signals, so
    # nobody gets a notification about it.
    between = Q(sender=blocker, receiver=target) | Q(sender=target, receiver=blocker)
    now = timezone.now()
    LinkUp.objects.filter(between, status=LinkUp.State.PENDING).update(
        status=LinkUp.State.DECLINED, responded_at=now
    )
    LinkUp.objects.filter(between, status=LinkUp.State.ACCEPTED).update(
        status=LinkUp.State.CLOSED, responded_at=now
    )
    return block


# ---------------------------------------------------------------------------
# Housekeeping (run by `manage.py expire_data` every 10-15 minutes)
# ---------------------------------------------------------------------------
def expire_stale():
    now = timezone.now()

    statuses, _ = Status.objects.filter(expires_at__lte=now).delete()
    # Iterating through the queryset lets the post_delete signal remove the files.
    moments, _ = Moment.objects.filter(expires_at__lte=now).delete()
    linkups = LinkUp.objects.filter(
        status=LinkUp.State.PENDING,
        created_at__lte=now - timedelta(hours=settings.LINKUP_TTL_HOURS),
    ).update(status=LinkUp.State.EXPIRED, responded_at=now)
    notifications, _ = Notification.objects.filter(
        created_at__lte=now - timedelta(days=30)
    ).delete()

    return {
        "statuses": statuses,
        "moments": moments,
        "linkups": linkups,
        "notifications": notifications,
    }
