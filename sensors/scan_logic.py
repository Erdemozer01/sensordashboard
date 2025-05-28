# sensors/scan_logic.py
import time
import os
from .config import (SCAN_START_ANGLE, SCAN_END_ANGLE, SCAN_STEP_ANGLE,
                     OBJECT_THRESHOLD_CM, YELLOW_LED_THRESHOLD_CM,
                     TERMINATION_DISTANCE_CM, LOOP_TARGET_INTERVAL_S)
from .hardware import (sensor, red_led, green_led, yellow_led, servo,
                       set_servo_angle, update_lcd_display)
from .database import current_scan_id, save_scan_point, update_scan_status


def perform_scan():
    """Tam bir tarama döngüsü gerçekleştirir."""
    ölçüm_tamponu_hız_için = []
    ornek_sayaci = 0
    script_exit_status = 'interrupted_unexpectedly'

    print(f"[{os.getpid()}] Servo ile 2D Tarama Başlıyor...")

    update_lcd_display(f"ScanID:{current_scan_id} Basladi", "Aci: -- Mes: --")

    try:
        print(f"[{os.getpid()}] Servo başlangıç açısına ({SCAN_START_ANGLE}°) ayarlanıyor...")
        set_servo_angle(SCAN_START_ANGLE)
        time.sleep(1.0)  # Servonun başlangıç pozisyonuna gelmesi için

        for angle_deg in range(SCAN_START_ANGLE, SCAN_END_ANGLE + SCAN_STEP_ANGLE, SCAN_STEP_ANGLE):
            loop_iteration_start_time = time.time()

            set_servo_angle(angle_deg)

            current_timestamp = time.time()
            distance_m = sensor.distance
            distance_cm = distance_m * 100

            hiz_cm_s = 0.0
            if ölçüm_tamponu_hız_için:
                son_veri_noktasi = ölçüm_tamponu_hız_için[-1]
                delta_mesafe = distance_cm - son_veri_noktasi['mesafe_cm']
                delta_zaman = current_timestamp - son_veri_noktasi['zaman_s']
                if delta_zaman > 0.001:
                    hiz_cm_s = delta_mesafe / delta_zaman

            update_lcd_display(
                f"Aci:{angle_deg:<3} ID:{current_scan_id:<3}",
                f"M:{distance_cm:5.1f} H:{hiz_cm_s:4.1f}"
            )

            if distance_cm > YELLOW_LED_THRESHOLD_CM:
                yellow_led.on()
            else:
                yellow_led.toggle()

            max_distance_cm = sensor.max_distance * 100
            is_reading_valid = (distance_cm > 0.0) and (distance_cm < max_distance_cm)

            if is_reading_valid:
                if distance_cm <= OBJECT_THRESHOLD_CM:
                    red_led.on()
                    green_led.off()
                else:
                    green_led.on()
                    red_led.off()
            else:
                red_led.off()
                green_led.off()

            if distance_cm < TERMINATION_DISTANCE_CM:
                print(f"[{os.getpid()}] DİKKAT: NESNE ÇOK YAKIN ({distance_cm:.2f}cm)! Tarama durduruluyor.")
                update_lcd_display("COK YAKIN! DUR!", f"{distance_cm:.1f} cm")
                red_led.blink(on_time=0.1, off_time=0.1, n=5, background=False)
                time.sleep(4.0)
                script_exit_status = 'terminated_close_object'
                break

            save_scan_point(angle_deg, distance_cm, hiz_cm_s)

            ölçüm_tamponu_hız_için = [{'mesafe_cm': distance_cm, 'zaman_s': current_timestamp}]
            ornek_sayaci += 1

            loop_processing_time = time.time() - loop_iteration_start_time
            sleep_duration = max(0, LOOP_TARGET_INTERVAL_S - loop_processing_time)
            if sleep_duration > 0 and (angle_deg < SCAN_END_ANGLE):
                time.sleep(sleep_duration)

        else:  # `for` döngüsü `break` olmadan tamamlanırsa
            script_exit_status = 'Tamamlandi'
            print(f"[{os.getpid()}] Tarama normal şekilde tamamlandı.")
            update_lcd_display("Tarama Tamamlandi", f"ID:{current_scan_id}")

    except KeyboardInterrupt:
        print(f"\n[{os.getpid()}] Tarama kullanıcı tarafından (Ctrl+C) sonlandırıldı.")
        script_exit_status = 'interrupted_ctrl_c'
        update_lcd_display("DURDURULDU (C)", "")
    except Exception as e:
        print(f"[{os.getpid()}] Tarama sırasında ana döngüde beklenmedik bir hata: {e}")
        script_exit_status = 'error_in_loop'
        update_lcd_display("HATA OLUSTU!", "")

    return script_exit_status