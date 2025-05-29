import dash
from dash import html, dcc, Output, Input, State  # Dash importları güncellendi
import dash_bootstrap_components as dbc
from django_plotly_dash import DjangoDash
import plotly.graph_objects as go

import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import math
import numpy as np
import signal  # Taramayı durdurmak için eklendi
import io  # Excel dışa aktarma için

# --- Sabitler ---
# Django projesinin ana dizinini (manage.py'nin olduğu yer) bulmaya çalışır.
# Bu dosya (dash_apps.py) dashboard_app klasöründe olduğu için,
# ana dizin genellikle bir üst seviyededir.
PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

DB_FILENAME = 'live_scan_data.sqlite3'  # sensor_script.py ile aynı olmalı!
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)

SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)

# Kilit ve PID dosyaları için mutlak yollar (sensor_script.py'deki ile aynı olmalı)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

# --- Varsayılan Tarama Ayarları (Dash Arayüzü için) ---
DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ (Yanıt #37'deki gibi) ---
title_card = dbc.Row([
    dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)
])

control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrol ve Ayarları", className="bg-primary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),  # minHeight artırıldı
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Başlangıç Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="start-angle-input", type="number", value=DEFAULT_UI_SCAN_START_ANGLE, min=0,
                                  max=180, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=0, max=180,
                                  step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])
# ... (stats_panel, system_card, scan_selector_card, export_card, analysis_card, visualization_tabs - Yanıt #37'deki gibi) ...
# Önceki cevaptaki (Yanıt #37) layout bileşenlerini buraya kopyalayabilirsiniz.
# Kısalık olması açısından hepsini tekrar eklemiyorum.
# Ana layout yapısı:
stats_panel = dbc.Card(dbc.CardBody(html.Div(id='realtime-values')),
                       className="mb-3")  # Basitleştirilmiş, içi callback ile dolacak
system_card = dbc.Card(dbc.CardBody(html.Div(id='system-status-values')), className="mb-3")  # Basitleştirilmiş
scan_selector_card = dbc.Card(dbc.CardBody(html.Div(id='scan-selector-content')), className="mb-3")  # Basitleştirilmiş
export_card = dbc.Card(dbc.CardBody(html.Div(id='export-content')), className="mb-3")  # Basitleştirilmiş
analysis_card = dbc.Card(dbc.CardBody(html.Div(id='analysis-output')), className="mb-3")  # Basitleştirilmiş
visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '70vh'}), label="2D Harita"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '70vh'}), label="Polar Grafik"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '70vh'}), label="Zaman Serisi")
])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, stats_panel, system_card, scan_selector_card, export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, analysis_card], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=5000, n_intervals=0),
    dcc.Download(id='download-csv'),
    dcc.Download(id='download-excel'),
])


# --- HELPER FONKSİYONLAR ---
def is_process_running(pid):
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)  # Salt okunur mod
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


# --- CALLBACK FONKSİYONLARI ---

@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'),
     State('end-angle-input', 'value'),
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val):
    ctx = dash.callback_context
    if not ctx.triggered or n_clicks_start == 0 or ctx.triggered_id != 'start-scan-button':
        return dash.no_update

    start_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
    end_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

    if not (0 <= start_a <= 180 and 0 <= end_a <= 180 and start_a <= end_a):
        return dbc.Alert("Geçersiz başlangıç/bitiş açıları!", color="danger", duration=4000)
    if not (1 <= step_a <= 45):
        return dbc.Alert("Geçersiz adım açısı (1-45 arası olmalı)!", color="danger", duration=4000)

    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: current_pid = int(pid_str)
        except (FileNotFoundError, ValueError, TypeError):
            current_pid = None

    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Sensör betiği zaten çalışıyor (PID: {current_pid}).", color="warning", duration=4000)

    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        print(
            f"Dash: Kalıntı kilit/PID dosyası bulundu ({LOCK_FILE_PATH_FOR_DASH}, {PID_FILE_PATH_FOR_DASH}). Siliniyor...")
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e_rm_lock:
            return dbc.Alert(f"Kalıntı kilit/PID dosyası silinirken hata: {e_rm_lock}. Lütfen manuel kontrol edin.",
                             color="danger", duration=5000)

    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}", color="danger", duration=5000)

        cmd = [
            python_executable, SENSOR_SCRIPT_PATH,
            "--start_angle", str(start_a),
            "--end_angle", str(end_a),
            "--step_angle", str(step_a)
        ]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2.5)

        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip()
                    if pid_str_new: new_pid = int(pid_str_new)
                if new_pid and is_process_running(new_pid):
                    return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid}). Veriler güncellenmeye başlayacak.",
                                     color="success", duration=4000)
                else:
                    return dbc.Alert(
                        f"Sensör betiği başlatıldı ancak PID ({new_pid}) ile çalışan bir process bulunamadı veya PID dosyası hatalı.",
                        color="warning", duration=5000)
            except Exception as e_pid_read:
                return dbc.Alert(
                    f"Sensör betiği başlatıldı ancak PID okunamadı ({PID_FILE_PATH_FOR_DASH}): {e_pid_read}",
                    color="warning", duration=5000)
        else:
            return dbc.Alert(
                f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını veya Raspberry Pi konsolunu kontrol edin.",
                color="danger", duration=5000)
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken genel hata: {str(e)}", color="danger", duration=5000)
    return dash.no_update  # Normalde buraya ulaşmamalı


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks_stop):
    ctx = dash.callback_context
    if not ctx.triggered or n_clicks_stop == 0 or ctx.triggered_id != 'stop-scan-button':
        return dash.no_update

    pid_to_kill = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: pid_to_kill = int(pid_str)
        except:
            pid_to_kill = None

    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            print(f"Dash: Sensör betiği (PID: {pid_to_kill}) için SIGTERM gönderiliyor...")
            os.kill(pid_to_kill, signal.SIGTERM)
            time.sleep(1.5)
            if is_process_running(pid_to_kill):
                print(f"Dash: Sensör betiği (PID: {pid_to_kill}) SIGTERM'e yanıt vermedi, SIGKILL gönderiliyor...")
                os.kill(pid_to_kill, signal.SIGKILL)
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) zorla durduruldu (SIGKILL).", color="warning",
                                 duration=4000)
            # Kilit ve PID dosyalarını burada da temizlemeyi deneyebiliriz, atexit de yapacak ama garanti için.
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
            return dbc.Alert(
                f"Sensör betiği (PID: {pid_to_kill}) için durdurma komutu (SIGTERM) gönderildi ve sonlandı.",
                color="info", duration=4000)
        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger",
                             duration=5000)
    else:
        # Kilit dosyası varsa ama process yoksa, kalıntı dosyaları temizle
        cleaned_stale = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            os.remove(LOCK_FILE_PATH_FOR_DASH);
            cleaned_stale = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            os.remove(PID_FILE_PATH_FOR_DASH);
            cleaned_stale = True
        if cleaned_stale:
            return dbc.Alert("Çalışan bir sensör betiği bulunamadı. Kalıntı kilit/PID dosyaları temizlendi.",
                             color="info", duration=4000)
        else:
            return dbc.Alert("Çalışan bir sensör betiği bulunamadı.", color="warning", duration=4000)


# ... (update_scan_dropdowns, update_realtime_values, update_all_graphs,
#      update_analysis_panel, update_system_card, export_csv_callback, export_excel_callback
#      fonksiyonları Yanıt #37'deki gibi veya ona benzer şekilde buraya eklenecek) ...
# Örnek olarak update_all_graphs'ı ekliyorum, diğerleri için Yanıt #37'ye bakabilirsiniz.

@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals'),
     Input('scan-select-dropdown', 'value')]
)
def update_all_graphs(n_intervals, selected_scan_id):
    conn, error_msg_conn = get_db_connection()
    fig_map = go.Figure().update_layout(title_text='2D Kartezyen Harita (Veri Bekleniyor)', uirevision=selected_scan_id)
    fig_polar = go.Figure().update_layout(title_text='Polar Grafik (Veri Bekleniyor)', uirevision=selected_scan_id)
    fig_time = go.Figure().update_layout(title_text='Zaman Serisi (Veri Bekleniyor)', uirevision=selected_scan_id)

    if error_msg_conn: return fig_map, fig_polar, fig_time

    current_scan_id_to_plot = selected_scan_id
    if not selected_scan_id:
        try:
            df_last_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn)
            if not df_last_scan.empty: current_scan_id_to_plot = int(df_last_scan['id'].iloc[0])
        except Exception as e:
            print(f"En son tarama ID'si alınırken hata: {e}")
        if not current_scan_id_to_plot:  # Hala ID yoksa
            if conn: conn.close(); return fig_map, fig_polar, fig_time

    if not current_scan_id_to_plot:
        if conn: conn.close(); return fig_map, fig_polar, fig_time

    try:
        df_points = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {current_scan_id_to_plot} ORDER BY id ASC",
            conn
        )
        df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {current_scan_id_to_plot}", conn)

        scan_status_str = df_scan_info['status'].iloc[0] if not df_scan_info.empty else "Bilinmiyor"
        start_time_str = time.strftime('%Y-%m-%d %H:%M', time.localtime(
            df_scan_info['start_time'].iloc[0])) if not df_scan_info.empty else "N/A"
        title_suffix = f"(ID: {current_scan_id_to_plot}, Başl: {start_time_str}, Dur: {scan_status_str})"

        if not df_points.empty:
            df_valid = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < 200)].copy()
            if not df_valid.empty:
                # 2D Kartezyen
                fig_map.add_trace(go.Scatter(x=df_valid['y_cm'], y=df_valid['x_cm'], mode='lines+markers', name='Sınır',
                                             marker=dict(size=5, color=df_valid['mesafe_cm'], colorscale='Viridis')))
                fig_map.add_trace(
                    go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                               name='Sensör'))
                fig_map.update_layout(title_text='2D Harita ' + title_suffix, xaxis_title="Yatay (cm)",
                                      yaxis_title="İleri (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1)
                # Polar
                fig_polar.add_trace(
                    go.Scatterpolar(r=df_valid['mesafe_cm'], theta=df_valid['angle_deg'], mode='lines+markers',
                                    name='Mesafe',
                                    marker=dict(color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=False)))
                fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix,
                                        polar=dict(radialaxis=dict(visible=True, range=[0, 200])))
                # Zaman Serisi
                if 'timestamp' in df_valid.columns:
                    df_time = df_valid.sort_values(by='timestamp')
                    time_labels = [time.strftime('%H:%M:%S', time.localtime(ts)) for ts in df_time['timestamp']]
                    fig_time.add_trace(
                        go.Scatter(x=time_labels, y=df_time['mesafe_cm'], mode='lines+markers', name='Mesafe'))
                fig_time.update_layout(title_text='Zaman Serisi - Mesafe ' + title_suffix, xaxis_title="Zaman",
                                       yaxis_title="Mesafe (cm)")
    except Exception as e:
        print(f"Grafik oluşturma hatası: {e}")
    finally:
        if conn: conn.close()
    return fig_map, fig_polar, fig_time

# Diğer callback'ler (update_scan_dropdowns, update_realtime_values, update_analysis_panel, 
# update_system_card, export_csv_callback, export_excel_callback) 
# Yanıt #37'deki gibi veya ona benzer şekilde buraya eklenecek.
# Tamamını eklemek yanıtı çok uzatacağından, prensip olarak yukarıdaki
# `update_all_graphs` ve `handle_start_scan_script` gibi yapıları kullanacaklardır.