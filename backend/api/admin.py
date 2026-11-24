from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    Block,
    LinkUp,
    Message,
    Moment,
    Notification,
    Report,
    Status,
    User,
    Venue,
    VenueMedia,
)

admin.site.site_header = "Plus1 admin"
admin.site.site_title = "Plus1"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "display_name", "role", "is_ghost", "is_active", "date_joined")
    list_filter = DjangoUserAdmin.list_filter + ("role", "is_ghost")
    search_fields = ("username", "email", "display_name", "slug")
    readonly_fields = ("slug", "last_login", "date_joined")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("Plus1", {"fields": ("slug", "display_name", "bio", "avatar", "birth_date", "role", "is_ghost", "last_seen")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("Plus1", {"fields": ("email", "display_name", "birth_date")}),
    )


class VenueMediaInline(admin.TabularInline):
    model = VenueMedia
    extra = 0


@admin.register(Venue)
class VenueAdmin(admin.ModelAdmin):
    list_display = ("name", "city", "country", "is_sponsored", "is_active")
    list_filter = ("is_sponsored", "is_active", "country")
    search_fields = ("name", "city", "address", "slug")
    readonly_fields = ("slug",)
    inlines = [VenueMediaInline]


@admin.register(Status)
class StatusAdmin(admin.ModelAdmin):
    list_display = ("user", "intent", "vibe", "venue", "expires_at")
    list_filter = ("intent", "vibe")
    search_fields = ("user__username", "user__display_name", "venue__name")
    autocomplete_fields = ()


@admin.register(Moment)
class MomentAdmin(admin.ModelAdmin):
    list_display = ("author", "venue", "kind", "created_at", "expires_at")
    list_filter = ("kind",)
    search_fields = ("author__username", "venue__name", "slug")
    readonly_fields = ("slug",)


@admin.register(LinkUp)
class LinkUpAdmin(admin.ModelAdmin):
    list_display = ("sender", "receiver", "venue", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("sender__username", "receiver__username", "venue__name")


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("sender", "linkup", "created_at", "read_at")
    search_fields = ("sender__username", "body")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "kind", "title", "is_read", "created_at")
    list_filter = ("kind", "is_read")
    search_fields = ("user__username", "title")


@admin.register(Block)
class BlockAdmin(admin.ModelAdmin):
    list_display = ("blocker", "blocked", "created_at")
    search_fields = ("blocker__username", "blocked__username")


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("reported_user", "reporter", "reason", "status", "created_at")
    list_filter = ("status", "reason")
    list_editable = ("status",)
    search_fields = ("reported_user__username", "reporter__username", "details")
