# servo_test.py

from gpiozero import Servo
from time import sleep
import sys

# ======================================================================
# --- AYARLAR ---
# ======================================================================
# Lütfen servonuzun Sinyal kablosunu bağladığınız GPIO pininin
# numarasını buraya yazın.
SERVO_PIN = 12
# ======================================================================


# --- GEREKLİ KONTROLLER ---
try:
    # GPIO Zero'nun pin fabrikasının doğru ayarlandığından emin olalım.
    # Bu, uzak bir bilgisayardan çalıştırırken veya bazı özel durumlarda
    # 'pigpio' gibi farklı bir kütüphane kullanmak için gerekebilir.
    # Genellikle varsayılan ayarlar yeterlidir.
    from gpiozero.pins.pigpio import PiGPIOFactory
    from gpiozero import Device

    Device.pin_factory = PiGPIOFactory()
    print("Pin fabrikası 'pigpio' olarak ayarlandı.")
except ImportError:
    print("UYARI: 'pigpio' kütüphanesi bulunamadı. Varsayılan pin fabrikası kullanılacak.")
    print("Eğer servo titreme (jitter) yaparsa, 'sudo apt-get install pigpio' komutuyla kurun.")
except Exception as e:
    # Bu genellikle 'pigpiod' servisi çalışmadığında olur.
    # 'sudo systemctl start pigpiod' komutuyla başlatabilirsiniz.
    print(f"Pin fabrikası ayarlanırken bir hata oluştu: {e}")
    print("Lütfen 'sudo systemctl start pigpiod' komutunu çalıştırıp tekrar deneyin.")
    sys.exit(1)


# --- ANA TEST FONKSİYONU ---
def run_servo_test():
    """
    Belirtilen pindeki servoyu test eder.
    Servoyu minimum, orta ve maksimum pozisyonlarına sırayla getirir.
    """
    print(f"Servo Test Betiği Başlatıldı - GPIO Pini: {SERVO_PIN}")

    # Servo nesnesini oluştur
    # Not: min_pulse_width ve max_pulse_width değerleri, servonuzun
    # tam 0-180 derece dönmemesi durumunda ince ayar için kullanılabilir.
    # Çoğu SG90 için varsayılan değerler yeterlidir.
    # servo = Servo(SERVO_PIN, min_pulse_width=0.5/1000, max_pulse_width=2.5/1000)
    servo = Servo(SERVO_PIN)

    print("\nTest başlıyor... Döngüden çıkmak için CTRL+C'ye basın.")

    try:
        while True:
            # --- 1. Adım: Minimum Pozisyon (0 Derece) ---
            print("1. Pozisyon: Minimum (0 derece)")
            servo.min()
            sleep(2)  # 2 saniye bekle

            # --- 2. Adım: Orta Pozisyon (90 Derece) ---
            print("2. Pozisyon: Orta (90 derece)")
            servo.mid()
            sleep(2)  # 2 saniye bekle

            # --- 3. Adım: Maksimum Pozisyon (180 Derece) ---
            print("3. Pozisyon: Maksimum (180 derece)")
            servo.max()
            sleep(2)  # 2 saniye bekle

    except KeyboardInterrupt:
        print("\n\nTest kullanıcı tarafından durduruldu.")

    finally:
        # Program sonlandığında servoyu serbest bırak
        servo.detach()
        print("Servo serbest bırakıldı. Program sonlandırıldı.")


if __name__ == '__main__':
    # Donanım bağlantısı için son bir hatırlatma
    print("=" * 50)
    print("LÜTFEN DİKKAT:")
    print("1. Servonun HARİCİ bir 5V güç kaynağına bağlı olduğundan emin olun.")
    print("2. Raspberry Pi ve harici güç kaynağının TOPRAK (GND) hatlarının birleşik olduğundan emin olun.")
    print(f"3. Servonun SİNYAL kablosunun GPIO {SERVO_PIN} pinine bağlı olduğundan emin olun.")
    print("=" * 50)

    # Kullanıcıya devam etme şansı ver
    try:
        input("Hazırsanız başlamak için ENTER'a basın...")
    except KeyboardInterrupt:
        print("\nBaşlamadan çıkıldı.")
        sys.exit(0)

    # Test fonksiyonunu çalıştır
    run_servo_test()