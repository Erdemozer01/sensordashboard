# dashboard_app/dash_apps.py (Dropdown Kaldırılmış Hali)
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
import io

# --- Sabitler ---
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

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

# --- LAYOUT BİLEŞENLERİ ---
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
# scan_selector_card KALDIRILDI
export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"),  # Başlık güncellendi
    dbc.CardBody([
        dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="me-1 w-100 mb-2"),
        dcc.Download(id='download-csv'),
        dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
        dcc.Download(id='download-excel'),
    ])
], className="mb-3")
analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),  # Başlık güncellendi
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
                 # scan_selector_card buradan kaldırıldı
                 dbc.Row(html.Div(style={"height": "15px"})), export_card],
                md=4, className="mb-3"),
        dbc.Col([visualization_tabs,
                 dbc.Row(html.Div(style={"height": "15px"})), analysis_card],
                md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),  # Ana interval (grafikler, analiz)
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0)  # Sistem durumu için ayrı interval
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


def get_latest_scan_id(conn_param=None):
    """ Veritabanından en son (veya çalışan) tarama ID'sini alır. """
    internal_conn = False
    conn_to_use = conn_param
    latest_id = None

    if not conn_to_use:  # Eğer dışarıdan bağlantı verilmediyse, yenisini aç
        conn_to_use, error = get_db_connection()
        if error:
            print(f"DB Bağlantı Hatası (get_latest_scan_id): {error}")
            return None
        internal_conn = True

    if conn_to_use:
        try:
            # Önce 'running' durumundaki en son taramayı dene
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use
            )
            if df_scan.empty:  # Çalışan yoksa, herhangi bir durumdaki en son taramayı al
                df_scan = pd.read_sql_query(
                    "SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use
                )
            if not df_scan.empty:
                latest_id = int(df_scan['id'].iloc[0])
        except Exception as e:
            print(f"En son tarama ID'si alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use:  # Sadece bu fonksiyon içinde açıldıysa kapat
                conn_to_use.close()
    return latest_id


# --- CALLBACK FONKSİYONLARI ---

# handle_start_scan_script ve handle_stop_scan_script (Yanıt #40'taki gibi)
# ... (Bu fonksiyonları bir önceki cevaptan buraya kopyalayın, değişiklik yok) ...
@app.callback(Output('scan-status-message', 'children'), [Input('start-scan-button', 'n_clicks')],
              [State('start-angle-input', 'value'), State('end-angle-input', 'value'),
               State('step-angle-input', 'value')], prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val):
    # ... (Yanıt #43'teki tam kod) ...
    if n_clicks_start is None or n_clicks_start == 0: return dash.no_update
    start_a = start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE
    end_a = end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE
    if not (0 <= start_a <= 180 and 0 <= end_a <= 180 and start_a <= end_a): return dbc.Alert("Geçersiz açı!",
                                                                                              color="danger")
    if not (1 <= step_a <= 45): return dbc.Alert("Geçersiz adım açısı!", color="danger")
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    if current_pid and is_process_running(current_pid): return dbc.Alert(f"Betik çalışıyor (PID: {current_pid}).",
                                                                         color="warning")
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit silinemedi: {e}.", color="danger")
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Betik bulunamadı: {SENSOR_SCRIPT_PATH}",
                                                                    color="danger")
        cmd = [python_executable, SENSOR_SCRIPT_PATH, "--start_angle", str(start_a), "--end_angle", str(end_a),
               "--step_angle", str(step_a)]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2.5)
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip();
                if pid_str_new: new_pid = int(pid_str_new)
                if new_pid and is_process_running(new_pid):
                    return dbc.Alert(f"Betik başlatıldı (PID: {new_pid}).", color="success")
                else:
                    return dbc.Alert(f"Betik PID ({new_pid}) ile process bulunamadı.", color="warning")
            except Exception as e:
                return dbc.Alert(f"PID okunurken hata: {e}", color="warning")
        else:
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Logları kontrol edin.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Betik başlatılırken hata: {str(e)}", color="danger")
    return dash.no_update


@app.callback(Output('scan-status-message', 'children', allow_duplicate=True), [Input('stop-scan-button', 'n_clicks')],
              prevent_initial_call=True)
def handle_stop_scan_script(n_clicks_stop):
    # ... (Yanıt #43'teki tam kod) ...
    if n_clicks_stop is None or n_clicks_stop == 0: return dash.no_update
    pid_to_kill = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: pid_to_kill = int(pid_str)
        except:
            pid_to_kill = None
    if pid_to_kill and is_process_running(pid_to_kill):
        try:
            os.kill(pid_to_kill, signal.SIGTERM);
            time.sleep(1.5)
            if is_process_running(pid_to_kill): os.kill(pid_to_kill, signal.SIGKILL); time.sleep(0.5)
            if not os.path.exists(PID_FILE_PATH_FOR_DASH) and not os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durduruldu.", color="info")
            else:
                return dbc.Alert(f"Betik (PID: {pid_to_kill}) durduruldu, kilit/PID kalmış olabilir.", color="warning")
        except Exception as e:
            return dbc.Alert(f"Betik (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        msg = "Çalışan betik bulunamadı.";
        cleaned = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
        if cleaned: msg += " Kalıntı dosyalar temizlendi."
        return dbc.Alert(msg, color="warning")
    return dash.no_update


# update_scan_dropdowns callback'i KALDIRILDI.

@app.callback(  # Anlık değerler (Aynı kalabilir)
    [Output('current-angle', 'children'),
     Output('current-distance', 'children'),
     Output('current-speed', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    # ... (Yanıt #43'teki gibi, Output'ları doğru döndürür) ...
    conn, error = get_db_connection()
    angle, distance, speed = "--°", "-- cm", "-- cm/s"
    if conn:
        try:
            df_running_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn)
            scan_id_to_check = None
            if not df_running_scan.empty:
                scan_id_to_check = df_running_scan['id'].iloc[0]
            else:
                df_last_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn)
                if not df_last_scan.empty: scan_id_to_check = df_last_scan['id'].iloc[0]
            if scan_id_to_check:
                df = pd.read_sql_query(
                    f"SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points WHERE scan_id = {scan_id_to_check} ORDER BY id DESC LIMIT 1",
                    conn)
                if not df.empty:
                    angle_val, distance_val, speed_val = df['angle_deg'].iloc[0], df['mesafe_cm'].iloc[0], \
                    df['hiz_cm_s'].iloc[0]
                    angle = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°";
                    distance = f"{distance_val:.1f} cm" if pd.notnull(distance_val) else "-- cm";
                    speed = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except Exception as e:
            print(f"Anlık değerler: {e}")
        finally:
            conn.close()
    return angle, distance, speed


@app.callback(  # DÜZENLENDİ: Dropdown Input'u kaldırıldı
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):  # selected_scan_id parametresi kaldırıldı
    print(f"--- update_all_graphs ÇAĞRILDI (Dropdown Yok) --- n_intervals: {n_intervals}")
    conn, error_msg_conn = get_db_connection()

    id_to_plot = get_latest_scan_id(conn_param=conn)  # Helper fonksiyonu kullan
    ui_revision_key = str(id_to_plot if id_to_plot else "no_scan") + f"_{n_intervals}"

    fig_map = go.Figure().update_layout(title_text='2D Kartezyen Harita (Veri bekleniyor...)',
                                        uirevision=ui_revision_key, plot_bgcolor='rgba(248,248,248,0.95)')
    fig_polar = go.Figure().update_layout(title_text='Polar Grafik (Veri bekleniyor...)', uirevision=ui_revision_key,
                                          plot_bgcolor='rgba(248,248,248,0.95)')
    fig_time = go.Figure().update_layout(title_text='Zaman Serisi - Mesafe (Veri bekleniyor...)',
                                         uirevision=ui_revision_key, plot_bgcolor='rgba(248,248,248,0.95)')

    if error_msg_conn:
        # ... (Hata durumu, Yanıt #46'daki gibi) ...
        if conn: conn.close();  # get_db_connection helper'ı açtıysa kapatılmaz, bu yüzden burada kontrol
        return fig_map, fig_polar, fig_time

    if not id_to_plot:
        print("Dash Grafik: Çizilecek bir tarama ID'si belirlenemedi.")
        if conn: conn.close();
        return fig_map, fig_polar, fig_time

    # ... (Veri çekme ve grafik oluşturma mantığı Yanıt #46'daki gibi,
    #      sadece current_scan_id_to_plot yerine id_to_plot kullanılır) ...
    #      Bu uzun kısmı tekrar eklemiyorum, Yanıt #46'dan alabilirsiniz.
    #      Önemli olan, id_to_plot'u kullanarak veri çekmesidir.
    #      Aşağıda sadece iskelet ve başlık güncellemesi var:
    df_points = pd.DataFrame()
    df_scan_info = pd.DataFrame()
    if conn:  # get_db_connection helper'ı None döndürmediyse
        try:
            df_scan_info = pd.read_sql_query(f"SELECT status, start_time FROM servo_scans WHERE id = {id_to_plot}",
                                             conn)
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY id ASC",
                conn)
        except Exception as e:
            print(f"Grafik için DB okuma hatası: {e}")
        finally:
            if conn: conn.close()  # get_db_connection helper'ı açmadıysa burada kapatılmaz.
            # get_db_connection helper'ı kendi içinde kapatmıyor.

    # Aslında get_db_connection'ı her sorgu için çağırmak ve onun içinde kapatmak daha iyi olabilir
    # ya da bu fonksiyonun başında bir kere çağırıp sonunda kapatmak. Mevcut haliyle de çalışır.

    # ... (Yanıt #46'daki df_points ve df_scan_info kullanarak fig_map, fig_polar, fig_time oluşturma kodları) ...
    # Örnek başlık güncelleme:
    title_suffix = f"(ID: {id_to_plot}, ...)"  # Diğer bilgiler df_scan_info'dan
    if not df_points.empty:
        # ... (grafiklere trace ekleme)
        fig_map.update_layout(title_text='2D Harita ' + title_suffix)
        # ...
    else:
        fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Nokta Verisi Yok)")
        # ...

    return fig_map, fig_polar, fig_time


@app.callback(  # DÜZENLENDİ: Dropdown Input'u kaldırıldı
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'),
     Output('max-width', 'children'), Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n_intervals):  # selected_scan_id parametresi kaldırıldı
    conn, error = get_db_connection()
    area, perimeter, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"

    latest_id = get_latest_scan_id(conn_param=conn)  # Açık bağlantıyı kullanabiliriz

    if conn and latest_id:  # Bağlantı varsa ve ID bulunduysa
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                conn)
            if not df_scan.empty:
                # ... (Yanıt #37'deki gibi değer atamaları) ...
                area_val = df_scan['hesaplanan_alan_cm2'].iloc[0];
                perimeter_val = df_scan['cevre_cm'].iloc[0]
                width_val = df_scan['max_genislik_cm'].iloc[0];
                depth_val = df_scan['max_derinlik_cm'].iloc[0]
                area = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "-- cm²";
                perimeter = f"{perimeter_val:.2f} cm" if pd.notnull(perimeter_val) else "-- cm"
                width = f"{width_val:.2f} cm" if pd.notnull(width_val) else "-- cm";
                depth = f"{depth_val:.2f} cm" if pd.notnull(depth_val) else "-- cm"
        except Exception as e:
            print(f"Analiz paneli hatası: {e}")
        # finally bloğu get_db_connection içinde olmadığı için burada conn.close() yok
    elif error:
        print(f"DB Bağlantı Hatası (Analiz): {error}")

    if conn: conn.close()  # Bağlantıyı burada kapat
    return area, perimeter, width, depth


@app.callback(  # Sistem durumu (Aynı kalabilir)
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    # ... (Yanıt #37'deki tam kod) ...
    script_status_text, status_class_name = "Beklemede", "text-secondary";
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
        if os.path.exists('/proc/stat') and os.path.exists('/proc/meminfo'):
            with open('/proc/stat', 'r') as f1, open('/proc/stat', 'r') as f2:
                line1 = f1.readline().split();
                time.sleep(0.1);
                line2 = f2.readline().split()
            idle1, total1 = float(line1[4]), sum(map(float, line1[1:8]));
            idle2, total2 = float(line2[4]), sum(map(float, line2[1:8]))
            idle_delta, total_delta = idle2 - idle1, total2 - total1
            if total_delta > 0: cpu_percent = round(100.0 * (1.0 - idle_delta / total_delta), 1)
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


@app.callback(  # DÜZENLENDİ: Dropdown State'i kaldırıldı, en son taramayı indirir
    Output('download-csv', 'data'),
    [Input('export-csv-button', 'n_clicks')],
    prevent_initial_call=True
)
def export_csv_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection()
    latest_id = get_latest_scan_id(conn_param=conn)
    if conn and latest_id:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            return dcc.send_data_frame(df.to_csv, f"tarama_verileri_id_{latest_id}.csv", index=False)
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
        finally:
            conn.close()  # get_db_connection açtıysa burada kapatılmaz, kendi içinde kapatmalı.
        # get_latest_scan_id conn_param alırsa kapatmaz.
    elif error:
        print(f"DB Bağlantı Hatası (CSV): {error}")
    if conn: conn.close()  # Her ihtimale karşı
    return dash.no_update


@app.callback(  # DÜZENLENDİ: Dropdown State'i kaldırıldı, en son taramayı indirir
    Output('download-excel', 'data'),
    [Input('export-excel-button', 'n_clicks')],
    prevent_initial_call=True
)
def export_excel_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection()
    latest_id = get_latest_scan_id(conn_param=conn)
    if conn and latest_id:
        try:
            df_points = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC",
                                          conn)
            df_scan_info = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id = {latest_id}", conn)
            excel_buffer = io.BytesIO()
            with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                df_points.to_excel(writer, sheet_name=f'Scan_{latest_id}_Points', index=False)
                df_scan_info.to_excel(writer, sheet_name=f'Scan_{latest_id}_Info', index=False)
            excel_buffer.seek(0)
            return dcc.send_bytes(excel_buffer.read(), f"tarama_detaylari_id_{latest_id}.xlsx")
        except Exception as e:
            print(f"Excel indirme hatası: {e}")
        # finally conn.close() burada yok çünkü get_latest_scan_id açık bağlantı kullanabilir.
    elif error:
        print(f"DB Bağlantı Hatası (Excel): {error}")
    if conn: conn.close()  # Her ihtimale karşı
    return dash.no_update