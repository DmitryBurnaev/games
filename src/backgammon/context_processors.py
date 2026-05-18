from django.conf import settings
from django.http import HttpRequest


def registration(request: HttpRequest) -> dict[str, object]:
    """Expose global UI settings to templates."""
    return {
        "allow_user_registration": settings.ALLOW_USER_REGISTRATION,
        "app_version": settings.APP_VERSION,
    }
