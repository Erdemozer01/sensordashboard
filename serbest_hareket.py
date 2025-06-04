import time
import atexit
import sys

# Gerekli GPIO kütüphanelerini import et
try:
    from gpiozero import DistanceSensor, Buzzer, OutputDevice, LED  # LED eklendi
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
STATUS_LED_PIN = 27  # EKLENDİ: LED için pin numarası
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
STEP_MOTOR_INTER_STEP_DELAY = 0.0015  # Motorun her adımı arasındaki süre
STEP_MOTOR_SETTLE_TIME = 0.05  # Homing için motor durduktan sonra bekleme

# Yeni mod ayarları
SWEEP_ANGLE_MAX = 90  # Merkezin sağına ve soluna kaç derece döneceği
ALGILAMA_ESIGI_CM = 20  # Bu mesafeden daha yakın nesneler uyarıyı tetikler
BUZZER_BIP_SURESI = 0.05
BUZZER_BIP_ARASI_SURE = 0.05
LED_BLINK_ON_SURESI = 0.5
LED_BLINK_OFF_SURESI = 0.5
LCD_TIME_UPDATE_INTERVAL = 1.0  # Saniye cinsinden LCD zaman güncelleme aralığı
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd, status_led = None, None, None, None  # status_led eklendi
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None

current_motor_angle_global = 0.0  # Motorun mevcut açısını takip etmek için
current_step_sequence_index = 0
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]

current_lcd_message_type = None  # LCD'nin mevcut mesaj tipini tutar ("normal_time", "alert_greeting")
last_lcd_time_update = 0  # LCD zamanının en son ne zaman güncellendiğini tutar
led_is_blinking = False  # LED'in yanıp sönme durumunu takip eder
init_hardware_called_successfully = False

# ==============================================================================

# ==============================================================================
# --- Donanım ve Yardımcı Fonksiyonlar ---
# ==============================================================================

def init_hardware():
    global sensor, buzzer, lcd, status_led, in1_dev, in2_dev, in3_dev, in4_dev, led_is_blinking
    print("Donanımlar başlatılıyor...")
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=5)
        buzzer = Buzzer(BUZZER_PIN);
        buzzer.off()
        status_led = LED(STATUS_LED_PIN)  # EKLENDİ: LED başlatma
        status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI,
                         background=True)  # Başlangıçta yanıp sönsün
        led_is_blinking = True

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
    _set_step_pins(0, 0, 0, 0)  # Motoru durdur
    if lcd:
        try:
            lcd.clear()
        except:
            pass
    if status_led:
        try:
            status_led.off()  # LED'i kapat
        except:
            pass

    for dev in [sensor, buzzer, lcd, status_led, in1_dev, in2_dev, in3_dev, in4_dev]:
        if dev and hasattr(dev, 'close'):
            try:
                dev.close()
            except Exception:
                pass
    print("✓ Temizleme tamamlandı.")


def _set_step_pins(s1, s2, s3, s4):
    if in1_dev: in1_dev.value = bool(s1)
    if in2_dev: in2_dev.value = bool(s2)
    if in3_dev: in3_dev.value = bool(s3)
    if in4_dev: in4_dev.value = bool(s4)


def _single_step_motor(direction_positive):
    """Motoru bir adım hareket ettirir ve global açıyı günceller."""
    global current_step_sequence_index, current_motor_angle_global
    current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
        step_sequence)) % len(step_sequence)
    _set_step_pins(*step_sequence[current_step_sequence_index])
    current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
    # Açıyı -180 ile +180 arasında tutmak için (isteğe bağlı, homing için faydalı olabilir)
    if current_motor_angle_global > 180: current_motor_angle_global -= 360
    if current_motor_angle_global < -180: current_motor_angle_global += 360
    time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def _move_motor_steps(num_steps, direction_positive):
    """Motoru belirtilen adım sayısı kadar hareket ettirir (çoklu adım)."""
    global current_step_sequence_index, current_motor_angle_global
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)
    # Normalize
    if current_motor_angle_global > 180: current_motor_angle_global -= 360
    if current_motor_angle_global < -180: current_motor_angle_global += 360


def move_motor_to_absolute_angle(target_angle_deg):
    """Motoru belirtilen mutlak açıya hareket ettirir (homing için)."""
    global current_motor_angle_global

    # Hedef açıya en kısa yoldan gitmek için farkı hesapla
    angle_diff = target_angle_deg - current_motor_angle_global
    if angle_diff > 180:
        angle_diff -= 360
    elif angle_diff < -180:
        angle_diff += 360

    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0:
        time.sleep(STEP_MOTOR_SETTLE_TIME)
        return

    direction_positive = (angle_diff > 0)
    print(f"Homing: {num_steps} adım, yön: {'pozitif' if direction_positive else 'negatif'}")
    _move_motor_steps(num_steps, direction_positive)  # _move_motor_steps kullanıldı
    # current_motor_angle_global = target_angle_deg # Doğrudan hedef açıya ayarla
    time.sleep(STEP_MOTOR_SETTLE_TIME)


def cift_bip(bip_suresi, ara_sure):
    if buzzer:
        buzzer.on();
        time.sleep(bip_suresi);
        buzzer.off()
        time.sleep(ara_sure)
        buzzer.on();
        time.sleep(bip_suresi);
        buzzer.off()


def update_lcd_display(message_type):
    global current_lcd_message_type, lcd, last_lcd_time_update
    now = time.time()

    # Sadece mesaj tipi değiştiyse veya zaman güncellemesi gerekiyorsa yaz
    if message_type == current_lcd_message_type and message_type != "normal_time":
        return
    if message_type == "normal_time" and (
            now - last_lcd_time_update < LCD_TIME_UPDATE_INTERVAL) and current_lcd_message_type == "normal_time":
        return

    if not lcd: return

    try:
        lcd.clear()
        if message_type == "alert_greeting":
            lcd.write_string("Merhaba")
            lcd.cursor_pos = (1, 0)
            lcd.write_string("Dream Pi")
        elif message_type == "normal_time":
            lcd.write_string("Dream Pi")
            lcd.cursor_pos = (1, 0)
            lcd.write_string(time.strftime("%H:%M:%S"))
            last_lcd_time_update = now

        current_lcd_message_type = message_type
    except Exception as e:
        print(f"LCD Yazma Hatası: {e}")
        current_lcd_message_type = "error"  # Hata durumuna geç


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU ---
# ==============================================================================
if __name__ == "__main__":
    atexit.register(release_resources_on_exit)
    if not init_hardware():
        sys.exit(1)

    print("\n>>> Sürekli Tarama Modu V4 Başlatıldı <<<")
    print(f"Algılama Eşiği: < {ALGILAMA_ESIGI_CM} cm")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    update_lcd_display("normal_time")  # Başlangıçta LCD normal durumda

    steps_for_sweep = int(SWEEP_ANGLE_MAX * (STEPS_PER_REVOLUTION / 360.0))
    current_direction_positive = True  # Başlangıçta sağa doğru

    try:
        while True:
            # Motoru bir adım hareket ettir
            _single_step_motor(current_direction_positive)

            # Mevcut açıya göre yön değiştirme mantığı
            # Sağa doğru maksimuma ulaştıysa sola dön
            if current_direction_positive and current_motor_angle_global >= SWEEP_ANGLE_MAX:
                print(f"Sağ limit ({SWEEP_ANGLE_MAX}°) ulaşıldı, sola dönülüyor...")
                current_direction_positive = False
            # Sola doğru maksimuma ulaştıysa sağa dön
            elif not current_direction_positive and current_motor_angle_global <= -SWEEP_ANGLE_MAX:
                print(f"Sol limit (-{SWEEP_ANGLE_MAX}°) ulaşıldı, sağa dönülüyor...")
                current_direction_positive = True

            # Mesafe ölçümü
            mesafe = sensor.distance * 100
            # print(f"Açı: {current_motor_angle_global:.1f}°, Mesafe: {mesafe:.1f} cm") # Detaylı loglama

            object_detected = (mesafe < ALGILAMA_ESIGI_CM)

            # LED Kontrolü
            if status_led:
                if object_detected:
                    if led_is_blinking:  # Eğer yanıp sönüyorsa, önce kapatıp sonra sürekli yak
                        status_led.off()
                        time.sleep(0.01)  # Blink'in kapanması için kısa bir bekleme
                        status_led.on()
                        led_is_blinking = False
                else:  # Nesne yoksa
                    if not led_is_blinking:  # Eğer sürekli yanıyorsa, yanıp sönmeye başla
                        status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI, background=True)
                        led_is_blinking = True

            # Buzzer ve LCD Kontrolü
            if object_detected:
                cift_bip(BUZZER_BIP_SURESI, BUZZER_BIP_ARASI_SURE)
                update_lcd_display("alert_greeting")
            else:
                update_lcd_display("normal_time")

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruluyor...")
    finally:
        # Her durumda (normal çıkış veya hata) motoru başa al ve kaynakları serbest bırak
        print("Motor başlangıç pozisyonuna (0°) getiriliyor...")
        if 'init_hardware' in locals() and init_hardware_called_successfully:  # Sadece donanım başarıyla başlatıldıysa
            move_motor_to_absolute_angle(0)
        else:  # Eğer init_hardware çağrılmadıysa veya başarısız olduysa, en azından pinleri sıfırla
            _set_step_pins(0, 0, 0, 0)
        # release_resources_on_exit() atexit ile zaten çağrılacak