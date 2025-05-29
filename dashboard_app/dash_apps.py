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
import signal

# --- Sabitler ---
PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock'
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'
DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ (Yanıt #37'deki gibi) ---
# ... (title_card, control_panel, stats_panel, system_card, scan_selector_card, export_card, analysis_card, visualization_tabs)
# Bu bileşenlerin tanımları bir önceki cevaptaki (#37) gibi kalacak.
# Onları tekrar yazmak yerine sadece ana app.layout'u gösteriyorum, içleri aynı varsayılır.
# Eğer bu bileşenlerin kodlarını da isterseniz belirtebilirsiniz.

title_card = dbc.Row([dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)])
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
], className="mb-3")  # Altına boşluk ekle
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
        dbc.Button('Seçili Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
        dcc.Download(id='download-excel'),
    ])
], className="mb-3")
analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi", className="bg-dark text-white"),
    dbc.CardBody(html.Div(id='analysis-output', children=[  # Varsayılan içerik
        dbc.Row([
            dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
            dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])
        ]),
        dbc.Row([
            dbc.Col([html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
            dbc.Col([html.H6("Max Derinlik:"), html.H4(id='max-depth', children="-- cm")])
        ])
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
def is_process_running(pid):  # Aynı
    if pid is None: return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    else:
        return True


def get_db_connection():  # Aynı
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


# --- CALLBACK FONKSİYONLARI ---
# (handle_start_scan_script, handle_stop_scan_script, update_scan_dropdowns,
#  update_realtime_values, update_all_graphs, update_analysis_panel,
#  update_system_card, export_csv_callback, export_excel_callback)
# Bu callback fonksiyonlarının tam ve güncel halleri bir önceki cevabımda (Yanıt #37) bulunmaktadır.
# Lütfen o cevaptaki tüm callback fonksiyonlarını buraya kopyalayın.
# Kısa olması için sadece en çok değişen handle_start_scan_script'i ve stop'u tekrar ekliyorum:

@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'),
     State('end-angle-input', 'value'),
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val):
    if n_clicks_start is None or n_clicks_start == 0:  # Butona tıklanmadıysa (prevent_initial_call olsa da)
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
        except:
            current_pid = None  # Hata durumunda None ata

    if current_pid and is_process_running(current_pid):
        return dbc.Alert(f"Sensör betiği zaten çalışıyor (PID: {current_pid}).", color="warning", duration=4000)

    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        print(f"Dash: Kalıntı kilit/PID dosyası bulundu. Siliniyor...")
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e_rm_lock:
            return dbc.Alert(f"Kalıntı kilit/PID dosyası silinirken hata: {e_rm_lock}. Lütfen manuel kontrol edin.",
                             color="danger")

    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"HATA: Sensör betiği ({SENSOR_SCRIPT_PATH}) bulunamadı!", color="danger")

        cmd = [
            python_executable, SENSOR_SCRIPT_PATH,
            "--start_angle", str(start_a), "--end_angle", str(end_a), "--step_angle", str(step_a)
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
                    return dbc.Alert(f"Sensör betiği başlatıldı (PID: {new_pid}).", color="success")
                else:
                    return dbc.Alert(f"Sensör betiği başlatıldı ama PID ({new_pid}) ile process bulunamadı.",
                                     color="warning")
            except Exception as e_pid_read:
                return dbc.Alert(f"PID okunurken hata ({PID_FILE_PATH_FOR_DASH}): {e_pid_read}", color="warning")
        else:
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını kontrol edin.",
                             color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken genel hata: {str(e)}", color="danger")
    return dash.no_update


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),  # allow_duplicate=True önemli!
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
            time.sleep(1.5)
            if is_process_running(pid_to_kill):
                print(f"Dash: Sensör betiği (PID: {pid_to_kill}) SIGTERM'e yanıt vermedi, SIGKILL gönderiliyor...")
                os.kill(pid_to_kill, signal.SIGKILL)
                # Kilit ve PID dosyalarını burada da temizle (eğer atexit çalışmazsa diye)
                if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
                if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) zorla durduruldu (SIGKILL).", color="warning")
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durduruldu (SIGTERM).", color="info")
        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        msg = "Çalışan bir sensör betiği bulunamadı."
        # Kalıntı dosyaları temizle
        cleaned = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
        if cleaned: msg += " Kalıntı kilit/PID dosyaları temizlendi."
        return dbc.Alert(msg, color="warning")


# === Diğer Callback Fonksiyonları (update_scan_dropdowns, update_realtime_values, vb.) ===
# Lütfen bu fonksiyonların tam ve güncel hallerini bir önceki tam kod cevabınızdan (Yanıt #37)
# veya kendi en son çalışan versiyonlarınızdan buraya dikkatlice kopyalayın.
# Özellikle veritabanı sorgularının ve döndürdükleri Output'ların
# layout'unuzdaki ID'lerle eşleştiğinden emin olun.
# Örnek olarak, grafik güncelleme callback'inin iskeleti:

@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals'),
     Input('scan-select-dropdown', 'value')]
)
def update_all_graphs(n_intervals, selected_scan_id):
    # Bu fonksiyonun tam içeriği için Yanıt #37'ye bakın.
    # Temel mantık:
    # 1. get_db_connection() ile DB'ye bağlan.
    # 2. selected_scan_id yoksa en sonuncuyu al.
    # 3. df_points ve df_scan_info'yu çek.
    # 4. fig_map, fig_polar, fig_time oluştur ve güncelle.
    # 5. Bu figürleri döndür.
    # Hata durumlarını ve boş veri durumlarını ele al.
    # (Placeholder, tam kodu eklemelisiniz)
    return go.Figure(), go.Figure(), go.Figure()

# Diğer callback'ler de benzer şekilde eklenecek...
# update_scan_dropdowns
# update_realtime_values
# update_analysis_panel
# update_system_card
# export_csv_callback
# export_excel_callback