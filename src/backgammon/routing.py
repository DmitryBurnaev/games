from django.urls import path

from . import consumers

# Django's path stub only accepts HTTP views; Channels also accepts ASGI apps.
websocket_urlpatterns = [
    path("ws/games/<int:game_id>/", consumers.GameStateConsumer.as_asgi()),  # type: ignore[arg-type]
]
