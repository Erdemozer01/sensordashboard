# sensors/database.py
import sqlite3
import time
import os
from .config import DB_PATH

current_scan_id = None
db_conn = None


def init_db():
    """Veritabanını başlatır ve yeni bir tarama kaydı oluşturur."""
    global current_scan_id, db_conn

    try:
        db_conn = sqlite3.connect(DB_PATH)
        cursor = db_conn.cursor()

        # Tabloları oluştur
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS servo_scans
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           start_time
                           REAL
                           UNIQUE,
                           status
                           TEXT
                       )''')

        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS scan_points
                       (
                           id
                           INTEGER
                           PRIMARY
                           KEY
                           AUTOINCREMENT,
                           scan_id
                           INTEGER,
                           angle_deg
                           REAL,
                           mesafe_cm
                           REAL,
                           hiz_cm_s
                           REAL,
                           timestamp
                           REAL,
                           FOREIGN
                           KEY
                       (
                           scan_id
                       ) REFERENCES servo_scans
                       (
                           id
                       )
                           )''')

        # Önceki yarım kalan taramaları işaretle
        cursor.execute("UPDATE servo_scans SET status = 'interrupted_prior_run' WHERE status = 'Calisiyor'")

        # Yeni tarama kaydı oluştur
        scan_start_time = time.time()
        cursor.execute("INSERT INTO servo_scans (start_time, status) VALUES (?, ?)",
                       (scan_start_time, 'Calisiyor'))
        current_scan_id = cursor.lastrowid
        db_conn.commit()

        print(f"[{os.getpid()}] Veritabanı '{DB_PATH}' hazırlandı. Yeni tarama ID: {current_scan_id}")
        return True
    except sqlite3.Error as e:
        print(f"[{os.getpid()}] Veritabanı başlatma/tarama kaydı oluşturma hatası: {e}")
        current_scan_id = None
        if db_conn:
            db_conn.close()
            db_conn = None
        return False


def save_scan_point(angle_deg, distance_cm, hiz_cm_s):
    """Bir tarama noktasını veritabanına kaydeder."""
    global db_conn, current_scan_id

    if not db_conn or not current_scan_id:
        print("Veritabanı bağlantısı veya tarama ID'si mevcut değil!")
        return False

    try:
        cursor = db_conn.cursor()
        current_timestamp = time.time()

        cursor.execute('''
                       INSERT INTO scan_points (scan_id, angle_deg, mesafe_cm, hiz_cm_s, timestamp)
                       VALUES (?, ?, ?, ?, ?)
                       ''', (current_scan_id, angle_deg, distance_cm, hiz_cm_s, current_timestamp))
        db_conn.commit()
        return True
    except Exception as e:
        print(f"[{os.getpid()}] Veri noktası kaydetme hatası: {e}")
        return False


def update_scan_status(status):
    """Mevcut taramanın durumunu günceller."""
    global db_conn, current_scan_id

    if not db_conn or not current_scan_id:
        return False

    try:
        cursor = db_conn.cursor()
        cursor.execute("UPDATE servo_scans SET status = ? WHERE id = ?",
                       (status, current_scan_id))
        db_conn.commit()
        print(f"[{os.getpid()}] Tarama ID {current_scan_id} durumu '{status}' olarak güncellendi.")
        return True
    except Exception as e:
        print(f"[{os.getpid()}] Tarama durumu güncellenirken hata: {e}")
        return False


def close_db_connection():
    """Veritabanı bağlantısını kapatır."""
    global db_conn

    if db_conn:
        try:
            db_conn.close()
            db_conn = None
            print(f"[{os.getpid()}] Veritabanı bağlantısı kapatıldı.")
            return True
        except Exception as e:
            print(f"[{os.getpid()}] Veritabanı bağlantısı kapatılırken hata: {e}")
            return False
    return True