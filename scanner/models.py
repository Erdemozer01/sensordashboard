# scanner/models.py

from django.db import models
from django.utils import timezone


class Scan(models.Model):
    class Status(models.TextChoices):
        RUNNING = 'RUNNING', 'Çalışıyor'
        COMPLETED = 'COMPLETED', 'Tamamlandı'
        ERROR = 'ERROR', 'Hata'
        INTERRUPTED = 'INTERRUPTED', 'Kesildi'
        INSUFFICIENT_POINTS = 'INSUFFICIENT_POINTS', 'Yetersiz Nokta'

    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)

    start_angle_setting = models.FloatField(default=0.0)
    end_angle_setting = models.FloatField(default=270.0)
    step_angle_setting = models.FloatField(default=10.0)
    buzzer_distance_setting = models.IntegerField(default=10)
    invert_motor_direction_setting = models.BooleanField(default=False)

    calculated_area_cm2 = models.FloatField(null=True, blank=True)
    perimeter_cm = models.FloatField(null=True, blank=True)
    max_width_cm = models.FloatField(null=True, blank=True)
    max_depth_cm = models.FloatField(null=True, blank=True)

    ai_commentary = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.pk:
            self.start_time = timezone.now()
        if self.status != self.Status.RUNNING:
            self.end_time = timezone.now()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Scan #{self.id} - {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}"


class ScanPoint(models.Model):
    scan = models.ForeignKey(Scan, related_name='points', on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=timezone.now)

    derece = models.FloatField(help_text="Step motorun yatay açısı (pan)")
    mesafe_cm = models.FloatField(help_text="Ana sensörün mesafesi (cm)")

    # Servo ve ikinci sensör için yeni alanlar
    dikey_aci = models.FloatField(default=0.0, help_text="Servonun dikey açısı (tilt)")
    mesafe_cm_2 = models.FloatField(null=True, blank=True, help_text="İkinci ultrasonik sensörün mesafesi (cm)")

    # 3D koordinatlar
    x_cm = models.FloatField(null=True, blank=True)
    y_cm = models.FloatField(null=True, blank=True)
    z_cm = models.FloatField(null=True, blank=True)

    # İsteğe bağlı
    hiz_cm_s = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"Point at Pan:{self.derece}°/Tilt:{self.dikey_aci}° -> {self.mesafe_cm} cm"