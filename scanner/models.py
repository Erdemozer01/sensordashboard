# scanner/models.py

from django.db import models
from django.utils import timezone

class Scan(models.Model):
    """
    Her bir tarama işleminin genel bilgilerini ve sonuçlarını tutar.
    Mevcut 'servo_scans' tablosunun yerine geçer.
    """
    class Status(models.TextChoices):
        RUNNING = 'running', 'Çalışıyor'
        COMPLETED = 'completed_analysis', 'Tamamlandı (Analiz Edildi)'
        INSUFFICIENT_POINTS = 'completed_insufficient_points', 'Tamamlandı (Yetersiz Nokta)'
        INTERRUPTED = 'interrupted_ctrl_c', 'Durduruldu (Ctrl+C)'
        ERROR = 'error_in_loop', 'Hata Oluştu'

    # Temel Bilgiler
    start_time = models.DateTimeField(default=timezone.now, verbose_name="Başlangıç Zamanı")
    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.RUNNING,
        verbose_name="Tarama Durumu"
    )

    # Tarama Ayarları
    start_angle_setting = models.FloatField(verbose_name="Ayar: Başlangıç Açısı")
    end_angle_setting = models.FloatField(verbose_name="Ayar: Bitiş Açısı")
    step_angle_setting = models.FloatField(verbose_name="Ayar: Adım Açısı")
    buzzer_distance_setting = models.IntegerField(verbose_name="Ayar: Uyarı Mesafesi (cm)")
    invert_motor_direction_setting = models.BooleanField(default=False, verbose_name="Ayar: Motor Yönü Ters")

    # Analiz Sonuçları
    calculated_area_cm2 = models.FloatField(null=True, blank=True, verbose_name="Hesaplanan Alan (cm²)")
    perimeter_cm = models.FloatField(null=True, blank=True, verbose_name="Çevre (cm)")
    max_width_cm = models.FloatField(null=True, blank=True, verbose_name="Maksimum Genişlik (cm)")
    max_depth_cm = models.FloatField(null=True, blank=True, verbose_name="Maksimum Derinlik (cm)")

    # Yapay Zeka Yorumu
    ai_commentary = models.TextField(blank=True, null=True, verbose_name="Yapay Zeka Yorumu")

    class Meta:
        verbose_name = "Tarama Kaydı"
        verbose_name_plural = "Tarama Kayıtları"
        ordering = ['-start_time'] # En yeniden eskiye sırala

    def __str__(self):
        return f"Tarama #{self.id} - {self.start_time.strftime('%d-%m-%Y %H:%M')}"


class ScanPoint(models.Model):
    """
    Bir tarama içindeki her bir ölçüm noktasını temsil eder.
    Mevcut 'scan_points' tablosunun yerine geçer.
    """
    scan = models.ForeignKey(
        Scan,
        on_delete=models.CASCADE,
        related_name='points',
        verbose_name="Ait Olduğu Tarama"
    )
    derece = models.FloatField(verbose_name="Açı (°)")
    mesafe_cm = models.FloatField(verbose_name="Mesafe (cm)")
    hiz_cm_s = models.FloatField(default=0, verbose_name="Hız (cm/s)")
    timestamp = models.DateTimeField(verbose_name="Zaman Damgası")
    x_cm = models.FloatField(verbose_name="X Koordinatı (cm)")
    y_cm = models.FloatField(verbose_name="Y Koordinatı (cm)")

    class Meta:
        verbose_name = "Tarama Noktası"
        verbose_name_plural = "Tarama Noktaları"
        ordering = ['timestamp']

    def __str__(self):
        return f"Nokta: {self.derece}° - {self.mesafe_cm:.1f} cm"