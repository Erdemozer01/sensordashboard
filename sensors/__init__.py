# sensors/__init__.py
from .hardware import init_hardware, cleanup_hardware
from .database import init_db, close_db_connection, update_scan_status
from .utils import acquire_lock_and_pid, release_lock
from .scan_logic import perform_scan