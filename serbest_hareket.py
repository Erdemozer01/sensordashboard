import time
import atexit
import sys

# Gerekli GPIO kütüphanelerini import et
try:
    from gpiozero import DistanceSensor, Buzzer, OutputDevice
    from RPLCD.i2c import CharLCD  # EKLENDİ: LCD kütüphanesi
except ImportError:
    print("HATA: Gerekli kütüphaneler (gpiozero, RPLCD) bulunamadı. Lütfen yükleyin.")
    sys.exit(1)

# ==============================================================================
# --- KONTROL DEĞİŞKENİ ---
# ==============================================================================
MOTOR_BAGLI = True
# ==============================================================================

# ==============================================================================
# --- Pin Tanımlamaları ---
# ==============================================================================
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
BUZZER_PIN = 17

# EKLENDİ: LCD Ayarları
LCD_I2C_ADDRESS = 0x27
LCD_PORT_EXPANDER = 'PCF8574'
LCD_COLS = 16
LCD_ROWS = 2
I2C_PORT = 1
# ==============================================================================

# ==============================================================================
# --- Parametreler ---
# ==============================================================================
STEPS_PER_REVOLUTION = 4096
STEP_MOTOR_INTER_STEP_DELAY = 0.0015
STEP_MOTOR_SETTLE_TIME = 0.05

BEKLEME_SURESI_SN = 10
DONUS_ACISI = 90
MIN_ALGILAMA_MESAFESI = 10
MAX_ALGILAMA_MESAFESI = 20
BUZZER_BIP_SURESI = 0.05
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd = None, None, None  # lcd eklendi
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
current_motor_angle_global = 0.0
current_step_sequence_index = 0
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]


# ==============================================================================
# --- Donanım ve Motor Fonksiyonları (Güncellendi) ---
# ==============================================================================

def init_hardware():
    """Gerekli donanım bileşenlerini başlatır."""
    global sensor, buzzer, lcd, in1_dev, in2_dev, in3_dev, in4_dev  # lcd eklendi
    print("Donanımlar başlatılıyor...")
    try:
        if MOTOR_BAGLI:
            in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
                IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5)
        buzzer = Buzzer(BUZZER_PIN)
        buzzer.off()

        # GÜNCELLENDİ: LCD başlatma eklendi
        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT,
                          cols=LCD_COLS, rows=LCD_ROWS, auto_linebreaks=False)
            lcd.clear()
            print("✓ LCD başarıyla başlatıldı.")
        except Exception as e_lcd:
            print(f"UYARI: LCD başlatılamadı! Hata: {e_lcd}")
            lcd = None

        print("✓ Donanımlar başarıyla başlatıldı.")
        return True
    except Exception as e:
        print(f"HATA: Donanım başlatılamadı! Detay: {e}")
        return False


def release_resources_on_exit():
    """Program sonlandığında donanım pinlerini serbest bırakır."""
    print("\nProgram sonlandırılıyor, kaynaklar serbest bırakılıyor...")
    if MOTOR_BAGLI:
        _set_step_pins(0, 0, 0, 0)

    # GÜNCELLENDİ: LCD temizleme eklendi
    if lcd:
        try:
            lcd.clear()
        except:
            pass  # Çıkışta hata varsa önemseme

    for dev in [sensor, buzzer, lcd, in1_dev, in2_dev, in3_dev, in4_dev]:
        if dev and hasattr(dev, 'close'):
            dev.close()
    print("✓ Temizleme tamamlandı.")


def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _step_motor(num_steps, direction_positive):
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def move_motor_to_angle(target_angle_deg):
    """Motoru belirtilen açıya hareket ettirir."""
    global current_motor_angle_global
    if not MOTOR_BAGLI: return
    angle_diff = target_angle_deg - current_motor_angle_global
    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0: return
    direction_positive = (angle_diff > 0)
    _step_motor(num_steps, direction_positive)
    current_motor_angle_global += (num_steps * DEG_PER_STEP * (1 if direction_positive else -1))
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def kisa_bip(duration):
    """Buzzer'ı belirtilen süre kadar öttürür."""
    if buzzer:
        buzzer.on()
        time.sleep(duration)
        buzzer.off()


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU (Güncellendi) ---
# ==============================================================================

if __name__ == "__main__":
    atexit.register(release_resources_on_exit)

    if not init_hardware():
        sys.exit(1)

    print("\n>>> Serbest Hareket Modu Başlatıldı (LCD Destekli) <<<")
    print(f"Nesne algılama aralığı: {MIN_ALGILAMA_MESAFESI} cm - {MAX_ALGILAMA_MESAFESI} cm")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    try:
        move_motor_to_angle(0)

        while True:
            hareket_silsilesi = [
                ("Saga Donuluyor", DONUS_ACISI),
                ("Merkeze Donuluyor", 0),
                ("Sola Donuluyor", -DONUS_ACISI),
                ("Merkeze Donuluyor", 0)
            ]

            for hareket_adi, hedef_aci in hareket_silsilesi:
                print(f"\n>> Yon: {hareket_adi}...")
                move_motor_to_angle(hedef_aci)

                mesafe = sensor.distance * 100
                print(f"   Mesafe: {mesafe:.1f} cm")

                # --- GÜNCELLENEN MANTIK BURADA ---
                if MIN_ALGILAMA_MESAFESI <= mesafe <= MAX_ALGILAMA_MESAFESI:
                    # Nesne algılandığında yapılacaklar
                    print("   UYARI: Nesne algılandı!")
                    kisa_bip(BUZZER_BIP_SURESI)
                    if lcd:
                        try:
                            lcd.clear()
                            lcd.write_string("!!! Merhaba !!!")
                            lcd.cursor_pos = (1, 0)
                            lcd.write_string(f"Mesafe: {mesafe:.1f}cm")
                            # Merhaba mesajının okunması için kısa bir bekleme
                            time.sleep(1)
                        except Exception as e:
                            print(f"LCD Uyarı Yazma Hatası: {e}")
                else:
                    # Normal durumda yapılacaklar
                    if lcd:
                        try:
                            lcd.clear()
                            lcd.write_string(hareket_adi)
                            lcd.cursor_pos = (1, 0)
                            lcd.write_string(f"Mesafe: {mesafe:.1f}cm")
                        except Exception as e:
                            print(f"LCD Durum Yazma Hatası: {e}")

            print(f"\n>>> Döngü tamamlandı. {BEKLEME_SURESI_SN} saniye bekleniyor...")
            time.sleep(BEKLEME_SURESI_SN)

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"\nBEKLENMEDİK BİR HATA OLUŞTU: {e}")