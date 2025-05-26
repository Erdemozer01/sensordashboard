from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc  # Güncel importlar
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import math  # Kutupsal -> Kartezyen dönüşümü için

# --- Sabitler ---
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILENAME = 'db.sqlite3'  # sensor_script.py ile aynı isimde olmalı
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)

SENSOR_SCRIPT_FILENAME = 'sensor_script.py'  # sensor_script.py'nin adı
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'

app = DjangoDash('RealtimeSensorDashboard', add_bootstrap_links=True)

app.layout = html.Div([
    html.H1("Eş Zamanlı Servo Motorlu 2D Alan Tarama Paneli", style={'textAlign': 'center', 'marginBottom': '10px'}),

    html.Div([
        html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                    style={'marginRight': '10px', 'padding': '10px', 'fontSize': '16px'}),
        html.Span(id='scan-status-message', style={'fontSize': '16px', 'color': 'blue'})
    ], style={'textAlign': 'center', 'marginBottom': '20px'}),

    dcc.Interval(
        id='interval-component-scan',
        interval=1500,  # Her 1.5 saniyede bir güncelle (ayarlanabilir)
        n_intervals=0
    ),
    html.Div([
        dcc.Graph(id='scan-map-graph')  # 2D harita için tek grafik
    ]),
    html.Div(id='scan-summary-realtime',
             style={'padding': '20px', 'fontSize': '16px', 'marginTop': '20px', 'border': '1px solid #ddd',
                    'borderRadius': '5px', 'backgroundColor': '#f9f9f9'})
])


# --- Buton Callback'i (sensor_script.py'yi başlatmak için) ---
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks):
    if n_clicks > 0:
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            print(f"Dash: Kilit dosyası ({LOCK_FILE_PATH_FOR_DASH}) mevcut. Betik zaten çalışıyor olabilir.")
            return "Tarama betiği zaten çalışıyor gibi görünüyor veya kilit dosyası kalmış."
        try:
            python_executable = sys.executable
            print(f"'{SENSOR_SCRIPT_PATH}' betiği '{python_executable}' ile başlatılıyor...")
            process = subprocess.Popen(
                [python_executable, SENSOR_SCRIPT_PATH],
                start_new_session=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            time.sleep(1.5)  # Betiğin başlaması için
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return f"Tarama betiği başlatma komutu gönderildi (PID: {process.pid}). Betik çalışıyor olmalı."
            else:
                stdout, stderr = process.communicate(timeout=2)
                error_message = f"Tarama betiği başlatıldı ancak kilit dosyası ({LOCK_FILE_PATH_FOR_DASH}) oluşmadı. "
                if stderr: error_message += f"Hata Çıktısı: {stderr.decode(errors='ignore')}"
                return error_message
        except FileNotFoundError:
            return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}"
        except Exception as e:
            return f"Sensör betiği başlatılırken hata oluştu: {str(e)}"
    return dash.no_update


# --- Grafik Güncelleme Callback'i ---
@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('scan-summary-realtime', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_scan_map_graph(n):
    conn = None
    df_scans = pd.DataFrame()
    df_points = pd.DataFrame()
    error_message_div = []
    latest_scan_id = None

    fig_map = go.Figure()
    summary_children = [html.P("Veri bekleniyor...")]

    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası bulunamadı: {DB_PATH}")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)

        # En son (veya 'running' durumundaki) taramayı bul
        df_latest_scan_info = pd.read_sql_query("SELECT id, status FROM servo_scans ORDER BY start_time DESC LIMIT 1",
                                                conn)

        if not df_latest_scan_info.empty:
            latest_scan_id = df_latest_scan_info['id'].iloc[0]
            latest_scan_status = df_latest_scan_info['status'].iloc[0]
            # print(f"Dash: Son tarama ID: {latest_scan_id}, Durum: {latest_scan_status}") # Debug

            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm FROM scan_points WHERE scan_id = {latest_scan_id} ORDER BY angle_deg ASC",
                conn)

    except sqlite3.OperationalError as e_sql:
        msg = f"DB okuma hatası: {e_sql}."
        print(msg)
        error_message_div = [html.P(msg, style={'color': 'red'})]
    except FileNotFoundError as e_fnf:
        msg = f"Veritabanı dosyası ({DB_PATH}) bulunamadı. Sensör betiği çalıştırıldı mı?"
        print(msg)
        error_message_div = [html.P(msg, style={'color': 'orange'})]
    except Exception as e_gen:
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}"
        print(msg)
        error_message_div = [html.P(msg, style={'color': 'red'})]
    finally:
        if conn:
            conn.close()

    if error_message_div:  # Hata varsa hata mesajını göster
        summary_children = error_message_div
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Yüklenemedi/Hata)')
    elif not df_points.empty:
        # Geçerli mesafeleri filtrele (0'dan büyük ve sensörün maksimumundan küçük)
        # sensor.max_distance değerini burada bilmemiz gerekebilir, veya scriptten almalıyız.
        # Şimdilik 200cm (2.0m) olarak varsayalım.
        max_plot_distance = 200.0
        df_points_valid = df_points[(df_points['mesafe_cm'] > 0) & (df_points['mesafe_cm'] < max_plot_distance)]

        if not df_points_valid.empty:
            angles_rad = np.radians(df_points_valid['angle_deg'])
            distances = df_points_valid['mesafe_cm']

            # Kutupsal koordinatları Kartezyen'e çevir
            # Sensörün 0 derecesi X ekseninin pozitif yönü olduğunu varsayarsak:
            x_coords = distances * np.cos(angles_rad)
            y_coords = distances * np.sin(angles_rad)

            fig_map.add_trace(go.Scatter(
                x=y_coords,  # Genellikle y ekseni yatay yayılımı gösterir
                y=x_coords,  # X ekseni ileri mesafeyi gösterir
                mode='markers',
                name='Engeller',
                marker=dict(size=5, color=distances, colorscale='Viridis', showscale=True,
                            colorbar_title_text="Mesafe (cm)")
            ))
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, color='red'), name='Sensör'))

            fig_map.update_layout(
                title_text=f'Canlı 2D Tarama Haritası (Tarama ID: {latest_scan_id})',
                xaxis_title="Y Ekseni (cm) - Yatay Yayılım",
                yaxis_title="X Ekseni (cm) - İleri Mesafe",
                yaxis=dict(scaleanchor="x", scaleratio=1, rangemode='tozero'),  # Eşit ölçek ve 0'dan başla
                xaxis=dict(rangemode='normal'),  # X ekseni için otomatik aralık
                width=700, height=600,  # Grafik boyutları
                margin=dict(l=50, r=50, b=50, t=80)
            )
        else:
            fig_map.update_layout(
                title_text=f'2D Tarama Haritası (Tarama ID: {latest_scan_id} - Çizilecek geçerli nokta yok)')

        # Özet Bilgiler
        summary_children = [html.H4("Tarama Özeti:", style={'marginTop': '0px', 'marginBottom': '10px'})]
        if latest_scan_id:
            summary_children.append(html.P(
                f"Aktif/Son Tarama ID: {latest_scan_id} ({latest_scan_status if 'latest_scan_status' in locals() else 'Bilinmiyor'})"))
        summary_children.append(html.P(f"Toplam Okunan Nokta Sayısı (Bu Tarama): {len(df_points)}"))
        if not df_points_valid.empty:
            summary_children.append(html.P(f"Grafiğe Çizilen Geçerli Nokta Sayısı: {len(df_points_valid)}"))
            summary_children.append(html.P(f"Minimum Algılanan Mesafe: {df_points_valid['mesafe_cm'].min():.2f} cm"))
            summary_children.append(html.P(f"Maksimum Algılanan Mesafe: {df_points_valid['mesafe_cm'].max():.2f} cm"))

    else:  # df_points boşsa (hata yok ama veri de yok)
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Bekleniyor)')
        summary_children = [html.P("Veritabanında bu tarama için henüz nokta bulunamadı.")]

    return fig_map, summary_children  # Hız grafiği kaldırıldığı için sadece 2 output var, düzeltildi

# Callback'in Output listesini de düzeltmemiz gerekiyor, sadece 2 output var artık:
# @app.callback(
#     [Output('scan-map-graph', 'figure'),
#      Output('scan-summary-realtime', 'children')],
# ...
# )
# Evet, yukarıdaki callback tanımı zaten 2 output için.