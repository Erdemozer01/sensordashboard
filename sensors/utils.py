# sensors/utils.py
import os
import fcntl
import time
from .config import LOCK_FILE_PATH, PID_FILE_PATH

lock_file_handle = None

def acquire_lock_and_pid():
    """Betik için bir kilit ve PID dosyası oluşturur."""
    global lock_file_handle
    
    try:
        if os.path.exists(PID_FILE_PATH):
            os.remove(PID_FILE_PATH)
    except OSError:
        pass

    try:
        lock_file_handle = open(LOCK_FILE_PATH, 'w')
        fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with open(PID_FILE_PATH, 'w') as pf:
            pf.write(str(os.getpid()))
        print(f"[{os.getpid()}] Betik kilidi ({LOCK_FILE_PATH}) ve PID ({PID_FILE_PATH}) başarıyla oluşturuldu.")
        return True
    except BlockingIOError:
        print(f"[{os.getpid()}] '{LOCK_FILE_PATH}' kilitli. Sensör betiği zaten çalışıyor olabilir.")
        if lock_file_handle:
            lock_file_handle.close()
        lock_file_handle = None
        return False
    except Exception as e:
        print(f"[{os.getpid()}] Kilit/PID alınırken beklenmedik bir hata: {e}")
        if lock_file_handle:
            lock_file_handle.close()
        lock_file_handle = None
        return False

def release_lock():
    """Betik kilidini ve PID dosyasını serbest bırakır."""
    global lock_file_handle
    pid = os.getpid()
    
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle.fileno(), fcntl.LOCK_UN)
            lock_file_handle.close()
            print(f"[{pid}] Kilit ({LOCK_FILE_PATH}) serbest bırakıldı.")
        except Exception as e:
            print(f"[{pid}] Kilit serbest bırakılırken hata: {e}")

    for f_path in [PID_FILE_PATH, LOCK_FILE_PATH]:
        try:
            if os.path.exists(f_path):
                if f_path == PID_FILE_PATH:
                    remove_this_pid_file = False
                    try:
                        with open(f_path, 'r') as pf_check:
                            if int(pf_check.read().strip()) == pid:
                                remove_this_pid_file = True
                    except:
                        pass

                    if remove_this_pid_file:
                        os.remove(f_path)
                        print(f"[{pid}] Dosya silindi: {f_path}")
                    else:
                        print(f"[{pid}] PID dosyası ({f_path}) başka bir processe ait veya okunamadı, silinmedi.")
                elif f_path == LOCK_FILE_PATH:
                    os.remove(f_path)
                    print(f"[{pid}] Kilit fiziksel dosyası ({LOCK_FILE_PATH}) silindi.")
        except OSError as e:
            print(f"[{pid}] Dosya ({f_path}) silinirken hata: {e}")