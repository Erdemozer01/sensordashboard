# dashboard_app/dash_apps.py
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
import math
import numpy as np

# --- Sabitler ---
# Django projesinin ana dizini (manage.py'nin olduğu yer)
# Bu dosya (dash_apps.py) dashboard_app içinde olduğu için bir seviye yukarı çıkıyoruz.
# Bu yol Django projenizin yapısına göre değişebilir.
# Eğer manage.py, dashboard_app ile aynı seviyedeyse (nadiren):
# PROJECT_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
# Eğer dashboard_app, manage.py'nin olduğu klasörün bir alt klasörüyse:
PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

DB_FILENAME = 'db.sqlite3'  # sensor_script.py ile aynı isimde olmalı
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)

SENSOR_SCRIPT_FILENAME = 'sensor_script.py'  # sensor_script.py'nin adı
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)

# Kilit ve PID dosyaları için mutlak yollar (sensor_script.py'deki ile aynı olmalı)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

app = DjangoDash('RealtimeServoScannerDashboard', add_bootstrap_links=True)

app.layout = html.Div([
    html.H1("Eş Zamanlı Servo Motorlu 2D Alan Tarama Paneli", style={'textAlign': 'center', 'marginBottom': '10px'}),

    html.Div([
        html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                    style={'marginRight': '10px', 'padding': '10px', 'fontSize': '16px', 'backgroundColor': '#4CAF50',
                           'color': 'white', 'border': 'none', 'cursor': 'pointer'}),
        html.Span(id='scan-status-message', style={'marginLeft': '15px', 'fontSize': '16px'})
    ], style={'textAlign': 'center', 'marginBottom': '20px'}),

    dcc.Interval(
        id='interval-component-scan',
        interval=1200,  # Her 1.2 saniyede bir güncelle
        n_intervals=0
    ),
    html.Div(id='graphs-container', children=[
        dcc.Graph(id='scan-map-graph')
    ]),
    html.Div(id='scan-summary-realtime',
             style={'padding': '20px', 'fontSize': '16px', 'marginTop': '20px', 'border': '1px solid #ddd',
                    'borderRadius': '5px', 'backgroundColor': '#f9f9f9'})
])


def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return dash.no_update  # Butona tıklanmadıysa bir şey yapma

    button_id = ctx.triggered[0]['prop_id'].split('.')[0]
    if button_id == 'start-scan-button':
        current_pid = None
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    current_pid = int(pf.read().strip())
            except (FileNotFoundError, ValueError):
                current_pid = None

        if current_pid and is_process_running(current_pid):
            return f"Sensör betiği zaten çalışıyor (PID: {current_pid})."

        if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
            message = f"Kalıntı kilit/PID dosyası bulundu. Siliniyor ve yeniden başlatma denenecek."
            print("Dash: " + message)
            try:
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)  # Önce PID, sonra kilit
            except OSError as e_rm_lock:
                return f"Kalıntı kilit/PID dosyası silinirken hata: {e_rm_lock}. Lütfen manuel kontrol edin: {LOCK_FILE_PATH_FOR_DASH}, {PID_FILE_PATH_FOR_DASH}"

        try:
            python_executable = sys.executable  # Sanal ortamdaki python
            if not os.path.exists(SENSOR_SCRIPT_PATH):
                return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}"

            print(f"'{SENSOR_SCRIPT_PATH}' betiği '{python_executable}' ile başlatılıyor...")
            process = subprocess.Popen(
                [python_executable, SENSOR_SCRIPT_PATH],
                start_new_session=True,  # Django'dan bağımsız çalışsın
                # stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL # Arka planda sessiz çalışsın
            )
            time.sleep(2.0)

            if os.path.exists(PID_FILE_PATH_FOR_DASH):
                new_pid = None
                try:
                    with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                        new_pid = int(pf_new.read().strip())
                    return f"Sensör betiği başlatıldı (Yeni PID: {new_pid}). Veriler güncellenmeye başlayacak."
                except:
                    return "Sensör betiği başlatıldı ancak PID okunamadı. Durumu kontrol edin."
            else:
                return f"Sensör betiği başlatma komutu gönderildi, ancak PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını (eğer varsa) veya Raspberry Pi konsolunu kontrol edin."
        except Exception as e:
            return f"Sensör betiği başlatılırken genel hata: {str(e)}"
    return dash.no_update


@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('scan-summary-realtime', 'children')],
    [Input('interval-component-scan', 'n_intervals')]
)
def update_scan_map_graph(n):
    conn = None
    df_points = pd.DataFrame()
    error_message_div = []
    latest_scan_id = None
    latest_scan_status = "Bilinmiyor"
    latest_scan_start_time_str = "N/A"

    fig_map = go.Figure()
    summary_children = [html.P("Veri bekleniyor...")]

    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası bulunamadı: {DB_PATH}. Sensör betiği çalıştırıldı mı?")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)

        df_latest_scan_info = pd.read_sql_query(
            "SELECT id, status, start_time FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn
        )

        if not df_latest_scan_info.empty:
            latest_scan_id = int(df_latest_scan_info['id'].iloc[0])
            latest_scan_status = df_latest_scan_info['status'].iloc[0]
            latest_scan_start_epoch = df_latest_scan_info['start_time'].iloc[0]
            latest_scan_start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(latest_scan_start_epoch))

            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm FROM scan_points WHERE scan_id = {latest_scan_id} ORDER BY angle_deg ASC",
                conn)

    except sqlite3.OperationalError as e_sql:
        msg = f"Veritabanı okuma hatası: {e_sql}. Veritabanı kilitli olabilir veya dosya bozuk."
        error_message_div = [html.P(msg, style={'color': 'red'})]
    except FileNotFoundError as e_fnf:
        msg = str(e_fnf)
        error_message_div = [html.P(msg, style={'color': 'orange'})]
    except Exception as e_gen:
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}"
        error_message_div = [html.P(msg, style={'color': 'red'})]
    finally:
        if conn:
            conn.close()

    if error_message_div:
        summary_children = error_message_div
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Yüklenemedi/Hata)')
    elif not df_points.empty:
        max_plot_distance = 200.0
        df_points_valid = df_points[
            (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_distance)].copy()

        if not df_points_valid.empty:
            df_points_valid.loc[:, 'angle_rad'] = np.radians(df_points_valid['angle_deg'])
            df_points_valid.loc[:, 'x_coord'] = df_points_valid['mesafe_cm'] * np.cos(df_points_valid['angle_rad'])
            df_points_valid.loc[:, 'y_coord'] = df_points_valid['mesafe_cm'] * np.sin(df_points_valid['angle_rad'])

            fig_map.add_trace(go.Scatter(
                x=df_points_valid['y_coord'], y=df_points_valid['x_coord'],
                mode='markers', name='Engeller',
                marker=dict(size=5, color=df_points_valid['mesafe_cm'], colorscale='Plasma', showscale=True,
                            colorbar_title_text="Mesafe (cm)")
            ))
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör'))

            fig_map.update_layout(
                title_text=f'Canlı 2D Tarama Haritası (Tarama ID: {latest_scan_id}, Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})',
                xaxis_title="Yatay Yayılım (cm)", yaxis_title="İleri Mesafe (cm)",
                yaxis_scaleanchor="x", yaxis_scaleratio=1,
                width=700, height=600,
                margin=dict(l=40, r=40, t=60, b=40), plot_bgcolor='rgba(245,245,245,1)'
            )
        else:
            fig_map.update_layout(title_text=f'2D Tarama (Tarama ID: {latest_scan_id} - Çizilecek geçerli nokta yok)')

        summary_children = [html.H4("Tarama Özeti:", style={'marginTop': '0px', 'marginBottom': '10px'})]
        if latest_scan_id:
            summary_children.append(html.P(
                f"Aktif/Son Tarama ID: {latest_scan_id} (Başlangıç: {latest_scan_start_time_str}, Durum: {latest_scan_status})"))
        summary_children.append(html.P(f"Toplam Okunan Nokta Sayısı: {len(df_points)}"))
        if not df_points_valid.empty:
            summary_children.append(html.P(f"Grafiğe Çizilen Geçerli Nokta Sayısı: {len(df_points_valid)}"))
            summary_children.append(html.P(f"Min Algılanan Mesafe: {df_points_valid['mesafe_cm'].min():.2f} cm"))
            summary_children.append(html.P(f"Max Algılanan Mesafe: {df_points_valid['mesafe_cm'].max():.2f} cm"))
    else:
        fig_map.update_layout(title_text='2D Tarama Haritası (Veri Bekleniyor)')
        if latest_scan_id:
            summary_children = [html.P(
                f"Tarama ID: {latest_scan_id} (Durum: {latest_scan_status}). Bu tarama için henüz nokta bulunamadı.")]
        else:
            summary_children = [html.P("Aktif tarama veya görüntülenecek veri yok.")]

    return fig_map, summary_children