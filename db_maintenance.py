# db_maintenance.py

import sqlite3
import os

# --- Ayarlar ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME_ONLY = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_NAME_ONLY)
# Saklanacak en son tarama sayısı
SCAN_RETENTION_COUNT = 100

def get_db_connection():
    """Veritabanına R/W modunda bir bağlantı açar."""
    if not os.path.exists(DB_PATH):
        print(f"Hata: Veritabanı dosyası bulunamadı: {DB_PATH}")
        return None
    try:
        # Bu betik değişiklik yapacağı için 'ro' (read-only) modunda AÇMIYORUZ.
        conn = sqlite3.connect(DB_PATH, timeout=10)
        return conn
    except Exception as e:
        print(f"Veritabanı bağlantı hatası: {e}")
        return None

def main():
    print("--- Veritabanı Bakım Betiği Başlatıldı ---")
    conn = get_db_connection()
    if not conn:
        return

    try:
        # Adım 1: Dosyanın mevcut boyutunu al
        initial_size_bytes = os.path.getsize(DB_PATH)
        print(f"İşlem öncesi veritabanı boyutu: {initial_size_bytes / 1024 / 1024:.2f} MB")

        # Adım 2: Tüm tarama ID'lerini al ve hangilerinin silineceğine karar ver
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM servo_scans ORDER BY start_time DESC")
        all_scan_ids = [row[0] for row in cursor.fetchall()]

        if len(all_scan_ids) <= SCAN_RETENTION_COUNT:
            print(f"Toplam tarama sayısı ({len(all_scan_ids)}) saklanacak sayıdan ({SCAN_RETENTION_COUNT}) az veya eşit. Silme işlemi yapılmayacak.")
            return

        ids_to_delete = all_scan_ids[SCAN_RETENTION_COUNT:]
        print(f"Toplam {len(all_scan_ids)} tarama bulundu. En eski {len(ids_to_delete)} tarama silinecek.")

        # Adım 3: Yabancı anahtar (foreign key) desteğini aktif et
        # Bu sayede servo_scans'den bir kayıt silindiğinde,
        # ilişkili tüm scan_points kayıtları da otomatik silinir.
        cursor.execute("PRAGMA foreign_keys = ON;")

        # Adım 4: Eski kayıtları sil
        # Hızlı silme için '?' placeholdera bir tuple listesi veriyoruz.
        placeholders = [(id,) for id in ids_to_delete]
        cursor.executemany("DELETE FROM servo_scans WHERE id = ?", placeholders)
        conn.commit()
        print(f"{cursor.rowcount} ana tarama kaydı ve ilişkili tüm noktalar başarıyla silindi.")

        # Adım 5: Veritabanı dosyasını küçült (En Önemli Adım)
        print("Veritabanı dosyası 'VACUUM' komutu ile yeniden yapılandırılıyor ve küçültülüyor...")
        cursor.execute("VACUUM;")
        conn.commit()
        print("'VACUUM' işlemi tamamlandı.")

        # Adım 6: Dosyanın son boyutunu göster
        final_size_bytes = os.path.getsize(DB_PATH)
        print(f"İşlem sonrası veritabanı boyutu: {final_size_bytes / 1024 / 1024:.2f} MB")
        size_reduction = initial_size_bytes - final_size_bytes
        print(f"Toplam küçülme: {size_reduction / 1024:.2f} KB")

    except Exception as e:
        print(f"Bakım sırasında bir hata oluştu: {e}")
    finally:
        if conn:
            conn.close()
        print("--- Veritabanı Bakım Betiği Tamamlandı ---")


if __name__ == "__main__":
    main()