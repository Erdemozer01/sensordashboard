# scanner/admin.py

from django.contrib import admin
from scanner.models import Scan, ScanPoint

class ScanPointInline(admin.TabularInline):
    """Scan detay sayfasında ilişkili noktaları göstermek için kullanılır."""
    model = ScanPoint
    extra = 0  # Yeni nokta ekleme alanı gösterme
    readonly_fields = ('derece', 'mesafe_cm', 'hiz_cm_s', 'timestamp', 'x_cm', 'y_cm') # Noktalar değiştirilemez olmalı
    can_delete = False # Admin'den nokta silmeyi engelle

    def has_add_permission(self, request, obj=None):
        return False # Yeni nokta ekleme butonunu kaldır

@admin.register(Scan)
class ScanAdmin(admin.ModelAdmin):
    # Tarama listesinde gösterilecek alanlar
    list_display = ('id', 'start_time', 'status', 'point_count', 'calculated_area_cm2')
    list_filter = ('status', 'start_time')
    search_fields = ('id', 'ai_commentary')

    # Detay sayfasında alanları gruplama ve salt okunur yapma
    fieldsets = (
        ('Genel Bilgiler', {
            'fields': ('id', 'start_time', 'status')
        }),
        ('Tarama Ayarları', {
            'classes': ('collapse',), # Gizlenebilir bölüm
            'fields': ('start_angle_setting', 'end_angle_setting', 'step_angle_setting', 'buzzer_distance_setting', 'invert_motor_direction_setting')
        }),
        ('Analiz Sonuçları', {
            'fields': ('calculated_area_cm2', 'perimeter_cm', 'max_width_cm', 'max_depth_cm')
        }),
        ('Yapay Zeka Analizi', {
            'fields': ('ai_commentary',)
        }),
    )
    readonly_fields = ('id', 'start_time')

    # Tarama noktalarını aynı sayfada göstermek için inline ekle
    inlines = [ScanPointInline]

    def point_count(self, obj):
        # Listede her taramanın kaç noktası olduğunu gösteren özel bir alan
        return obj.points.count()
    point_count.short_description = "Nokta Sayısı"


@admin.register(ScanPoint)
class ScanPointAdmin(admin.ModelAdmin):
    """ScanPoint'leri ayrıca görüntülemek için (isteğe bağlı)."""
    list_display = ('scan', 'timestamp', 'derece', 'mesafe_cm')
    list_filter = ('scan',)
    search_fields = ('scan__id',)