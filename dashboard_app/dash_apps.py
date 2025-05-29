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

# --- LAYOUT BİLEŞENLERİ (Dropdown kaldırıldı) ---
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
stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
                        dbc.CardBody([html.Div(id='realtime-values')])], className="mb-3")
system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
                        dbc.CardBody([html.Div(id='system-status-values')])], className="mb-3")
# scan_selector_card KALDIRILDI
export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody(
    [dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
     dcc.Download(id='download-csv'),
     dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
     dcc.Download(id='download-excel')])], className="mb-3")
analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
                          dbc.CardBody(html.Div(id='analysis-output'))])
visualization_tabs = dbc.Tabs([dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Harita"),
                               dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
                               dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}),
                                       label="Zaman Serisi")])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card, dbc.Row(html.Div(style={"height": "15px"})),
                 export_card], md=4, className="mb-3"),  # scan_selector_card kaldırıldı
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})), analysis_card], md=8)
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


def get_latest_scan_id_from_db(conn_param=None):  # Aynı
    internal_conn = False;
    conn_to_use = conn_param;
    latest_id = None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error: print(f"DB Hatası (get_latest_scan_id): {error}"); return None
        internal_conn = True
    if conn_to_use:
        try:
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if df_scan.empty: df_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1",
                                                          conn_to_use)
            if not df_scan.empty: latest_id = int(df_scan['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


# --- CALLBACK FONKSİYONLARI ---
# handle_start_scan_script ve handle_stop_scan_script (Yanıt #43'teki gibi)
@app.callback(Output('scan-status-message', 'children'), [Input('start-scan-button', 'n_clicks')],
              [State('start-angle-input', 'value'), State('end-angle-input', 'value'),
               State('step-angle-input', 'value')], prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val):
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
            time.sleep(2.0)
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

@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    conn, error = get_db_connection()
    angle, distance, speed = "--°", "-- cm", "-- cm/s"
    if conn:
        try:
            latest_id = get_latest_scan_id_from_db(conn_param=conn)
            if latest_id:
                df = pd.read_sql_query(
                    f"SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC LIMIT 1",
                    conn)
                if not df.empty:
                    angle_val, dist_val, speed_val = df['angle_deg'].iloc[0], df['mesafe_cm'].iloc[0], \
                    df['hiz_cm_s'].iloc[0]
                    angle = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°";
                    distance = f"{dist_val:.1f} cm" if pd.notnull(dist_val) else "-- cm";
                    speed = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
        except Exception as e:
            print(f"Anlık değerler: {e}")
        finally:
            conn.close()
    return angle, distance, speed


@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-graph', 'figure'), Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    # Bu fonksiyonun tam ve güncel hali bir önceki cevabımda (Yanıt #46) bulunmaktadır.
    # O cevaptaki fonksiyonu (selected_scan_id parametresi olmadan,
    # en son taramayı kendi bulan versiyonunu) buraya kopyalayın.
    # Kısa olması için burada sadece iskeletini bırakıyorum.
    # ÖNEMLİ: Yanıt #46'daki update_all_graphs fonksiyonu selected_scan_id alıyordu.
    # Onu, selected_scan_id yerine her zaman get_latest_scan_id_from_db() kullanacak şekilde
    # düzenlemeniz gerekecek. Aşağıda bu düzenlenmiş hali var:

    print(f"--- update_all_graphs ÇAĞRILDI (Dropdown Yok) --- n_intervals: {n_intervals}")
    conn, error_msg_conn = get_db_connection()
    id_to_plot = get_latest_scan_id_from_db(conn_param=conn)
    ui_revision_key = str(id_to_plot if id_to_plot else "no_scan") + f"_{n_intervals}"

    fig_map = go.Figure().update_layout(title_text='2D Kartezyen Harita (Veri bekleniyor...)',
                                        uirevision=ui_revision_key, plot_bgcolor='rgba(248,248,248,0.95)')
    fig_polar = go.Figure().update_layout(title_text='Polar Grafik (Veri bekleniyor...)', uirevision=ui_revision_key,
                                          plot_bgcolor='rgba(248,248,248,0.95)')
    fig_time = go.Figure().update_layout(title_text='Zaman Serisi - Mesafe (Veri bekleniyor...)',
                                         uirevision=ui_revision_key, plot_bgcolor='rgba(248,248,248,0.95)')

    if error_msg_conn and not conn:
        print(f"Dash Grafik Güncelleme: DB bağlantı hatası: {error_msg_conn}")
        # Hata mesajlarını grafik başlıklarına yansıt
        if conn: conn.close();  # Bu bloğa girerse conn zaten None olur ama yine de kontrol
        return fig_map, fig_polar, fig_time

    if not id_to_plot:
        print("Dash Grafik: Çizilecek bir tarama ID'si belirlenemedi.")
        if conn: conn.close();
        return fig_map, fig_polar, fig_time

    df_points = pd.DataFrame();
    df_scan_info = pd.DataFrame()
    if conn:
        try:
            df_scan_info = pd.read_sql_query(f"SELECT status, start_time FROM servo_scans WHERE id = {id_to_plot}",
                                             conn)
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY id ASC",
                conn)
        except Exception as e:
            print(f"Grafik için DB okuma hatası: {e}")
        # finally: conn.close() # Bağlantıyı en sonda kapatacağız

    scan_status_str = df_scan_info['status'].iloc[0] if not df_scan_info.empty else "Bilinmiyor"
    start_time_epoch = df_scan_info['start_time'].iloc[0] if not df_scan_info.empty else time.time()
    start_time_str = time.strftime('%H:%M:%S (%d-%m-%y)', time.localtime(start_time_epoch))
    title_suffix = f"(ID: {id_to_plot}, Başl: {start_time_str}, Dur: {scan_status_str})"

    if not df_points.empty:
        max_plot_dist = 200.0
        df_valid = df_points[(df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist)].copy()
        if not df_valid.empty and 'x_cm' in df_valid.columns and 'y_cm' in df_valid.columns:
            fig_map.add_trace(go.Scatter(x=df_valid['y_cm'], y=df_valid['x_cm'], mode='lines+markers', name='Sınır',
                                         marker=dict(size=5, color=df_valid['mesafe_cm'], colorscale='Viridis',
                                                     showscale=False), line=dict(color='dodgerblue')))
            polygon_plot_x = [0] + list(df_valid['y_cm']);
            polygon_plot_y = [0] + list(df_valid['x_cm'])
            if len(df_valid) > 1: polygon_plot_x.append(0); polygon_plot_y.append(0)
            fig_map.add_trace(
                go.Scatter(x=polygon_plot_x, y=polygon_plot_y, fill="toself", fillcolor='rgba(0,176,246,0.2)',
                           line=dict(color='rgba(255,255,255,0)'), showlegend=False))
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör'))
            fig_map.update_layout(title_text='2D Harita ' + title_suffix, xaxis_title="Yatay (cm)",
                                  yaxis_title="İleri (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1)

            fig_polar.add_trace(
                go.Scatterpolar(r=df_valid['mesafe_cm'], theta=df_valid['angle_deg'], mode='lines+markers',
                                name='Mesafe',
                                marker=dict(color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=True,
                                            colorbar_title_text="Mesafe(cm)")))
            fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix,
                                    polar=dict(radialaxis=dict(visible=True, range=[0, max_plot_dist]),
                                               angularaxis=dict(direction="clockwise", ticksuffix="°")))

            if 'timestamp' in df_valid.columns:
                df_time = df_valid.sort_values(by='timestamp');
                datetime_series = pd.to_datetime(df_time['timestamp'], unit='s')
                fig_time.add_trace(
                    go.Scatter(x=datetime_series, y=df_time['mesafe_cm'], mode='lines+markers', name='Mesafe (cm)'))
                fig_time.update_xaxes(type='date', tickformat='%H:%M:%S')
            fig_time.update_layout(title_text='Zaman Serisi - Mesafe ' + title_suffix, xaxis_title="Zaman",
                                   yaxis_title="Mesafe (cm)")
        else:  # df_valid boşsa
            fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Geçerli Veri Yok)");
            fig_polar.update_layout(title_text='Polar ' + title_suffix + " (Geçerli Veri Yok)");
            fig_time.update_layout(title_text='Zaman S. ' + title_suffix + " (Geçerli Veri Yok)")
    else:  # df_points boşsa
        fig_map.update_layout(title_text='2D Harita ' + title_suffix + " (Nokta Verisi Yok)");
        fig_polar.update_layout(title_text='Polar ' + title_suffix + " (Nokta Verisi Yok)");
        fig_time.update_layout(title_text='Zaman S. ' + title_suffix + " (Nokta Verisi Yok)")

    if conn: conn.close()  # En sonda bağlantıyı kapat
    return fig_map, fig_polar, fig_time


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'),
     Output('max-width', 'children'), Output('max-depth', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n_intervals):
    conn, error = get_db_connection()
    area, perimeter, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    latest_id = get_latest_scan_id_from_db(conn_param=conn)
    if conn and latest_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                conn)
            if not df_scan.empty:
                area_val, per_val, w_val, d_val = df_scan.iloc[0]
                area = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "-- cm²";
                perimeter = f"{per_val:.2f} cm" if pd.notnull(per_val) else "-- cm"
                width = f"{w_val:.2f} cm" if pd.notnull(w_val) else "-- cm";
                depth = f"{d_val:.2f} cm" if pd.notnull(d_val) else "-- cm"
        except Exception as e:
            print(f"Analiz paneli hatası: {e}")
    elif error:
        print(f"DB Bağlantı Hatası (Analiz): {error}")
    if conn: conn.close()
    return area, perimeter, width, depth


@app.callback(
    [Output('script-status', 'children'), Output('script-status', 'className'),
     Output('cpu-usage', 'value'), Output('cpu-usage', 'label'),
     Output('ram-usage', 'value'), Output('ram-usage', 'label')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    # ... (Yanıt #40'taki tam kod) ...
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
        if sys.platform == "linux" and os.path.exists('/proc/stat') and os.path.exists('/proc/meminfo'):
            with open('/proc/stat', 'r') as f1:
                stat_start = list(map(int, f1.readline().split()[1:8]))
            time.sleep(0.1);
            with open('/proc/stat', 'r') as f2:
                stat_end = list(map(int, f2.readline().split()[1:8]))
            diff_idle = stat_end[3] - stat_start[3];
            diff_total = sum(stat_end) - sum(stat_start)
            if diff_total > 0: cpu_percent = round(100.0 * (1.0 - diff_idle / total_delta), 1)
            mem_info = {};
            with open('/proc/meminfo', 'r') as f_mem:
                for line in f_mem: parts = line.split(':'); key = parts[0]; value = parts[1].strip();
                if value.endswith('kB'): value = float(value[:-2].strip()) * 1024
                mem_info[key] = value
            if 'MemTotal' in mem_info and 'MemAvailable' in mem_info: ram_percent = round(
                100.0 * (mem_info['MemTotal'] - mem_info['MemAvailable']) / mem_info['MemTotal'], 1)
    except Exception as e:
        print(f"CPU/RAM okuma hatası: {e}")
    return script_status_text, status_class_name, cpu_percent, f"{cpu_percent}%", ram_percent, f"{ram_percent}%"


@app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')], prevent_initial_call=True)
def export_csv_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection()
    latest_id = get_latest_scan_id_from_db(conn_param=conn)
    if conn and latest_id:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            return dcc.send_data_frame(df.to_csv, f"en_son_tarama_id_{latest_id}.csv", index=False)
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
    elif error:
        print(f"DB Bağlantı Hatası (CSV): {error}")
    if conn: conn.close()
    return dash.no_update


@app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')], prevent_initial_call=True)
def export_excel_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection()
    latest_id = get_latest_scan_id_from_db(conn_param=conn)
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
    elif error:
        print(f"DB Bağlantı Hatası (Excel): {error}")
    if conn: conn.close()
    return dash.no_update