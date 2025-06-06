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


from django.db import models
from django.utils import timezone

class ScanPoint(models.Model):
    scan = models.ForeignKey('Scan', on_delete=models.CASCADE, related_name='points')
    derece = models.FloatField() # Or DecimalField
    mesafe_cm = models.FloatField() # <-- THIS ONE! Or DecimalField
    hiz_cm_s = models.FloatField(null=True, blank=True) # If you calculate speed
    x_cm = models.FloatField()
    y_cm = models.FloatField()
    z_cm = models.FloatField()
    dikey_aci = models.FloatField(null=True, blank=True)
    mesafe_cm_2 = models.FloatField(null=True, blank=True)
    timestamp = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Point {self.id} at {self.derece}° - {self.mesafe_cm} cm"