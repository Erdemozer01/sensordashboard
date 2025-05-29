# dashboard_app/dash_apps.py
from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc, Output, Input, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import math
import numpy as np
import signal
import io  # Excel dışa aktarma için

# --- Sabitler ---
# Django projesinin ana dizinini (manage.py'nin olduğu yer) bulmaya çalışır.
# Bu dosya (dash_apps.py) dashboard_app klasöründe olduğu için,
# ana dizin genellikle bir üst seviyededir.
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:  # __file__ interaktif modda tanımlı olmayabilir
    PROJECT_ROOT_DIR = os.getcwd()  # Geçici bir çözüm, Django ile çalışırken __file__ tanımlı olur.

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
            dbc.Col(html.Button('2D Taramayı Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Başl. Açı (°)", style={"width": "120px"}),
                        dbc.Input(id="start-angle-input", type="number", value=DEFAULT_UI_SCAN_START_ANGLE, min=0,
                                  max=180, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=0, max=180,
                                  step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])

stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody([html.Div(id='realtime-values', children=[
        dbc.Row([
            dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]), width=4,
                    className="text-center"),
            dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=4,
                    className="text-center")
        ])])])
], className="mb-3")

system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody([
        dbc.Row([
            dbc.Col(html.Div([html.H6("Sensör Betiği:"), html.H5(id='script-status', children="Beklemede")])),
        ], className="mb-2"),
        dbc.Row([
            dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                              dbc.Progress(id='cpu-usage', value=0, color="success", style={"height": "20px"},
                                           className="mb-1", label="0%")])),
            dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                              dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"},
                                           className="mb-1", label="0%")]))
        ])])
], className="mb-3")

scan_selector_card = dbc.Card([
    dbc.CardHeader("Geçmiş Taramalar", className="bg-light"),
    dbc.CardBody([
        html.Label("Görüntülenecek Tarama ID:"),
        dcc.Dropdown(id='scan-select-dropdown', placeholder="Tarama seçin...", style={'marginBottom': '10px'}),
    ])
], className="mb-3")

export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma", className="bg-light"),
    dbc.CardBody([
        dbc.Button('Seçili Taramayı CSV İndir', id='export-csv-button', color="primary", className="me-1 w-100 mb-2"),
        dcc.Download(id='download-csv'),
        dbc.Button('Seçili Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100 mb-2"),
        # mb-2 eklendi
        dcc.Download(id='download-excel'),
        # html.Button('Grafiği Kaydet (PNG)', id='save-image-button', className="btn btn-outline-info w-100") # PNG kaydetme daha karmaşık
    ])
], className="mb-3")

analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi", className="bg-dark text-white"),
    dbc.CardBody(html.Div(id='analysis-output', children=[
        dbc.Row([
            dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
            dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])
        ]),
        dbc.Row([
            dbc.Col([html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
            dbc.Col([html.H6("Max Derinlik:"), html.H4(id='max-depth', children="-- cm")])
        ], className="mt-2")
    ]))
])

visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)")
])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card,
                 dbc.Row(html.Div(style={"height": "15px"})), scan_selector_card,
                 dbc.Row(html.Div(style={"height": "15px"})), export_card],
                md=4, className="mb-3"),
        dbc.Col([visualization_tabs,
                 dbc.Row(html.Div(style={"height": "15px"})), analysis_card],
                md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
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
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
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
    if n_clicks_start is None or n_clicks_start == 0:
        return dash.no_update

    start_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
    end_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

    if not (0 <= start_a <= 180 and 0 <= end_a <= 180 and start_a <= end_a):
        return dbc.Alert("Geçersiz başlangıç/bitiş açıları!", color="danger", duration=4000)
    if not (1 <= step_a <= 45):  # Adım açısı için mantıklı bir sınır
        return dbc.Alert("Geçersiz adım açısı (1-45 arası olmalı)!", color="danger", duration=4000)

    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
                if pid_str: current_pid = int(pid_str)
        except (FileNotFoundError, ValueError, TypeError) as e:
            print(f"Dash: PID dosyası ({PID_FILE_PATH_FOR_DASH}) okunurken/dönüştürülürken hata: {e}")
            current_pid = None

    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Sensör betiği zaten çalışıyor (PID: {current_pid}).", color="warning", duration=4000)

    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        print(f"Dash: Kalıntı kilit/PID dosyası bulundu. Siliniyor...")
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
            "--start_angle", str(start_a), "--end_angle", str(end_a), "--step_angle", str(step_a)
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
                    return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid}).", color="success", duration=5000)
                else:
                    return dbc.Alert(f"Sensör betiği başlatıldı ama PID ({new_pid}) ile process bulunamadı.",
                                     color="warning", duration=5000)
            except Exception as e:
                return dbc.Alert(f"PID okunurken hata ({PID_FILE_PATH_FOR_DASH}): {e}", color="warning", duration=5000)
        else:
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını kontrol edin.",
                             color="danger", duration=5000)
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken hata: {str(e)}", color="danger", duration=5000)
    return dash.no_update


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_stop_scan_script(n_clicks_stop):
    if n_clicks_stop is None or n_clicks_stop == 0:
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
            time.sleep(2.0)  # atexit'in çalışması ve dosyaları silmesi için daha uzun bekle
            if is_process_running(pid_to_kill):
                print(f"Dash: Sensör betiği (PID: {pid_to_kill}) SIGTERM'e yanıt vermedi, SIGKILL gönderiliyor...")
                os.kill(pid_to_kill, signal.SIGKILL)
                time.sleep(0.5)  # SIGKILL sonrası

            # Kilit ve PID dosyalarının silindiğini kontrol et
            if not os.path.exists(PID_FILE_PATH_FOR_DASH) and not os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durduruldu ve kilit dosyaları temizlendi.",
                                 color="info", duration=4000)
            else:
                return dbc.Alert(
                    f"Sensör betiği (PID: {pid_to_kill}) durduruldu, ancak kilit/PID dosyaları hala mevcut olabilir. Manuel kontrol edin.",
                    color="warning", duration=5000)

        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger",
                             duration=5000)
    else:
        msg = "Çalışan bir sensör betiği bulunamadı."
        cleaned = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
        if cleaned: msg += " Kalıntı kilit/PID dosyaları temizlendi."
        return dbc.Alert(msg, color="warning", duration=4000)


@app.callback(
    [Output('scan-select-dropdown', 'options'), Output('scan-select-dropdown', 'value')],
    [Input('interval-component-main', 'n_intervals')],  # Ana interval ile tetiklensin
    [State('scan-select-dropdown', 'value')]
)
def update_scan_dropdowns(n_intervals, current_selected_scan_id):
    conn, error = get_db_connection()
    options = []
    new_selected_scan_id = current_selected_scan_id
    if conn:
        try:
            df = pd.read_sql_query(
                "SELECT id, start_time, status, start_angle_setting, end_angle_setting, step_angle_setting FROM servo_scans ORDER BY start_time DESC LIMIT 30",
                conn
            )
            for _, row in df.iterrows():
                scan_time = time.strftime('%y-%m-%d %H:%M', time.localtime(row['start_time']))
                label = f"ID:{row['id']} ({scan_time}) {row['start_angle_setting']}-{row['end_angle_setting']}/{row['step_angle_setting']}° St:{row['status']}"
                options.append({"label": label, "value": int(row['id'])})
            if not new_selected_scan_id and options:
                new_selected_scan_id = options[0]['value']
            elif new_selected_scan_id and options and not any(opt['value'] == new_selected_scan_id for opt in options):
                new_selected_scan_id = options[0]['value']
        except Exception as e:
            print(f"Dropdown güncelleme hatası: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (Dropdown): {error}")
    if not options and new_selected_scan_id: new_selected_scan_id = None
    return options, new_selected_scan_id


@app.callback(
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn, error = get_db_connection()
    angle, distance, speed = "--°", "-- cm", "-- cm/s"
    if conn:
        try:
            # Sadece 'running' durumundaki en son taramanın son noktasını al
            df_running_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn)
            if not df_running_scan.empty:
                running_scan_id = df_running_scan['id'].iloc[0]
                df = pd.read_sql_query(
                    f"SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points WHERE scan_id = {running_scan_id} ORDER BY id DESC LIMIT 1",
                    conn)
                if not df.empty:
                    angle_val, distance_val, speed_val = df['angle_deg'].iloc[0], df['mesafe_cm'].iloc[0], \
                        df['hiz_cm_s'].iloc[0]
                    angle = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°"
                    distance = f"{distance_val:.1f} cm" if pd.notnull(distance_val) else "-- cm"
                    speed = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except Exception as e:
            print(f"Anlık değerler alınırken hata: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (Anlık Değerler): {error}")
    return angle, distance, speed


@app.callback(
    [Output('scan-map-graph', 'figure'),  # 2D Kartezyen Harita
     Output('polar-graph', 'figure'),  # Polar Grafik
     Output('time-series-graph', 'figure')],  # Zaman Serisi Grafiği
    [Input('interval-component-main', 'n_intervals'),  # Periyodik güncelleme için
     Input('scan-select-dropdown', 'value')]  # Kullanıcının seçtiği tarama ID'si
    # prevent_initial_call=True # KALDIRILDI veya False yapıldı, sayfa yüklenince çalışması için
)
def update_all_graphs(n_intervals, selected_scan_id):
    conn, error_msg_conn = get_db_connection()  # DB bağlantısını helper fonksiyonla al

    # Başlangıçta boş veya hata durumunda gösterilecek figürler
    fig_map_title = '2D Kartezyen Harita (Veri Bekleniyor/Yok)'
    fig_polar_title = 'Polar Grafik (Veri Bekleniyor/Yok)'
    fig_time_title = 'Zaman Serisi - Mesafe (Veri Bekleniyor/Yok)'

    # uirevision, kullanıcı etkileşimlerini (zoom, pan) korumak için kullanılır.
    # Seçili tarama ID'si değiştiğinde grafik sıfırlanır.
    current_uirevision = str(selected_scan_id) if selected_scan_id else "no_scan_selected"

    fig_map = go.Figure().update_layout(title_text=fig_map_title, uirevision=current_uirevision,
                                        plot_bgcolor='rgba(248,248,248,0.95)')
    fig_polar = go.Figure().update_layout(title_text=fig_polar_title, uirevision=current_uirevision,
                                          plot_bgcolor='rgba(248,248,248,0.95)')
    fig_time = go.Figure().update_layout(title_text=fig_time_title, uirevision=current_uirevision,
                                         plot_bgcolor='rgba(248,248,248,0.95)')

    if error_msg_conn:
        print(f"Grafik güncelleme DB Bağlantı Hatası: {error_msg_conn}")
        # Hata durumunda grafiklere hata mesajını yansıt
        fig_map.update_layout(title_text=f'2D Harita ({error_msg_conn})')
        fig_polar.update_layout(title_text=f'Polar Grafik ({error_msg_conn})')
        fig_time.update_layout(title_text=f'Zaman Serisi ({error_msg_conn})')
        return fig_map, fig_polar, fig_time

    current_scan_id_to_plot = selected_scan_id
    # Eğer dropdown'dan bir seçim yapılmadıysa (sayfa ilk yüklendiğinde veya dropdown boşsa)
    # en son taramayı otomatik olarak seçmeye çalış
    if not current_scan_id_to_plot and conn:
        try:
            df_last_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn)
            if not df_last_scan.empty:
                current_scan_id_to_plot = int(df_last_scan['id'].iloc[0])
                print(f"Dash: Seçili tarama yok, en son tarama ID: {current_scan_id_to_plot} kullanılacak.")
        except Exception as e:
            print(f"Dash: En son tarama ID'si alınırken hata: {e}")
            # Hata devam ederse, aşağıdaki current_scan_id_to_plot None kontrolü durumu ele alacak

    if not current_scan_id_to_plot:  # Hala bir tarama ID'si belirlenemediyse
        if conn: conn.close()
        # Kullanıcıya bilgi vermek için grafik başlıkları zaten "Veri Bekleniyor/Yok" şeklinde
        return fig_map, fig_polar, fig_time

    try:
        # Seçilen/en son tarama için bilgileri ve noktaları çek
        df_scan_info = pd.read_sql_query(
            f"SELECT status, start_time FROM servo_scans WHERE id = {current_scan_id_to_plot}", conn)
        df_points = pd.read_sql_query(
            f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {current_scan_id_to_plot} ORDER BY id ASC",
            conn
        )

        scan_status_str = df_scan_info['status'].iloc[0] if not df_scan_info.empty else "Bilinmiyor"
        start_time_epoch = df_scan_info['start_time'].iloc[0] if not df_scan_info.empty else time.time()
        start_time_str = time.strftime('%H:%M:%S (%d-%m-%y)', time.localtime(start_time_epoch))

        title_suffix = f"(ID: {current_scan_id_to_plot}, Başl: {start_time_str}, Dur: {scan_status_str})"

        if not df_points.empty:
            # sensor_script.py'deki max_distance ile uyumlu olmalı
            # ve 0'dan büyük okumalar alınmalı
            max_plot_dist = 200.0
            df_valid = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist)].copy()

            if not df_valid.empty:
                # 1. 2D Kartezyen Harita
                if 'x_cm' in df_valid.columns and 'y_cm' in df_valid.columns:
                    fig_map.add_trace(go.Scatter(
                        x=df_valid['y_cm'], y=df_valid['x_cm'], mode='lines+markers',
                        name='Taranan Sınır',
                        marker=dict(size=5, color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=False),
                        # Ana renk skalası polar'da kalsın
                        line=dict(color='dodgerblue', width=1.5)
                    ))
                    fig_map.add_trace(
                        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                                   name='Sensör'))
                fig_map.update_layout(
                    title_text='2D Kartezyen Harita ' + title_suffix,
                    xaxis_title="Yatay Yayılım (cm)", yaxis_title="İleri Mesafe (cm)",
                    yaxis_scaleanchor="x", yaxis_scaleratio=1,
                    margin=dict(l=40, r=40, t=60, b=40)
                )

                # 2. Polar Grafik
                fig_polar.add_trace(go.Scatterpolar(
                    r=df_valid['mesafe_cm'], theta=df_valid['angle_deg'], mode='lines+markers',
                    name='Mesafe Profili',
                    marker=dict(color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=True,
                                colorbar_title_text="Mesafe (cm)"),
                    line_color='deepskyblue'
                ))
                fig_polar.update_layout(
                    title_text='Polar Grafik ' + title_suffix,
                    polar=dict(radialaxis=dict(visible=True, range=[0, max_plot_dist], ticksuffix=" cm"),
                               angularaxis=dict(direction="clockwise", ticksuffix="°"))
                )

                # 3. Zaman Serisi (Mesafe vs Zaman)
                if 'timestamp' in df_valid.columns:
                    df_time_series = df_valid.sort_values(by='timestamp')
                    # Zaman damgalarını daha okunabilir bir formata çevir
                    time_labels_for_plot = [time.strftime('%H:%M:%S', time.localtime(ts)) for ts in
                                            df_time_series['timestamp']]

                    fig_time.add_trace(go.Scatter(
                        x=time_labels_for_plot,  # X ekseninde formatlanmış zaman etiketleri
                        y=df_time_series['mesafe_cm'],
                        mode='lines+markers', name='Mesafe (cm)',
                        marker_color='green'
                    ))
                    # fig_time.update_xaxes(type='category') # Etiketler string olduğu için kategori olarak belirtilebilir
                fig_time.update_layout(
                    title_text='Zaman Serisi - Mesafe ' + title_suffix,
                    xaxis_title="Ölçüm Zamanı", yaxis_title="Mesafe (cm)",
                    margin=dict(l=40, r=40, t=60, b=40)
                )
            else:  # df_valid boş ise
                fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Geçerli Veri Yok)")
                fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix + " (Geçerli Veri Yok)")
                fig_time.update_layout(title_text='Zaman Serisi ' + title_suffix + " (Geçerli Veri Yok)")
        else:  # df_points boş ise
            fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Nokta Verisi Yok)")
            fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix + " (Nokta Verisi Yok)")
            fig_time.update_layout(title_text='Zaman Serisi ' + title_suffix + " (Nokta Verisi Yok)")

    except Exception as e:
        print(f"Dash: Grafik oluşturma sırasında genel hata: {e}")
        # Hata durumunda da başlıkları güncelleyebiliriz
        fig_map.update_layout(title_text=f'2D Harita (Hata: {str(e)[:50]})')
        fig_polar.update_layout(title_text=f'Polar Grafik (Hata: {str(e)[:50]})')
        fig_time.update_layout(title_text=f'Zaman Serisi (Hata: {str(e)[:50]})')
    finally:
        if conn:
            conn.close()

    return fig_map, fig_polar, fig_time


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'),
     Output('max-width', 'children'), Output('max-depth', 'children')],
    [Input('scan-select-dropdown', 'value')]
)
def update_analysis_panel(selected_scan_id):
    conn, error = get_db_connection()
    area, perimeter, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    if conn and selected_scan_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {selected_scan_id}",
                conn)
            if not df_scan.empty:
                area_val = df_scan['hesaplanan_alan_cm2'].iloc[0]
                perimeter_val = df_scan['cevre_cm'].iloc[0]
                width_val = df_scan['max_genislik_cm'].iloc[0]
                depth_val = df_scan['max_derinlik_cm'].iloc[0]

                area = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "-- cm²"
                perimeter = f"{perimeter_val:.2f} cm" if pd.notnull(perimeter_val) else "-- cm"
                width = f"{width_val:.2f} cm" if pd.notnull(width_val) else "-- cm"
                depth = f"{depth_val:.2f} cm" if pd.notnull(depth_val) else "-- cm"
        except Exception as e:
            print(f"Analiz paneli hatası: {e}")
        finally:
            conn.close()
    elif error:
        print(f"DB Bağlantı Hatası (Analiz): {error}")
    return area, perimeter, width, depth


@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
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

    cpu_percent, ram_percent = 0.0, 0.0
    try:
        if os.path.exists('/proc/stat') and os.path.exists('/proc/meminfo'):  # Sadece Linux'ta çalışır
            # CPU (Basit bir anlık kullanım, daha iyisi psutil ile olur)
            with open('/proc/stat', 'r') as f:
                prev_stat = list(map(int, f.readline().split()[1:5]))
            time.sleep(0.1)
            with open('/proc/stat', 'r') as f:
                curr_stat = list(map(int, f.readline().split()[1:5]))
            prev_total = sum(prev_stat);
            prev_idle = prev_stat[3]
            curr_total = sum(curr_stat);
            curr_idle = curr_stat[3]
            total_delta = curr_total - prev_total
            idle_delta = curr_idle - prev_idle
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
            if 'MemTotal' in mem_info and 'MemAvailable' in mem_info:
                ram_percent = round(100.0 * (mem_info['MemTotal'] - mem_info['MemAvailable']) / mem_info['MemTotal'], 1)
    except Exception as e:
        print(f"CPU/RAM okuma hatası: {e}")
    return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


@app.callback(
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    [State('scan-select-dropdown', 'value')],
    prevent_initial_call=True
)
def export_csv_callback(n_clicks, selected_scan_id):
    if n_clicks is None or selected_scan_id is None: return dash.no_update
    conn, error = get_db_connection()
    if conn:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {selected_scan_id} ORDER BY id ASC",
                                   conn)
            return dcc.send_data_frame(df.to_csv, f"tarama_verileri_id_{selected_scan_id}.csv", index=False)
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (CSV): {error}")
    return dash.no_update


@app.callback(
    Output('download-excel', 'data'),
    [Input('export-excel-button', 'n_clicks')],
    [State('scan-select-dropdown', 'value')],
    prevent_initial_call=True
)
def export_excel_callback(n_clicks, selected_scan_id):
    if n_clicks is None or selected_scan_id is None: return dash.no_update
    conn, error = get_db_connection()
    if conn:
        try:
            df_points = pd.read_sql_query(
                f"SELECT * FROM scan_points WHERE scan_id = {selected_scan_id} ORDER BY id ASC", conn)
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {selected_scan_id}", conn)

            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_points.to_excel(writer, sheet_name=f'Scan_{selected_scan_id}_Points', index=False)
                df_scan_info.to_excel(writer, sheet_name=f'Scan_{selected_scan_id}_Info', index=False)
            excel_buffer.seek(0)
            return dcc.send_bytes(excel_buffer.read(), f"tarama_detaylari_id_{selected_scan_id}.xlsx")
        except Exception as e:
            print(f"Excel indirme hatası: {e}")
        finally:
            conn.close()
    else:
        print(f"DB Bağlantı Hatası (Excel): {error}")
    return dash.no_update
