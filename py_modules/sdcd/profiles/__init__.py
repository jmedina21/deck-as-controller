"""Emulated controller types."""
from .base import Battery, Encoder, Profile
from .dualsense import DualSense
from .steam import SteamController
from .xbox import Xbox

PROFILES: dict[str, Profile] = {p.id: p for p in (DualSense(), DualSense(edge=True), Xbox(), SteamController())}
DEFAULT_PROFILE = "dualsense_edge"


def get_profile(profile_id: str) -> Profile:
    return PROFILES.get(profile_id) or PROFILES[DEFAULT_PROFILE]
