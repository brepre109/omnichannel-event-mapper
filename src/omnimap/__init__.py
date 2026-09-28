"""omnichannel-event-mapper: map channel order/shipment payloads onto one canonical schema."""
from .mapping import MappingSet
from .store import Workspace, timeline
from .validate import validate

__all__ = ["MappingSet", "Workspace", "timeline", "validate"]
__version__ = "0.1.0"
