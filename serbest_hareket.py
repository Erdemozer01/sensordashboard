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

# Yeni mod ayarları
SWEEP_ANGLE = 90  # Merkezin sağına ve soluna kaç derece döneceği
ALGILAMA_ESIGI_CM = 20  # Bu mesafeden daha yakın nesneler uarıyı tetikler
BUZZER_BIP_SURESI = 0.1
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd = None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None
current_step_sequence_index = 0
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]
current_lcd_state = None  # LCD'nin titremesini önlemek için mevcut durumu tutar


# ==============================================================================
# --- Donanım ve Yardımcı Fonksiyonlar ---
# ==============================================================================

def init_hardware():
    """Gerekli donanım bileşenlerini başlatır."""
    global sensor, buzzer, lcd, in1_dev, in2_dev, in3_dev, in4_dev
    print("Donanımlar başlatılıyor...")
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=5)
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
    """Program sonlandığında donanım pinlerini serbest bırakır."""
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


def _step_motor(direction_positive):
    """Motoru bir adım hareket ettirir."""
    global current_step_sequence_index
    current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
        step_sequence)) % len(step_sequence)
    _set_step_pins(*step_sequence[current_step_sequence_index])
    time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def kisa_bip(duration):
    """Buzzer'ı belirtilen süre kadar öttürür."""
    if buzzer:
        buzzer.on()
        time.sleep(duration)
        buzzer.off()


def update_lcd(new_state):
    """LCD ekranı sadece durum değiştiğinde günceller."""
    global current_lcd_state
    if new_state == current_lcd_state or not lcd:
        return  # Durum aynıysa veya LCD yoksa hiçbir şey yapma

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
        current_lcd_state = new_state  # Yeni durumu kaydet
    except Exception as e:
        print(f"LCD Yazma Hatası: {e}")
        current_lcd_state = "error"  # Hata durumuna geç


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================

if __name__ == "__main__":
    atexit.register(release_resources_on_exit)
    if not init_hardware():
        sys.exit(1)

    print("\n>>> Sürekli Tarama Modu Başlatıldı <<<")
    print(f"Algılama Eşiği: < {ALGILAMA_ESIGI_CM} cm")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    # Başlangıçta LCD ekranını normal duruma getir
    update_lcd("normal")

    # Motorun bir tam turdaki adım sayısının 360'a bölümüyle bir derecelik adımı bul
    steps_per_degree = STEPS_PER_REVOLUTION / 360.0
    # Salınım açısı için gereken adım sayısı
    sweep_steps = int(SWEEP_ANGLE * steps_per_degree)

    try:
        while True:
            # Sağa doğru salınım
            print(f"--> Sağa doğru taranıyor (+{SWEEP_ANGLE}°)...")
            for _ in range(sweep_steps):
                _step_motor(direction_positive=True)
                mesafe = sensor.distance * 100
                if mesafe < ALGILAMA_ESIGI_CM:
                    update_lcd("alert")
                    kisa_bip(BUZZER_BIP_SURESI)
                else:
                    update_lcd("normal")

            # Sola doğru salınım
            print(f"<-- Sola doğru taranıyor (-{SWEEP_ANGLE}°)...")
            for _ in range(sweep_steps):
                _step_motor(direction_positive=False)
                mesafe = sensor.distance * 100
                if mesafe < ALGILAMA_ESIGI_CM:
                    update_lcd("alert")
                    kisa_bip(BUZZER_BIP_SURESI)
                else:
                    update_lcd("normal")

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"\nBEKLENMEDİK BİR HATA OLUŞTU: {e}")