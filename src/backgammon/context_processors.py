from django.conf import settings
from django.http import HttpRequest


def registration(request: HttpRequest) -> dict[str, bool]:
    """Expose user-registration availability to auth templates."""
    return {"allow_user_registration": settings.ALLOW_USER_REGISTRATION}
