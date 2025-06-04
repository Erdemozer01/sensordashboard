import time
import atexit
import sys

# Gerekli GPIO kütüphanelerini import et
try:
    from gpiozero import DistanceSensor, Buzzer, OutputDevice, LED
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
STATUS_LED_PIN = 27
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

# YENİ ve GÜNCELLENMİŞ Mod Ayarları
SWEEP_TARGET_ANGLE = 45  # Merkezin sağına ve soluna kaç derece döneceği (MUTLAK DEĞER)
ALGILAMA_ESIGI_CM = 20  # Bu mesafeden daha yakın nesneler uyarıyı tetikler
MOTOR_PAUSE_ON_DETECTION_S = 3.0  # Nesne algılandığında motorun duracağı süre (saniye)
CYCLE_END_PAUSE_S = 5.0  # Her tur sonunda merkezde beklenecek süre (saniye)

BUZZER_BIP_SURESI = 0.03
LED_BLINK_ON_SURESI = 0.5
LED_BLINK_OFF_SURESI = 0.5
LCD_TIME_UPDATE_INTERVAL = 1.0
# ==============================================================================

# --- Global Değişkenler ---
sensor, buzzer, lcd, status_led = None, None, None, None
in1_dev, in2_dev, in3_dev, in4_dev = None, None, None, None

current_motor_angle_global = 0.0
current_step_sequence_index = 0
DEG_PER_STEP = 360.0 / STEPS_PER_REVOLUTION
step_sequence = [[1, 0, 0, 0], [1, 1, 0, 0], [0, 1, 0, 0], [0, 1, 1, 0],
                 [0, 0, 1, 0], [0, 0, 1, 1], [0, 0, 0, 1], [1, 0, 0, 1]]

current_lcd_message_type = None
last_lcd_time_update = 0
led_is_blinking = False
init_hardware_called_successfully = False
object_alert_active = False


# ==============================================================================

# ==============================================================================
# --- Donanım ve Yardımcı Fonksiyonlar (Çoğu Aynı) ---
# (init_hardware, release_resources_on_exit, _set_step_pins,
#  _single_step_motor, _move_motor_steps, move_motor_to_absolute_angle,
#  kisa_uyari_bip, update_lcd_display fonksiyonları bir önceki cevaptaki gibi.
#  Sadece init_hardware içinde led_is_blinking = True satırını kontrol edeceğim.)
# ==============================================================================
def init_hardware():
    global sensor, buzzer, lcd, status_led, in1_dev, in2_dev, in3_dev, in4_dev, led_is_blinking, init_hardware_called_successfully
    print("Donanımlar başlatılıyor...")
    try:
        in1_dev, in2_dev, in3_dev, in4_dev = OutputDevice(IN1_GPIO_PIN), OutputDevice(IN2_GPIO_PIN), OutputDevice(
            IN3_GPIO_PIN), OutputDevice(IN4_GPIO_PIN)
        sensor = DistanceSensor(echo=ECHO_PIN, trigger=TRIG_PIN, max_distance=2.5, queue_len=5)
        buzzer = Buzzer(BUZZER_PIN);
        buzzer.off()
        status_led = LED(STATUS_LED_PIN)
        # Başlangıçta LED yanıp sönmeye başlasın ve durumu doğru set edilsin
        if not led_is_blinking:  # Eğer zaten yanıp sönmüyorsa başlat
            status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI, background=True)
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
        init_hardware_called_successfully = True
        return True
    except Exception as e:
        print(f"HATA: Donanım başlatılamadı! Detay: {e}")
        init_hardware_called_successfully = False
        return False


def release_resources_on_exit():
    print("\nProgram sonlandırılıyor, kaynaklar serbest bırakılıyor...")
    _set_step_pins(0, 0, 0, 0)
    if lcd:
        try:
            lcd.clear()
        except:
            pass
    if status_led:
        try:
            status_led.off()  # Önce blink'i durdurup sonra kapatmak daha iyi olabilir
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
    global current_step_sequence_index, current_motor_angle_global
    current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
        step_sequence)) % len(step_sequence)
    _set_step_pins(*step_sequence[current_step_sequence_index])
    current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
    # Açıyı normalize etmeye gerek yok, move_motor_to_absolute_angle bunu kendi içinde yapar
    time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def _move_motor_steps(num_steps, direction_positive):
    global current_step_sequence_index, current_motor_angle_global
    for _ in range(int(num_steps)):
        current_step_sequence_index = (current_step_sequence_index + (1 if direction_positive else -1) + len(
            step_sequence)) % len(step_sequence)
        _set_step_pins(*step_sequence[current_step_sequence_index])
        current_motor_angle_global += (DEG_PER_STEP * (1 if direction_positive else -1))
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY)


def move_motor_to_absolute_angle(target_angle_deg, speed_factor=1.0):
    global current_motor_angle_global

    angle_diff_raw = target_angle_deg - current_motor_angle_global
    angle_diff = angle_diff_raw

    # En kısa yolu bul
    if abs(angle_diff_raw) > 180:
        if angle_diff_raw > 0:
            angle_diff = angle_diff_raw - 360
        else:
            angle_diff = angle_diff_raw + 360

    num_steps = round(abs(angle_diff) / DEG_PER_STEP)
    if num_steps == 0:
        time.sleep(STEP_MOTOR_SETTLE_TIME / speed_factor)
        return

    direction_positive = (angle_diff > 0)

    # _move_motor_steps yerine _single_step_motor'u döngüde kullanalım,
    # böylece her adımda açıyı daha hassas takip ederiz.
    # Bu fonksiyon zaten _move_motor_steps'in yaptığı işi yapar.
    # Homing için daha yavaş hareket istenirse diye speed_factor eklendi.
    for _ in range(num_steps):
        _single_step_motor(direction_positive)  # Bu zaten current_motor_angle_global'i güncelliyor
        time.sleep(STEP_MOTOR_INTER_STEP_DELAY / speed_factor)  # Homing daha yavaş olabilir

    # Hedefe ulaştıktan sonra global açıyı tam olarak hedef açıya set et
    current_motor_angle_global = target_angle_deg
    time.sleep(STEP_MOTOR_SETTLE_TIME / speed_factor)


def kisa_uyari_bip(bip_suresi):
    if buzzer:
        buzzer.on();
        time.sleep(bip_suresi);
        buzzer.off()


def update_lcd_display(message_type):
    global current_lcd_message_type, lcd, last_lcd_time_update
    now = time.time()
    if message_type == current_lcd_message_type and message_type != "normal_time": return
    if message_type == "normal_time" and (
            now - last_lcd_time_update < LCD_TIME_UPDATE_INTERVAL) and current_lcd_message_type == "normal_time": return
    if not lcd: return
    try:
        lcd.clear()
        if message_type == "alert_greeting":
            lcd.write_string("Merhaba")
            lcd.cursor_pos = (1, 0);
            lcd.write_string("Dream Pi")
        elif message_type == "normal_time":
            lcd.write_string("Dream Pi")
            lcd.cursor_pos = (1, 0);
            lcd.write_string(time.strftime("%H:%M:%S"))
            last_lcd_time_update = now
        current_lcd_message_type = message_type
    except Exception as e:
        print(f"LCD Yazma Hatası: {e}");
        current_lcd_message_type = "error"


# ==============================================================================
# --- ANA ÇALIŞMA BLOĞU (YENİ TUR MANTIĞI VE DURAKSAMALARLA) ---
# ==============================================================================
if __name__ == "__main__":
    atexit.register(release_resources_on_exit)
    if not init_hardware():
        sys.exit(1)

    print("\n>>> Serbest Tarama Modu V5 Başlatıldı <<<")
    print(f"Tarama Açıları: -{SWEEP_TARGET_ANGLE}° ile +{SWEEP_TARGET_ANGLE}° arası")
    print(f"Algılama Eşiği: < {ALGILAMA_ESIGI_CM} cm")
    print(f"Nesne Algılandığında Duraklama: {MOTOR_PAUSE_ON_DETECTION_S} sn")
    print(f"Tur Sonu Bekleme: {CYCLE_END_PAUSE_S} sn")
    print("Durdurmak için Ctrl+C tuşlarına basın.")

    move_motor_to_absolute_angle(0)  # Başlangıçta motoru merkeze al
    update_lcd_display("normal_time")

    try:
        while True:
            # Bir turdaki hareket hedefleri (açı, hareket adı)
            # Bu silsile: 0 -> +45 -> -45 -> 0
            tur_hedefleri = [
                (SWEEP_TARGET_ANGLE, f"Merkezden +{SWEEP_TARGET_ANGLE}° yonune"),
                (-SWEEP_TARGET_ANGLE, f"+{SWEEP_TARGET_ANGLE}° yonunden -{SWEEP_TARGET_ANGLE}° yonune"),
                (0, f"-{SWEEP_TARGET_ANGLE}° yonunden Merkeze (0°)")
            ]

            for hedef_aci_tur, hareket_adi in tur_hedefleri:
                print(f"\n>> {hareket_adi} taranıyor...")

                # Hedef açıya ulaşana kadar adım adım git ve ölçüm yap
                # Yönü belirle
                angle_diff_for_direction = hedef_aci_tur - current_motor_angle_global
                if abs(angle_diff_for_direction) > 180:  # En kısa yolu bul
                    angle_diff_for_direction = angle_diff_for_direction - (
                        360 if angle_diff_for_direction > 0 else -360)

                direction_is_positive_leg = angle_diff_for_direction > 0

                while True:
                    # Hedefe ulaşıp ulaşmadığını kontrol et (küçük bir toleransla)
                    if abs(current_motor_angle_global - hedef_aci_tur) < DEG_PER_STEP:
                        current_motor_angle_global = hedef_aci_tur  # Tam hedef açıya sabitle
                        break

                        # Eğer hedefi geçtiyse (nadiren olur ama önlem)
                    if (direction_is_positive_leg and current_motor_angle_global > hedef_aci_tur) or \
                            (not direction_is_positive_leg and current_motor_angle_global < hedef_aci_tur):
                        # Hedefi biraz geçtiyse, bir sonraki turda düzelir veya homing'de.
                        # Daha hassas bir durdurma için buraya ince ayar eklenebilir.
                        # Şimdilik, tam hedefe sabitleyip çıkalım.
                        current_motor_angle_global = hedef_aci_tur
                        break

                    _single_step_motor(direction_is_positive_leg)

                    mesafe = sensor.distance * 100
                    is_object_currently_close = (mesafe < ALGILAMA_ESIGI_CM)

                    # GÜNCELLENMİŞ LED, BUZZER VE LCD KONTROL MANTIĞI
                    if is_object_currently_close:
                        if not object_alert_active:  # Nesne YENİ algılandı
                            print(f"   >>> UYARI: Nesne {mesafe:.1f} cm'de algılandı! MOTOR DURAKSATILIYOR... <<<")
                            kisa_uyari_bip(BUZZER_BIP_SURESI)
                            update_lcd_display("alert_greeting")
                            if status_led:
                                if led_is_blinking:
                                    status_led.off();
                                    time.sleep(0.01);
                                    status_led.on()
                                elif not status_led.is_lit:
                                    status_led.on()
                            led_is_blinking = False
                            object_alert_active = True

                            print(f"Motor {MOTOR_PAUSE_ON_DETECTION_S} saniye duraklatıldı.")
                            time.sleep(MOTOR_PAUSE_ON_DETECTION_S)  # Motoru duraklat
                            print("Harekete devam ediliyor...")

                    else:  # Nesne algılanmıyor
                        if object_alert_active:  # Nesne YENİ kayboldu
                            print("   <<< UYARI SONA ERDİ. >>>")
                            update_lcd_display("normal_time")
                            if status_led:
                                if not led_is_blinking:
                                    status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI,
                                                     background=True)
                            led_is_blinking = True
                            object_alert_active = False
                        else:
                            update_lcd_display("normal_time")
                            if status_led and not led_is_blinking:
                                status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI,
                                                 background=True)
                                led_is_blinking = True

                print(f"   {hareket_adi} tamamlandı. Mevcut Açı: {current_motor_angle_global:.1f}°")

            # Tur tamamlandı, merkezde bekle
            print(
                f"\n>>> Bir tur tamamlandı. Merkeze dönüldü ({current_motor_angle_global:.1f}°). {CYCLE_END_PAUSE_S} saniye bekleniyor...")
            # LCD'nin normal zamanda olduğundan emin ol
            update_lcd_display("normal_time")
            # LED'in yanıp söndüğünden emin ol
            if status_led and not led_is_blinking:
                status_led.blink(on_time=LED_BLINK_ON_SURESI, off_time=LED_BLINK_OFF_SURESI, background=True)
                led_is_blinking = True

            time.sleep(CYCLE_END_PAUSE_S)

    except KeyboardInterrupt:
        print("\nKullanıcı tarafından durduruluyor...")
    finally:
        print("Program sonlanıyor...")
        if init_hardware_called_successfully:
            print("Motor başlangıç pozisyonuna (0°) getiriliyor...")
            move_motor_to_absolute_angle(0)
        else:
            print("Donanım başlatılamadığı için motor homing atlanıyor, pinler sıfırlanacak.")
            _set_step_pins(0, 0, 0, 0)
        print("Çıkış işlemleri tamamlandı.")