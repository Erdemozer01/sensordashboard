# stepper_uln2003_test.py
from gpiozero import OutputDevice
import time

# --- Pin Tanımlamaları (Kendi bağlantılarınıza göre güncelleyin) ---
# ULN2003 Sürücü Kartının IN pinlerine bağlanan Raspberry Pi GPIO pinleri
IN1_PIN = 5  # Örnek: GPIO5
IN2_PIN = 6  # Örnek: GPIO6
IN3_PIN = 13  # Örnek: GPIO13
IN4_PIN = 19  # Örnek: GPIO19

# Step motor için adım sıralaması (Half-step için 8 adım)
# Bu sıra 28BYJ-48 motorunun kablo renklerine ve sürücüye bağlanışına göre değişebilir.
# Yaygın bir sıralama:
# Step | IN1 | IN2 | IN3 | IN4
# ---------------------------
# 1    | 1   | 0   | 0   | 0
# 2    | 1   | 1   | 0   | 0
# 3    | 0   | 1   | 0   | 0
# 4    | 0   | 1   | 1   | 0
# 5    | 0   | 0   | 1   | 0
# 6    | 0   | 0   | 1   | 1
# 7    | 0   | 0   | 0   | 1
# 8    | 1   | 0   | 0   | 1
# Farklı bir kablolama için bu sıra (veya pinlerin sırası) değişebilir.
# Genellikle mavi-pembe-sarı-turuncu veya benzeri bir renk sırası vardır motor kablolarında.
# Sürücü kartındaki LED'ler yanıyorsa, doğru sırayı bulmak için deneme yapabilirsiniz.

# Half-step sequence
# Bu sekans, motorunuzun kablo bağlantı sırasına göre ayarlanmalıdır.
# Genellikle 28BYJ-48 motorlar için (ve ULN2003 üzerindeki LED'lerin sırayla yandığı)
# doğru sekanslardan biri budur.
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

# Full-step sequence (daha az adım, daha az hassas ama daha fazla tork)
# step_sequence = [
#  [1,0,1,0],
#  [0,1,1,0],
#  [0,1,0,1],
#  [1,0,0,1]
# ]


# --- GPIO Pinlerini OutputDevice olarak başlatma ---
motor_pins = [
    OutputDevice(IN1_PIN),
    OutputDevice(IN2_PIN),
    OutputDevice(IN3_PIN),
    OutputDevice(IN4_PIN)
]


def cleanup_pins():
    """Tüm motor pinlerini kapatır."""
    for pin in motor_pins:
        pin.off()
        pin.close()
    print("Motor pinleri temizlendi.")


def motor_step(step_idx):
    """Motoru verilen adıma göre sürer."""
    for i in range(4):
        if step_sequence[step_idx][i] == 1:
            motor_pins[i].on()
        else:
            motor_pins[i].off()


# --- Ana Test Döngüsü ---
if __name__ == "__main__":
    print("Step Motor ULN2003 Testi Başlıyor...")
    print(f"Kullanılan GPIO Pinleri: IN1={IN1_PIN}, IN2={IN2_PIN}, IN3={IN3_PIN}, IN4={IN4_PIN}")

    # 28BYJ-48 motoru genellikle bir devir için (half-step modunda) 4096 adım atar.
    # (Dişli oranı ~64, motorun kendi adımı 32, half-step 64 -> 64*64 = 4096)
    # Full-step modunda ise 2048 adım.
    # Bizim `step_sequence` 8 adımlık bir döngü. 4096 / 8 = 512 tam döngü bir devir eder.

    steps_per_revolution_half_step = 4096
    # Adım başına bekleme süresi (saniye). Bu değeri azaltarak hızı artırabilirsiniz,
    # ama çok azaltırsanız motor adım kaçırabilir.
    step_delay = 0.001  # 1 milisaniye, hızlı bir dönüş için. Daha yavaş için 0.002 veya 0.003 deneyin.

    current_step = 0

    try:
        print("Motor saat yönünde bir devir dönecek...")
        # Bir tam devir için (half-step)
        for _ in range(steps_per_revolution_half_step):
            motor_step(current_step % len(step_sequence))
            current_step += 1
            time.sleep(step_delay)

        cleanup_pins()  # Arada pinleri serbest bırakmak iyi olabilir
        time.sleep(1)
        print("\nMotor saat yönünün tersine bir devir dönecek...")
        # Saat yönünün tersi için sekansı tersten uygula veya current_step'i azalt
        for _ in range(steps_per_revolution_half_step):
            motor_step(current_step % len(step_sequence))  # Sekansı tersten almak için current_step'i azalt
            current_step -= 1
            if current_step < 0:  # Negatife düşerse başa sar
                current_step = len(step_sequence) - 1
            time.sleep(step_delay)

        print("\nTest tamamlandı!")

    except KeyboardInterrupt:
        print("\nTest kullanıcı tarafından durduruldu.")
    except Exception as e:
        print(f"Test sırasında bir hata oluştu: {e}")
    finally:
        cleanup_pins()
        print("Program sonlandı.")