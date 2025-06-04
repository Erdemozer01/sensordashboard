import time
import atexit
import sys

# Gerekli GPIO kütüphanelerini import et
try:
    from gpiozero import DistanceSensor, Buzzer, OutputDevice
    from RPLCD.i2c import CharLCD
except ImportError:
    print("HATA: Gerekli kütüphaneler (gpiozero, RPLCD) bulunamadı. Lütfen yükleyin.")
    sys.exit(1)

# ==============================================================================
# --- Pin Tanımlamaları ve Donanım Ayarları ---
# ==============================================================================
TRIG_PIN, ECHO_PIN = 23, 24
IN1_GPIO_PIN, IN2_GPIO_PIN, IN3_GPIO_PIN, IN4_GPIO_PIN = 6, 13, 19, 26
BUZZER_PIN = 17
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
STEP_MOTOR_SETTLE_TIME = 0.05  # Motor durduktan sonra ölçüm için bekleme

# Serbest hareket modu ayarları
BEKLEME_SURESI_SN_DONGU_SONU = 10  # Tüm hareket silsilesi bittikten sonra beklenecek süre
DONUS_ACISI = 90  # Sağa ve sola ne kadar döneceği (derece)
ALGILAMA_ESIGI_CM = 20  # Bu mesafeden daha yakın nesneler uyarıyı tetikler

# Buzzer ayarları
BUZZER_BIP_SURESI = 0.05  # Her bir bip'in süresi
BUZZER_BIP_ARASI_SURE = 0.05  # İki bip arasındaki bekleme süresi
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd = None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
current_motor_angle_global = 0.0
current_step_sequence_index = 0
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]
current_lcd_state = None  # LCD'nin titremesini önlemek için mevcut durumu tutar


# ==============================================================================
# --- Donanım ve Motor Fonksiyonları ---
# ==============================================================================

def init_hardware():
    global sensor, buzzer, lcd, in1_dev, in2_dev, in3_dev, in4_dev
    print("Donanımlar başlatılıyor...")
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=2)  # queue_len düşürüldü
        buzzer = Buzzer(BUZZER_PIN)
        buzzer.off()

        try:
            lcd = CharLCD(i2c_expander=LCD_PORT_EXPANDER, address=LCD_I2C_ADDRESS, port=I2C_PORT, cols=LCD_COLS,
                          rows=LCD_ROWS, auto_linebreaks=False)
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
    print("\nProgram sonlandırılıyor, kaynaklar serbest bırakılıyor...")
    _set_step_pins(0, 0, 0, 0)
    if lcd:
        try:
            lcd.clear()
        except:
            pass
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
    """Motoru belirtilen adım sayısı kadar hareket ettirir (çoklu adım)."""
    global current_step_sequence_index
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def move_motor_to_angle(target_angle_deg):
    """Motoru belirtilen açıya hareket ettirir ve durur."""
    global current_motor_angle_global

    # Mevcut mantık, göreceli harekete dayanıyor.
    # Hedefe olan farkı hesapla ve o kadar adım at.
    angle_diff = target_angle_deg - current_motor_angle_global

    # 360 derece dönüşlerde en kısa yolu bulmak için (isteğe bağlı, mevcut basit farkla çalışır)
    # if abs(angle_diff) > 180:
    #     angle_diff = angle_diff - (360 if angle_diff > 0 else -360)

    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0:
        time.sleep(STEP_MOTOR_SETTLE_TIME)  # Hedefteyse bile kısa bir bekleme
        return

    direction_positive = (angle_diff > 0)
    _step_motor(num_steps, direction_positive)
    current_motor_angle_global += (num_steps * DEG_PER_STEP * (1 if direction_positive else -1))
    # Normalize angle to be within -180 to 180 or 0 to 360 if needed,
    # but for this logic, cumulative angle is fine as long as target_angle_deg is within a reasonable range.
    # current_motor_angle_global = current_motor_angle_global % 360
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def cift_bip(bip_suresi, ara_sure):
    """Buzzer'ı iki kez kısa aralıklarla öttürür."""
    if buzzer:
        buzzer.on()
        time.sleep(bip_suresi)
        buzzer.off()
        time.sleep(ara_sure)
        buzzer.on()
        time.sleep(bip_suresi)
        buzzer.off()


def update_lcd(new_state):
    """LCD ekranı sadece durum değiştiğinde günceller."""
    global current_lcd_state
    if new_state == current_lcd_state or not lcd:
        return

    try:
        lcd.clear()
        if new_state == "alert":
            lcd.write_string("Dokunma bana!")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Nesne Cok Yakin")
        elif new_state == "normal":
            lcd.write_string("Merhaba benim")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Adim Dream Pi")
        current_lcd_state = new_state
    except Exception as e:
        print(f"LCD Yazma Hatası: {e}")
        current_lcd_state = "error"


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================

if __name__ == "__main__":
    atexit.register(release_resources_on_exit)
    if not init_hardware():
        sys.exit(1)

    print("\n>>> Serbest Hareket Modu V3 Başlatıldı (Dur-Bak & Çift Bip) <<<")
    print(f"Algılama Eşiği: < {ALGILAMA_ESIGI_CM} cm")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    update_lcd("normal")  # Başlangıçta LCD normal durumda
    move_motor_to_angle(0)  # Motoru merkeze al

    try:
        while True:
            # Önceki `serbest_hareket.py` (v1) gibi hareket silsilesi
            hareket_silsilesi = [
                (f"Saga ({DONUS_ACISI} gr.)", DONUS_ACISI),
                ("Merkeze", 0),
                (f"Sola (-{DONUS_ACISI} gr.)", -DONUS_ACISI),
                ("Merkeze", 0)
            ]

            for hareket_adi, hedef_aci in hareket_silsilesi:
                print(f"\n>> Hedef: {hareket_adi} ({hedef_aci}°)")
                move_motor_to_angle(hedef_aci)  # Motor hedefe gider ve durur

                # Durduktan sonra ölçüm yap
                mesafe = sensor.distance * 100
                print(f"   Mesafe (Aci {current_motor_angle_global:.1f}°): {mesafe:.1f} cm")

                if mesafe < ALGILAMA_ESIGI_CM:
                    print("   UYARI: Nesne algılandı!")
                    update_lcd("alert")
                    cift_bip(BUZZER_BIP_SURESI, BUZZER_BIP_ARASI_SURE)
                else:
                    update_lcd("normal")

            # Tüm hareket silsilesi bittikten sonra bekle
            print(f"\n>>> Döngü tamamlandı. {BEKLEME_SURESI_SN_DONGU_SONU} saniye bekleniyor...")
            time.sleep(BEKLEME_SURESI_SN_DONGU_SONU)

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"\nBEKLENMEDİK BİR HATA OLUŞTU: {e}")