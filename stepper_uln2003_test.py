# stepper_uln2003_test.py (Düzeltilmiş)
from gpiozero import OutputDevice
import time


IN1_PIN = 5
IN2_PIN = 6
IN3_PIN = 13
IN4_PIN = 19

# Half-step sequence
step_sequence = [
    [1, 0, 0, 0],
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 1, 1, 0],
    [0, 0, 1, 0],
    [0, 0, 1, 1],
    [0, 0, 0, 1],
    [1, 0, 0, 1]
]

motor_pins = []


def initialize_motor_pins():
    """Motor pinlerini başlatır."""
    global motor_pins
    # Önceki pinleri temizle (eğer varsa ve açıksa)
    for pin_obj in motor_pins:
        if hasattr(pin_obj, 'close') and not pin_obj.closed:
            pin_obj.close()
    motor_pins = [
        OutputDevice(IN1_PIN),
        OutputDevice(IN2_PIN),
        OutputDevice(IN3_PIN),
        OutputDevice(IN4_PIN)
    ]
    print("Motor pinleri başlatıldı.")


def cleanup_pins():
    """Tüm motor pinlerini kapatır."""
    global motor_pins
    print("Motor pinleri temizleniyor...")
    for pin_obj in motor_pins:
        try:
            if not pin_obj.closed:  # Pin hala açıksa kapat
                pin_obj.off()
                pin_obj.close()
        except Exception as e:
            print(f"Pin temizlenirken hata ({pin_obj.pin if hasattr(pin_obj, 'pin') else 'bilinmiyor'}): {e}")
    motor_pins = []  # Listeyi temizle
    print("Motor pinleri temizlendi.")


def motor_step(step_idx):
    """Motoru verilen adıma göre sürer."""
    global motor_pins
    if not motor_pins:  # Pinler başlatılmamışsa bir şey yapma
        print("Hata: Motor pinleri başlatılmamış!")
        return

    for i in range(4):
        try:
            if not motor_pins[i].closed:  # Sadece açık pinlere yaz
                if step_sequence[step_idx][i] == 1:
                    motor_pins[i].on()
                else:
                    motor_pins[i].off()
            # else:
            #     print(f"Uyarı: Pin {motor_pins[i].pin} kapalı, yazma atlandı.")
        except Exception as e:
            print(f"motor_step içinde pin {i} yazılırken hata: {e}")


# --- Ana Test Döngüsü ---
if __name__ == "__main__":
    print("Step Motor ULN2003 Testi Başlıyor...")
    print(f"Kullanılan GPIO Pinleri: IN1={IN1_PIN}, IN2={IN2_PIN}, IN3={IN3_PIN}, IN4={IN4_PIN}")

    initialize_motor_pins()  # Pinleri burada başlat

    steps_per_revolution_half_step = 4096
    step_delay = 0.0015  # Biraz yavaşlatalım, 0.001 çok hızlı olabilir

    current_step_index = 0  # Sekans içindeki adım indeksi

    try:
        print("\nMotor saat yönünde bir devir dönecek...")
        for _ in range(steps_per_revolution_half_step):
            motor_step(current_step_index)
            current_step_index = (current_step_index + 1) % len(step_sequence)  # Sekans içinde dön
            time.sleep(step_delay)

        print("Saat yönünde dönüş tamamlandı.")
        time.sleep(1)  # İki dönüş arasında kısa bir bekleme

        print("\nMotor saat yönünün tersine bir devir dönecek...")
        for _ in range(steps_per_revolution_half_step):
            # current_step_index'i azaltarak ters yönde git
            current_step_index = (current_step_index - 1 + len(step_sequence)) % len(step_sequence)
            motor_step(current_step_index)
            time.sleep(step_delay)

        print("\nTest tamamlandı!")

    except KeyboardInterrupt:
        print("\nTest kullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"Test sırasında bir hata oluştu: {e}")
        # Hata oluştuğunda traceback'i görmek için:
        # import traceback
        # traceback.print_exc()
    finally:
        cleanup_pins()  # Pinleri SADECE en sonda temizle
        print("Program sonlandı.")
