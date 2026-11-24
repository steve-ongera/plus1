"""App-level URLs. Mounted at /api/ by config/urls.py."""
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register("venues", views.VenueViewSet, basename="venue")
router.register("moments", views.MomentViewSet, basename="moment")
router.register("linkups", views.LinkUpViewSet, basename="linkup")
router.register("blocks", views.BlockViewSet, basename="block")

urlpatterns = [
    path("health/", views.health, name="health"),
    # auth + profile
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("me/", views.MeView.as_view(), name="me"),
    path("users/<slug:slug>/", views.PublicUserView.as_view(), name="public-user"),
    # status
    path("status/", views.StatusView.as_view(), name="status"),
    # notifications
    path("notifications/", views.NotificationListView.as_view(), name="notifications"),
    path("notifications/read/", views.NotificationReadView.as_view(), name="notifications-read"),
    # safety
    path("reports/", views.ReportCreateView.as_view(), name="report"),
    # venues, moments, linkups, blocks
    path("", include(router.urls)),
]
