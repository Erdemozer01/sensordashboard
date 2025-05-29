# dashboard_app/dash_apps.py
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc
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
import dash_bootstrap_components as dbc

# --- Sabitler ---
PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

# --- Varsayılan Tarama Ayarları (Dash Arayüzü için) ---
DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ ---
title_card = dbc.Row([
    dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)
])

control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrol ve Ayarları", className="bg-primary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Button('2D Taramayı Başlat', id='start-scan-button',
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur (Deneysel)', id='stop-scan-button',
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '20px'},
                 className="text-center mb-3"),
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

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), width=4),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4)
        ])
    ])
])

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Div([html.H6("Sensör Betiği:"), html.H5(id='script-status', children="Beklemede")])),
            # dbc.Col(html.Div([html.H6("Servo Pozisyonu:"), html.H5(id='servo-position', children="--°")])) # Anlık servo pos. için ayrı bir veri kaynağı gerekir. Şimdilik son okunan açı kullanılır.
        ]),
        # CPU/RAM için placeholder, gerçek implementasyon psutil veya /proc okuması gerektirir.
        dbc.Row([
            dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                              dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"},
                                           className="mb-2")])),
            dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                              dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"},
                                           className="mb-2")]))
        ])
    ])
])

scan_selector_card = dbc.Card([
    dbc.CardHeader("Geçmiş Taramalar", className="bg-light"),
    dbc.CardBody([
        html.Label("Görüntülenecek Tarama ID:"),
        dcc.Dropdown(id='scan-select-dropdown', placeholder="Tarama seçin...", style={'marginBottom': '10px'}),
        # Karşılaştırma özelliği daha sonra eklenebilir.
        # html.Label("Karşılaştırılacak Tarama ID (Opsiyonel):"),
        # dcc.Dropdown(id='compare-scan-dropdown', placeholder="Tarama seçin..."),
    ])
])

export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma", className="bg-light"),
    dbc.CardBody([
        dbc.Button('Seçili Taramayı CSV İndir', id='export-csv-button', color="primary", className="me-2 w-100 mb-2"),
        dcc.Download(id='download-csv'),
        dbc.Button('Seçili Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
        dcc.Download(id='download-excel'),
        # PNG kaydetme daha karmaşık, şimdilik kaldırıldı.
    ])
])

analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi", className="bg-dark text-white"),
    dbc.CardBody(html.Div(id='analysis-output'))  # İçerik callback ile dolacak
])

visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)")
])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([  # Sol Kolon
            control_panel,
            dbc.Row(html.Div(style={"height": "15px"})),  # Boşluk
            stats_panel,
            dbc.Row(html.Div(style={"height": "15px"})),
            system_card,
            dbc.Row(html.Div(style={"height": "15px"})),
            scan_selector_card,
            dbc.Row(html.Div(style={"height": "15px"})),
            export_card,
        ], md=4, className="mb-3"),  # Orta ekranlarda 4 kolon

        dbc.Col([  # Sağ Kolon
            visualization_tabs,
            dbc.Row(html.Div(style={"height": "15px"})),
            analysis_card,
        ], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),  # Grafik ve özetler için
    dcc.Interval(id='interval-component-system', interval=5000, n_intervals=0)  # Sistem durumu için
])


# --- CALLBACK FONKSİYONLARI ---

def get_db_connection():
    """Veritabanı bağlantısı oluşturur."""
    try:
        if not os.path.exists(DB_PATH): return None, "Veritabanı dosyası bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


@app.callback(  # Tarama listesini güncelle
    [Output('scan-select-dropdown', 'options'), Output('scan-select-dropdown', 'value')],
    [Input('interval-component-main', 'n_intervals')],
    [State('scan-select-dropdown', 'value')]
)
def update_scan_dropdowns(n_intervals, current_selected_scan_id):
    conn, error = get_db_connection()
    options = []
    new_selected_scan_id = current_selected_scan_id

    if conn:
        try:
            # start_angle_setting, end_angle_setting, step_angle_setting de çekilebilir
            df = pd.read_sql_query(
                "SELECT id, start_time, status FROM servo_scans ORDER BY start_time DESC LIMIT 30", conn
            )
            for _, row in df.iterrows():
                scan_time = time.strftime('%y-%m-%d %H:%M', time.localtime(row['start_time']))
                label = f"ID:{row['id']} ({scan_time}) St:{row['status']}"
                options.append({"label": label, "value": int(row['id'])})

            # Eğer bir seçim yoksa veya seçili ID listede yoksa en sonuncuyu seç
            if not new_selected_scan_id and options:
                new_selected_scan_id = options[0]['value']
            elif new_selected_scan_id and options and not any(opt['value'] == new_selected_scan_id for opt in options):
                new_selected_scan_id = options[0]['value']

        except Exception as e:
            print(f"Dropdown güncelleme hatası: {e}")
        finally:
            conn.close()
    else:
        print(error)  # DB bağlantı hatası

    # Eğer hiç seçenek yoksa ve bir ID seçiliyse, seçimi temizle
    if not options and new_selected_scan_id:
        new_selected_scan_id = None

    return options, new_selected_scan_id


@app.callback(  # Başlatma butonu
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'),
     State('end-angle-input', 'value'),
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks, start_angle, end_angle, step_angle):
    # ... (Bu callback fonksiyonu bir önceki cevaptakiyle aynı, SADECE SENSOR_SCRIPT_PATH'a argümanları ekleyeceğiz) ...
    # ... Argüman eklenmiş hali: ...
    ctx = dash.callback_context;
    if not ctx.triggered or n_clicks == 0: return dash.no_update
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    if current_pid and is_process_running(current_pid): return dbc.Alert(
        f"Sensör betiği zaten çalışıyor (PID: {current_pid}).", color="warning")
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit/PID silinirken hata: {e}.", color="danger")
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(
            f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}", color="danger")

        cmd = [python_executable, SENSOR_SCRIPT_PATH,
               "--start_angle", str(start_angle if start_angle is not None else DEFAULT_UI_SCAN_START_ANGLE),
               "--end_angle", str(end_angle if end_angle is not None else DEFAULT_UI_SCAN_END_ANGLE),
               "--step_angle", str(step_angle if step_angle is not None else DEFAULT_UI_SCAN_STEP_ANGLE)
               ]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2.5)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip();
                if pid_str_new: new_pid = int(pid_str_new)
                if new_pid and is_process_running(new_pid):
                    return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid}).", color="success")
                else:
                    return dbc.Alert(f"Sensör betiği başlatıldı ama PID ({new_pid}) ile process bulunamadı.",
                                     color="warning")
            except Exception as e:
                return dbc.Alert(f"PID okunurken hata: {e}", color="warning")
        else:
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını kontrol edin.",
                             color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken hata: {str(e)}", color="danger")
    return dash.no_update


@app.callback(  # Durdurma Butonu (Deneysel)
    Output('scan-status-message', 'children', allow_duplicate=True),  # allow_duplicate eklendi
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks):
    if n_clicks > 0:
        pid_to_kill = None
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip()
                    if pid_str: pid_to_kill = int(pid_str)
            except:
                pass

        if pid_to_kill and is_process_running(pid_to_kill):
            try:
                os.kill(pid_to_kill, signal.SIGTERM)  # Önce nazikçe sonlandırmayı dene
                time.sleep(1)
                if is_process_running(pid_to_kill):  # Hala çalışıyorsa
                    os.kill(pid_to_kill, signal.SIGKILL)  # Zorla kapat
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) için durdurma komutu gönderildi.", color="info")
            except Exception as e:
                return dbc.Alert(f"Sensör betiği durdurulurken hata: {e}", color="danger")
        else:
            return dbc.Alert("Çalışan bir sensör betiği bulunamadı.", color="warning")
    return dash.no_update


@app.callback(  # Anlık değerler
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    [Input('interval-component-main', 'n_intervals')],
    [State('scan-select-dropdown', 'value')]  # Hangi taramanın anlık değerleri? Ya da en sonuncusu?
    # En iyisi her zaman en son DB kaydını almak
)
def update_realtime_values(n_intervals, selected_scan_id):
    conn, error = get_db_connection()
    angle, distance, speed = "--°", "-- cm", "-- cm/s"
    if conn:
        try:
            # Her zaman en son eklenen noktayı al, seçili tarama ID'sinden bağımsız olarak
            df = pd.read_sql_query("SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points ORDER BY id DESC LIMIT 1",
                                   conn)
            if not df.empty:
                angle = f"{df['angle_deg'].iloc[0]:.0f}°"
                distance = f"{df['mesafe_cm'].iloc[0]:.1f} cm"
                speed = f"{df['hiz_cm_s'].iloc[0]:.1f} cm/s"
        except Exception as e:
            print(f"Anlık değerler alınırken hata: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (Anlık Değerler): {error}")
    return angle, distance, speed


@app.callback(  # Grafiklerin güncellenmesi
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals'),
     Input('scan-select-dropdown', 'value')]  # Seçilen tarama ID'si
)
def update_all_graphs(n_intervals, selected_scan_id):
    conn, error_msg_conn = get_db_connection()
    fig_map = go.Figure()
    fig_polar = go.Figure()
    fig_time = go.Figure()

    # Varsayılan başlıklar
    fig_map.update_layout(title_text='2D Kartezyen Harita (Veri Bekleniyor)')
    fig_polar.update_layout(title_text='Polar Grafik (Veri Bekleniyor)')
    fig_time.update_layout(title_text='Zaman Serisi (Veri Bekleniyor)')

    if error_msg_conn:  # DB bağlantı hatası varsa
        print(f"Grafik güncelleme DB Hatası: {error_msg_conn}")
        return fig_map, fig_polar, fig_time  # Boş grafikler döner

    current_scan_id_to_plot = selected_scan_id
    if not selected_scan_id:  # Eğer dropdown'dan bir seçim yapılmadıysa, en son taramayı al
        try:
            df_last_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn)
            if not df_last_scan.empty:
                current_scan_id_to_plot = int(df_last_scan['id'].iloc[0])
        except Exception as e:
            print(f"En son tarama ID'si alınırken hata: {e}")
            # Hata durumunda boş grafikler dönülecek
            if conn: conn.close()
            return fig_map, fig_polar, fig_time

    if not current_scan_id_to_plot:  # Hala bir tarama ID'si yoksa
        if conn: conn.close()
        return fig_map, fig_polar, fig_time

    try:
        df_points = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {current_scan_id_to_plot} ORDER BY id ASC",
            conn
        )
        df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {current_scan_id_to_plot}",
                                         conn)  # Analiz için lazım olabilir

        if not df_points.empty:
            # 2D Kartezyen Harita
            # ... (Bir önceki cevaptaki fig_map oluşturma mantığı - x_cm, y_cm kullanarak) ...
            df_valid_map = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < 200)].copy()
            if not df_valid_map.empty:
                fig_map.add_trace(
                    go.Scatter(x=df_valid_map['y_cm'], y=df_valid_map['x_cm'], mode='lines+markers', name='Sınır',
                               marker=dict(size=5, color=df_valid_map['mesafe_cm'], colorscale='Viridis')))
                fig_map.add_trace(
                    go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                               name='Sensör'))
            fig_map.update_layout(title_text=f'2D Harita (ID: {current_scan_id_to_plot})', xaxis_title="Yatay (cm)",
                                  yaxis_title="İleri (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1)

            # Polar Grafik
            # ... (Bir önceki cevaptaki fig_polar oluşturma mantığı - angle_deg, mesafe_cm kullanarak) ...
            df_valid_polar = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < 200)].copy()
            if not df_valid_polar.empty:
                fig_polar.add_trace(go.Scatterpolar(r=df_valid_polar['mesafe_cm'], theta=df_valid_polar['angle_deg'],
                                                    mode='lines+markers', name='Mesafe',
                                                    marker=dict(color=df_valid_polar['mesafe_cm'], colorscale='Viridis',
                                                                showscale=False)))
            fig_polar.update_layout(title_text=f'Polar Grafik (ID: {current_scan_id_to_plot})',
                                    polar=dict(radialaxis=dict(visible=True, range=[0, 200])))

            # Zaman Serisi (Mesafe vs Zaman)
            # ... (Bir önceki cevaptaki fig_time oluşturma mantığı - timestamp, mesafe_cm kullanarak) ...
            if 'timestamp' in df_points.columns:
                df_time_series = df_points.sort_values(by='timestamp')
                time_labels = [time.strftime('%H:%M:%S', time.localtime(ts)) for ts in df_time_series['timestamp']]
                fig_time.add_trace(
                    go.Scatter(x=time_labels, y=df_time_series['mesafe_cm'], mode='lines+markers', name='Mesafe'))
            fig_time.update_layout(title_text=f'Zaman Serisi - Mesafe (ID: {current_scan_id_to_plot})',
                                   xaxis_title="Zaman", yaxis_title="Mesafe (cm)")

    except Exception as e:
        print(f"Grafik oluşturma hatası: {e}")
    finally:
        if conn: conn.close()

    return fig_map, fig_polar, fig_time


@app.callback(  # Analiz kartı
    [Output('calculated-area', 'children'),
     Output('perimeter-length', 'children'),
     Output('max-width', 'children'),
     Output('max-depth', 'children')],
    [Input('scan-select-dropdown', 'value')]  # Veya interval ile de tetiklenebilir
)
def update_analysis_panel(selected_scan_id):
    # ... (Bir önceki cevaptaki update_analysis fonksiyonu - seçilen scan_id'ye göre servo_scans ve scan_points tablolarından veri çeker) ...
    # ... hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm değerlerini döndürür ...
    conn, error = get_db_connection()
    area, perimeter, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    if conn and selected_scan_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {selected_scan_id}",
                conn)
            if not df_scan.empty:
                area = f"{df_scan['hesaplanan_alan_cm2'].iloc[0]:.2f} cm²" if pd.notnull(
                    df_scan['hesaplanan_alan_cm2'].iloc[0]) else "-- cm²"
                perimeter = f"{df_scan['cevre_cm'].iloc[0]:.2f} cm" if pd.notnull(
                    df_scan['cevre_cm'].iloc[0]) else "-- cm"
                width = f"{df_scan['max_genislik_cm'].iloc[0]:.2f} cm" if pd.notnull(
                    df_scan['max_genislik_cm'].iloc[0]) else "-- cm"
                depth = f"{df_scan['max_derinlik_cm'].iloc[0]:.2f} cm" if pd.notnull(
                    df_scan['max_derinlik_cm'].iloc[0]) else "-- cm"
        except Exception as e:
            print(f"Analiz paneli hatası: {e}")
        finally:
            conn.close()
    elif error:
        print(f"DB Bağlantı Hatası (Analiz): {error}")
    return area, perimeter, width, depth


@app.callback(  # Sistem durumu (script, cpu, ram)
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'children'),
     Output('ram-usage', 'value'), Output('ram-usage', 'children')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    # ... (Bir önceki cevaptaki update_system_status ve update_system_metrics birleştirilmiş hali) ...
    # ... Script durumu için LOCK/PID kontrolü, CPU/RAM için /proc okuması ...
    script_status_text, status_class_name = "Beklemede", "text-secondary"
    pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: pid = int(pid_str)
        except:
            pass
    if pid and is_process_running(pid): script_status_text, status_class_name = "Çalışıyor", "text-success"

    cpu_percent, ram_percent = 0, 0
    try:  # psutil daha iyi olur ama /proc okuması bağımlılık gerektirmez
        # CPU
        with open('/proc/stat', 'r') as f1, open('/proc/stat', 'r') as f2:
            line1 = f1.readline().split()
            time.sleep(0.1)  # Kısa bir bekleme
            line2 = f2.readline().split()

        idle1, total1 = float(line1[4]), sum(map(float, line1[1:8]))
        idle2, total2 = float(line2[4]), sum(map(float, line2[1:8]))

        idle_delta, total_delta = idle2 - idle1, total2 - total1
        if total_delta > 0: cpu_percent = round(100.0 * (1.0 - idle_delta / total_delta), 1)

        # RAM
        mem_info = {}
        with open('/proc/meminfo', 'r') as f_mem:
            for line in f_mem:
                parts = line.split(':');
                key = parts[0];
                value = parts[1].strip()
                if value.endswith('kB'): value = float(value[:-2].strip()) * 1024
                mem_info[key] = value
        if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:  # MemAvailable daha iyi bir gösterge
            ram_percent = round(100.0 * (mem_info['MemTotal'] - mem_info['MemAvailable']) / mem_info['MemTotal'], 1)
    except Exception as e:
        print(f"CPU/RAM okuma hatası: {e}")

    return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


@app.callback(  # CSV İndirme
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    [State('scan-select-dropdown', 'value')],
    prevent_initial_call=True
)
def export_csv_callback(n_clicks, selected_scan_id):
    # ... (Bir önceki cevaptaki export_csv fonksiyonu) ...
    if not selected_scan_id: return dash.no_update
    conn, error = get_db_connection()
    if conn:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {selected_scan_id}", conn)
            return dcc.send_data_frame(df.to_csv, f"tarama_{selected_scan_id}.csv", index=False)
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (CSV): {error}")
    return dash.no_update


@app.callback(  # Excel İndirme
    Output('download-excel', 'data'),
    [Input('export-excel-button', 'n_clicks')],
    [State('scan-select-dropdown', 'value')],
    prevent_initial_call=True
)
def export_excel_callback(n_clicks, selected_scan_id):
    # ... (Bir önceki cevaptaki export_excel fonksiyonu) ...
    if not selected_scan_id: return dash.no_update
    conn, error = get_db_connection()
    if conn:
        try:
            df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {selected_scan_id}", conn)
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {selected_scan_id}", conn)

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_points.to_excel(writer, sheet_name='Scan Points', index=False)
                df_scan_info.to_excel(writer, sheet_name='Scan Info', index=False)
            excel_buffer.seek(0)
            return dcc.send_bytes(excel_buffer.read(), f"tarama_{selected_scan_id}.xlsx")
        except Exception as e:
            print(f"Excel indirme hatası: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (Excel): {error}")
    return dash.no_update