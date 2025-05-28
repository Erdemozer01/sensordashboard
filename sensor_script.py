#!/usr/bin/env python3
# sensor_script.py
import atexit
import sys
import os
from sensors import (
    init_hardware,
    cleanup_hardware,
    init_db,
    close_db_connection,
    update_scan_status,
    acquire_lock_and_pid,
    release_lock,
    perform_scan
)

script_exit_status = 'interrupted_unexpectedly'


def release_resources_on_exit():
    """Betik çıkışında kaynakları serbest bırakır."""
    global script_exit_status

    pid = os.getpid()
    print(f"[{pid}] `release_resources_on_exit` çağrıldı. Betik çıkış durumu: {script_exit_status}")

    close_db_connection()
    update_scan_status(script_exit_status)
    cleanup_hardware()
    release_lock()

    print(f"[{pid}] Temizleme fonksiyonu tamamlandı. Betik çıkıyor.")


if __name__ == "__main__":
    atexit.register(release_resources_on_exit)

    if not acquire_lock_and_pid():
        sys.exit(1)

    if not init_hardware():
        sys.exit(1)

    if not init_db():
        print(f"[{os.getpid()}] HATA: Veritabanı başlatılamadı. Çıkılıyor.")
        sys.exit(1)

    script_exit_status = perform_scan()

    # Normal çıkış durumunda release_resources_on_exit() otomatik olarak çağrılacak
    sys.exit(0)