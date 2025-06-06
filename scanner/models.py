# scanner/models.py

from django.db import models
from django.utils import timezone


class Scan(models.Model): # <-- THIS CLASS MUST EXIST!
    # Your fields for the Scan model
    start_angle_setting = models.FloatField(default=0.0)
    end_angle_setting = models.FloatField(default=0.0)
    step_angle_setting = models.FloatField(default=10.0)
    buzzer_distance_setting = models.IntegerField(default=10)
    invert_motor_direction_setting = models.BooleanField(default=False)
    start_time = models.DateTimeField(auto_now_add=True)
    end_time = models.DateTimeField(null=True, blank=True)
    calculated_area_cm2 = models.FloatField(null=True, blank=True)
    perimeter_cm = models.FloatField(null=True, blank=True)
    max_width_cm = models.FloatField(null=True, blank=True)
    max_depth_cm = models.FloatField(null=True, blank=True)
    ai_commentary = models.TextField(blank=True, null=True)

    class Status(models.TextChoices):
        RUNNING = 'RUN', 'Running'
        COMPLETED = 'CMP', 'Completed'
        INTERRUPTED = 'INT', 'Interrupted'
        ERROR = 'ERR', 'Error'
        INSUFFICIENT_POINTS = 'ISP', 'Insufficient Points'

    status = models.CharField(max_length=3, choices=Status.choices, default=Status.RUNNING)


    def __str__(self):
        return f"Scan {self.id} ({self.status}) - {self.start_time.strftime('%Y-%m-%d %H:%M')}"


class ScanPoint(models.Model):
    scan = models.ForeignKey(Scan, on_delete=models.CASCADE, related_name='points')

    derece = models.FloatField() # Or models.DecimalField
    mesafe_cm = models.FloatField() # <--- THIS MUST BE HERE
    x_cm = models.FloatField()
    y_cm = models.FloatField()
    z_cm = models.FloatField()
    timestamp = models.DateTimeField(default=timezone.now)
    # Add other fields like hiz_cm_s, mesafe_cm_2 if they are in your database schema
    hiz_cm_s = models.FloatField(null=True, blank=True, default=0.0) # Example: add default
    mesafe_cm_2 = models.FloatField(null=True, blank=True, default=0.0) # Example: add default

    def __str__(self):
        return f"ScanPoint {self.id} (Scan {self.scan.id}) - {self.derece}° {self.mesafe_cm}cm"