"""TUI tabs package (split by feature) with stable import surface."""
from .common import PAYMENT_METHODS, PAYMENT_TYPES, SOURCES, push_modal, show_error, _room_options, _category_options
from .front_desk import FrontDeskTab
from .reservations import ReservationsTab
from .pos_inventory import PosInventoryTab, NewOrderTab, InventorySubTab
from .accounting import AccountingTab
from .settings import SettingsTab
from .assistant import AssistantTab

__all__ = [
    "PAYMENT_METHODS","PAYMENT_TYPES","SOURCES","push_modal","show_error","_room_options","_category_options",
    "FrontDeskTab","ReservationsTab","PosInventoryTab","NewOrderTab","InventorySubTab",
    "AccountingTab","SettingsTab","AssistantTab",
]
