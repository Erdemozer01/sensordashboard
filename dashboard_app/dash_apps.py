from django_plotly_dash import DjangoDash

from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import matplotlib.pyplot as plt

import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time
import io
import signal
import psutil
import numpy as np
from scipy.spatial import ConvexHull
from simplification.cutil import simplify_coords
from sklearn.linear_model import RANSACRegressor
from sklearn.cluster import DBSCAN

# ==============================================================================
# --- SABİTLER VE UYGULAMA BAŞLATMA ---
# ==============================================================================
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()

DB_FILENAME = 'live_scan_data.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
# Sensör betiğinin kullandığı PID ve Lock dosyalarının tam yolları
# Bu yolların sensör betiğindeki tanımlarla aynı olması KRİTİKTİR.
SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_START_ANGLE = 0
DEFAULT_UI_SCAN_END_ANGLE = 180
DEFAULT_UI_SCAN_STEP_ANGLE = 10
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
title_card = dbc.Row(
    [dbc.Col(html.H1("Dream Pi Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()])

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
                                  max=359, step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Bitiş Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="end-angle-input", type="number", value=DEFAULT_UI_SCAN_END_ANGLE, min=0, max=359,
                                  step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "120px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1,
                                  max=45, step=0.1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Buzzer Mes. (cm)", style={"width": "120px"}),
                        dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0,
                                  max=200, step=1)], className="mb-2"),
        dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=DEFAULT_UI_INVERT_MOTOR,
                     className="mt-2 mb-2"),
    ])
])

stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"), dbc.CardBody(dbc.Row(
    [dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children="--°")]), width=3,
             className="text-center border-end"),
     dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children="-- cm")]),
             id='current-distance-col', width=3, className="text-center rounded border-end"),
     dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children="-- cm/s")]), width=3,
             className="text-center border-end"),
     dbc.Col(html.Div([html.H6("Max. Algılanan Mesafe:"), html.H4(id='max-detected-distance', children="-- cm")]),
             width=3, className="text-center")]))], className="mb-3")
system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"), dbc.CardBody(
    [dbc.Row([dbc.Col(html.Div([html.H6("Sensör Betiği Durumu:"), html.H5(id='script-status', children="Beklemede")]))],
             className="mb-2"), dbc.Row([dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                                                           dbc.Progress(id='cpu-usage', value=0, color="success",
                                                                        style={"height": "20px"}, className="mb-1",
                                                                        label="0%")])), dbc.Col(html.Div(
        [html.H6("Pi RAM Kullanımı:"),
         dbc.Progress(id='ram-usage', value=0, color="info", style={"height": "20px"}, className="mb-1",
                      label="0%")]))])])], className="mb-3")
export_card = dbc.Card([dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"), dbc.CardBody(
    [dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
     dcc.Download(id='download-csv'),
     dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
     dcc.Download(id='download-excel'), ])], className="mb-3")
analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
                          dbc.CardBody([dbc.Row(
                              [dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area', children="-- cm²")]),
                               dbc.Col(
                                   [html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length', children="-- cm")])]),
                                        dbc.Row([dbc.Col(
                                            [html.H6("Max Genişlik:"), html.H4(id='max-width', children="-- cm")]),
                                                 dbc.Col([html.H6("Max Derinlik:"),
                                                          html.H4(id='max-depth', children="-- cm")])],
                                                className="mt-2")])])
estimation_card = dbc.Card([dbc.CardHeader("Akıllı Ortam Analizi", className="bg-success text-white"), dbc.CardBody(
    html.Div("Tahmin: Bekleniyor...", id='environment-estimation-text', className="text-center"))])
visualization_tabs = dbc.Tabs(
    [dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita", tab_id="tab-map"),
     dbc.Tab(dcc.Graph(id='polar-regression-graph', style={'height': '75vh'}), label="Regresyon Analizi",
             tab_id="tab-regression"),
     dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik", tab_id="tab-polar"),
     dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)",
             tab_id="tab-time"),
     dbc.Tab(dcc.Loading(id="loading-datatable", children=[html.Div(id='tab-content-datatable')]), label="Veri Tablosu",
             tab_id="tab-datatable")], id="visualization-tabs-main", active_tab="tab-map", )

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel, dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card, dbc.Row(html.Div(style={"height": "15px"})),
                 export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})),
                 dbc.Row([dbc.Col(analysis_card, md=8), dbc.Col(estimation_card, md=4)])], md=8)
    ]),
    dcc.Store(id='clustered-data-store'),
    dbc.Modal([dbc.ModalHeader(dbc.ModalTitle(id="modal-title")), dbc.ModalBody(id="modal-body")],
              id="cluster-info-modal", is_open=False, centered=True),
    dcc.Interval(id='interval-component-main', interval=3000, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# ==============================================================================
def is_process_running(pid):
    if pid is None: return False
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return False


def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5)
        return conn, None
    except sqlite3.OperationalError as e:
        return None, f"DB Kilitli/Hata: {e}"
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):
    internal_conn, conn_to_use, latest_id = False, conn_param, None
    if not conn_to_use:
        conn_to_use, error = get_db_connection()
        if error: return None
        internal_conn = True
    if conn_to_use:
        try:
            df_r = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_r.empty:
                latest_id = int(df_r['id'].iloc[0])
            else:
                df_l = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)
                if not df_l.empty: latest_id = int(df_l['id'].iloc[0])
        except Exception as e:
            print(f"Son tarama ID alınırken hata: {e}"); latest_id = None
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


def add_scan_rays(fig, df):
    x_lines, y_lines = [], []
    for _, row in df.iterrows(): x_lines.extend([0, row['y_cm'], None]); y_lines.extend([0, row['x_cm'], None])
    fig.add_trace(
        go.Scatter(x=x_lines, y=y_lines, mode='lines', line=dict(color='rgba(255,100,100,0.4)', dash='dash', width=1),
                   showlegend=False))


def add_sector_area(fig, df):
    poly_x, poly_y = df['y_cm'].tolist(), df['x_cm'].tolist()
    fig.add_trace(
        go.Scatter(x=[0] + poly_x, y=[0] + poly_y, mode='lines', fill='toself', fillcolor='rgba(255,0,0,0.15)',
                   line=dict(color='rgba(255,0,0,0.4)'), name='Taranan Sektör'))


def add_sensor_position(fig):
    fig.add_trace(
        go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=12, symbol='circle', color='red'), name='Sensör'))


def update_polar_graph(fig, df):
    fig.add_trace(go.Scatterpolar(r=df['mesafe_cm'], theta=df['derece'], mode='lines+markers', name='Mesafe'))
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 250]), angularaxis=dict(direction="clockwise", period=360)))


def update_time_series_graph(fig, df):
    df_s = df.sort_values(by='timestamp')
    fig.add_trace(go.Scatter(x=pd.to_datetime(df_s['timestamp'], unit='s'), y=df_s['mesafe_cm'], mode='lines+markers',
                             name='Mesafe'))
    fig.update_layout(xaxis_title="Zaman", yaxis_title="Mesafe (cm)")


def find_clearest_path(df_valid):
    if df_valid.empty: return "En açık yol için veri yok."
    try:
        df_filtered = df_valid[df_valid['mesafe_cm'] > 0]
        if df_filtered.empty: return "Geçerli pozitif mesafe bulunamadı."
        cp = df_filtered.loc[df_filtered['mesafe_cm'].idxmax()]
        return f"En Açık Yol: {cp['derece']:.1f}° yönünde, {cp['mesafe_cm']:.1f} cm."
    except Exception as e:
        return f"En açık yol hesaplanamadı: {e}"


def analyze_polar_regression(df_valid):
    if len(df_valid) < 5: return None, "Polar regresyon için yetersiz veri."
    X, y = df_valid[['derece']].values, df_valid['mesafe_cm'].values
    try:
        ransac = RANSACRegressor(random_state=42);
        ransac.fit(X, y)
        slope = ransac.estimator_.coef_[0]
        inf = f"Yüzey dairesel/paralel (Eğim:{slope:.3f})" if abs(slope) < 0.1 else (
            f"Yüzey açı arttıkça uzaklaşıyor (Eğim:{slope:.3f})" if slope > 0 else f"Yüzey açı arttıkça yaklaşıyor (Eğim:{slope:.3f})")
        xr = np.array([df_valid['derece'].min(), df_valid['derece'].max()]).reshape(-1, 1)
        return {'x': xr.flatten(), 'y': ransac.predict(xr)}, "Polar Regresyon: " + inf + " cm/derece."
    except Exception as e:
        return None, f"Polar regresyon hatası: {e}"


def analyze_environment_shape(fig, df_valid):
    points_all = df_valid[['y_cm', 'x_cm']].to_numpy()
    if len(points_all) < 10:
        df_valid['cluster'] = -2
        return "Analiz için yetersiz veri.", df_valid

    db = DBSCAN(eps=15, min_samples=3).fit(points_all)
    labels = db.labels_
    df_valid['cluster'] = labels

    desc = []
    unique_clusters = set(labels)
    num_actual_clusters = len(unique_clusters - {-1})

    if num_actual_clusters > 0:
        desc.append(f"{num_actual_clusters} potansiyel nesne kümesi bulundu.")
    else:
        desc.append("Belirgin bir nesne kümesi bulunamadı (DBSCAN).")

    num_colors_for_map = len(unique_clusters) if len(unique_clusters) > 0 else 1
    colors = plt.cm.get_cmap('viridis', num_colors_for_map)

    for k_label in unique_clusters:
        cluster_points_to_plot = points_all[labels == k_label]
        if k_label == -1:
            color_val, point_size, name_val = 'rgba(128,128,128,0.3)', 5, 'Gürültü/Diğer'
        else:
            num_non_noise_clusters_for_norm = len(unique_clusters - {-1})
            if num_non_noise_clusters_for_norm > 1:
                norm_k = k_label / (num_non_noise_clusters_for_norm - 1)
            elif num_non_noise_clusters_for_norm == 1:
                norm_k = 0.0
            else:
                norm_k = 0.0
            norm_k = np.clip(norm_k, 0.0, 1.0)
            raw_col = colors(norm_k)
            color_val = f'rgba({raw_col[0] * 255:.0f},{raw_col[1] * 255:.0f},{raw_col[2] * 255:.0f},0.9)'
            point_size, name_val = 8, f'Küme {k_label}'

        fig.add_trace(go.Scatter(
            x=cluster_points_to_plot[:, 0], y=cluster_points_to_plot[:, 1], mode='markers',
            marker=dict(color=color_val, size=point_size), name=name_val,
            customdata=[k_label] * len(cluster_points_to_plot)
        ))
    return " ".join(desc), df_valid


# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('start-angle-input', 'value'), State('end-angle-input', 'value'),
     State('step-angle-input', 'value'), State('buzzer-distance-input', 'value'),
     State('invert-motor-checkbox', 'value')],
    prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, start_angle_val, end_angle_val, step_angle_val, buzzer_distance_val,
                             invert_motor_val):
    if n_clicks_start == 0: return no_update
    start_a, end_a, step_a, buzzer_d, invert_dir = \
        (start_angle_val if start_angle_val is not None else DEFAULT_UI_SCAN_START_ANGLE), \
            (end_angle_val if end_angle_val is not None else DEFAULT_UI_SCAN_END_ANGLE), \
            (step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE), \
            (buzzer_distance_val if buzzer_distance_val is not None else DEFAULT_UI_BUZZER_DISTANCE), \
            bool(invert_motor_val)

    if not (0 <= start_a <= 359 and 0 <= end_a <= 359): return dbc.Alert("Açılar 0-359 arasında olmalı!",
                                                                         color="danger", duration=None)
    if not (0.1 <= abs(step_a) <= 45): return dbc.Alert("Adım açısı 0.1-45 arasında olmalı!", color="danger",
                                                        duration=None)
    if not (0 <= buzzer_d <= 200): return dbc.Alert("Buzzer mesafesi 0-200cm arasında olmalı!", color="danger",
                                                    duration=None)

    pid = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_str = pf.read().strip(); pid = int(pid_str) if pid_str else None
        except:
            pid = None
    if pid and is_process_running(pid): return dbc.Alert(f"Sensör betiği çalışıyor (PID:{pid}). Önce durdurun.",
                                                         color="warning", duration=None)

    for fp in [SENSOR_SCRIPT_LOCK_FILE, SENSOR_SCRIPT_PID_FILE]:
        if os.path.exists(fp):
            try:
                os.remove(fp)
            except OSError as e:
                return dbc.Alert(f"Kalıntı dosya ({fp}) silinemedi: {e}.", color="danger", duration=None)
    try:
        py_exec = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}",
                                                                    color="danger", duration=None)
        cmd = [py_exec, SENSOR_SCRIPT_PATH, "--start_angle", str(start_a), "--end_angle", str(end_a), "--step_angle",
               str(step_a), "--buzzer_distance", str(buzzer_d), "--invert_motor_direction", str(invert_dir)]
        log_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log')
        with open(log_path, 'w') as log_f:
            subprocess.Popen(cmd, start_new_session=True, stdout=log_f, stderr=log_f)
        time.sleep(2.5)
        if os.path.exists(SENSOR_SCRIPT_PID_FILE):
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf_new:
                new_pid = pf_new.read().strip()
            return dbc.Alert(
                f"Sensör betiği başlatıldı (PID:{new_pid}). Yön Ters Çevirme: {'Aktif' if invert_dir else 'Pasif'}",
                color="success")
        else:
            log_disp = f"PID dosyası ({SENSOR_SCRIPT_PID_FILE}) oluşmadı. "
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f_log:
                        lines = "".join(f_log.readlines()[-10:]);log_disp_detail = (lines[:500] + '...') if len(
                            lines) > 500 else lines
                    if log_disp_detail.strip():
                        log_disp += "Logdan son satırlar:"; return dbc.Alert([html.Span(log_disp), html.Br(),
                                                                              html.Pre(log_disp_detail,
                                                                                       style={'whiteSpace': 'pre-wrap',
                                                                                              'maxHeight': '150px',
                                                                                              'overflowY': 'auto',
                                                                                              'fontSize': '0.8em',
                                                                                              'backgroundColor': '#f0f0f0',
                                                                                              'border': '1px solid #ccc',
                                                                                              'padding': '5px'})],
                                                                             color="danger", duration=None)
                    else:
                        log_disp += f"'{os.path.basename(log_path)}' boş."
                except Exception as e_l:
                    log_disp += f"Log okuma hatası: {e_l}"
            else:
                log_disp += f"Log dosyası ('{os.path.basename(log_path)}') bulunamadı."
            return dbc.Alert(log_disp, color="danger", duration=None)
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatma hatası: {e}", color="danger", duration=None)


@app.callback(Output('scan-status-message', 'children', allow_duplicate=True), [Input('stop-scan-button', 'n_clicks')],
              prevent_initial_call=True)
def handle_stop_scan_script(n):
    if n == 0: return no_update
    pid_kill = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_s = pf.read().strip()
                pid_kill = int(pid_s) if pid_s else None
        except (IOError, ValueError):
            pid_kill = None
            print(f"PID dosyası ({SENSOR_SCRIPT_PID_FILE}) okunurken hata oluştu veya dosya boş.")

    if pid_kill and is_process_running(pid_kill):
        try:
            os.kill(pid_kill, signal.SIGTERM);
            time.sleep(2)
            if is_process_running(pid_kill): os.kill(pid_kill, signal.SIGKILL);time.sleep(0.5)
            if not is_process_running(pid_kill):
                for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
                    if os.path.exists(fp): os.remove(fp)
                return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) durduruldu.", color="info")
            else:
                return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) durdurulamadı!", color="danger", duration=None)
        except ProcessLookupError:
            for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
                if os.path.exists(fp): os.remove(fp)
            return dbc.Alert(f"Sensör betiği (PID:{pid_kill}) zaten çalışmıyordu.", color="warning")
        except Exception as e:
            return dbc.Alert(f"Sensör betiği durdurma hatası:{e}", color="danger", duration=None)
    else:
        for fp in [SENSOR_SCRIPT_PID_FILE, SENSOR_SCRIPT_LOCK_FILE]:
            if os.path.exists(fp):
                try:
                    os.remove(fp)
                except OSError as e_rem:
                    print(f"Kalıntı dosya ({fp}) silinirken hata: {e_rem}")
        if pid_kill is not None:
            return dbc.Alert(f"Sensör betiği (PID: {pid_kill}) çalışmıyor.", color="warning")
        else:
            return dbc.Alert("Çalışan sensör betiği bulunamadı veya PID dosyası okunamadı.", color="warning")


@app.callback(
    [Output('current-angle', 'children'), Output('current-distance', 'children'), Output('current-speed', 'children'),
     Output('current-distance-col', 'style'), Output('max-detected-distance', 'children')],
    [Input('interval-component-main', 'n_intervals')])
def update_realtime_values(n):
    conn, err = get_db_connection();
    angle_s, dist_s, speed_s, max_dist_s = "--°", "-- cm", "-- cm/s", "-- cm";
    dist_style = {'padding': '10px', 'transition': 'background-color 0.5s ease', 'borderRadius': '5px'}
    if err: return angle_s, dist_s, speed_s, dist_style, max_dist_s
    if conn:
        try:
            lid = get_latest_scan_id_from_db(conn)
            if lid:
                q_curr = f"SELECT mesafe_cm,derece,hiz_cm_s FROM scan_points WHERE scan_id={lid} ORDER BY id DESC LIMIT 1";
                df_p = pd.read_sql_query(q_curr, conn)
                q_set = f"SELECT buzzer_distance_setting FROM servo_scans WHERE id={lid}";
                df_set = pd.read_sql_query(q_set, conn)
                b_thr = float(df_set['buzzer_distance_setting'].iloc[0]) if not df_set.empty and pd.notnull(
                    df_set['buzzer_distance_setting'].iloc[0]) else None
                if not df_p.empty:
                    d, a, s = df_p['mesafe_cm'].iloc[0], df_p['derece'].iloc[0], df_p['hiz_cm_s'].iloc[0]
                    angle_s, dist_s, speed_s = f"{a:.1f}°" if pd.notnull(a) else "--°", f"{d:.1f} cm" if pd.notnull(
                        d) else "-- cm", f"{s:.1f} cm/s" if pd.notnull(s) else "-- cm/s"
                    if b_thr is not None and pd.notnull(d) and d <= b_thr: dist_style.update(
                        {'backgroundColor': '#d9534f', 'color': 'white'})
                q_max = f"SELECT MAX(mesafe_cm) as max_dist FROM scan_points WHERE scan_id={lid} AND mesafe_cm<250 AND mesafe_cm>0";
                df_max = pd.read_sql_query(q_max, conn)
                if not df_max.empty and pd.notnull(
                    df_max['max_dist'].iloc[0]): max_dist_s = f"{df_max['max_dist'].iloc[0]:.1f} cm"
        except Exception as e:
            print(f"Anlık değer/max mesafe hata:{e}")
        finally:
            conn.close()
    return angle_s, dist_s, speed_s, dist_style, max_dist_s


@app.callback(
    [Output('calculated-area', 'children'), Output('perimeter-length', 'children'), Output('max-width', 'children'),
     Output('max-depth', 'children')], [Input('interval-component-main', 'n_intervals')])
def update_analysis_panel(n):
    conn, err = get_db_connection();
    area, perim, width, depth = "-- cm²", "-- cm", "-- cm", "-- cm"
    if err: return area, perim, width, depth
    if conn:
        try:
            lid = get_latest_scan_id_from_db(conn)
            if lid:
                df_s = pd.read_sql_query(
                    f"SELECT hesaplanan_alan_cm2,cevre_cm,max_genislik_cm,max_derinlik_cm FROM servo_scans WHERE id={lid}",
                    conn)
                if not df_s.empty:
                    r = df_s.iloc[0];
                    area = f"{r['hesaplanan_alan_cm2']:.2f} cm²" if pd.notnull(r['hesaplanan_alan_cm2']) else "N/A";
                    perim = f"{r['cevre_cm']:.2f} cm" if pd.notnull(r['cevre_cm']) else "N/A";
                    width = f"{r['max_genislik_cm']:.2f} cm" if pd.notnull(r['max_genislik_cm']) else "N/A";
                    depth = f"{r['max_derinlik_cm']:.2f} cm" if pd.notnull(r['max_derinlik_cm']) else "N/A"
        except Exception as e:
            print(f"Analiz panel DB hata:{e}")
        finally:
            conn.close()
    return area, perim, width, depth


@app.callback([Output('script-status', 'children'), Output('script-status', 'className'), Output('cpu-usage', 'value'),
               Output('cpu-usage', 'label'), Output('ram-usage', 'value'), Output('ram-usage', 'label')],
              [Input('interval-component-system', 'n_intervals')])
def update_system_card(n):
    stat_txt, stat_cls = "Beklemede", "text-secondary";
    pid_v = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_s = pf.read().strip();pid_v = int(pid_s) if pid_s else None
        except:
            pass
    if pid_v and is_process_running(pid_v):
        stat_txt, stat_cls = f"Çalışıyor (PID:{pid_v})", "text-success"
    else:
        stat_txt, stat_cls = "Çalışmıyor", "text-danger"
    cpu, ram = psutil.cpu_percent(.1), psutil.virtual_memory().percent
    return stat_txt, stat_cls, cpu, f"{cpu:.1f}%", ram, f"{ram:.1f}%"


@app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')], prevent_initial_call=True)
def export_csv_callback(n):
    if n == 0: return no_update;conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid: df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id={lid} ORDER BY id ASC", conn)
        if not df.empty: return dcc.send_data_frame(df.to_csv, f"tarama_id_{lid}_noktalar.csv", index=False)
    except Exception as e:
        print(f"CSV export hata:{e}")
    finally:
        if conn: conn.close()
    return no_update


@app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')], prevent_initial_call=True)
def export_excel_callback(n):
    if n == 0: return no_update;conn, _ = get_db_connection()
    if not conn: return no_update
    try:
        lid = get_latest_scan_id_from_db(conn)
        if lid:
            df_p = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id={lid} ORDER BY id ASC", conn);
            df_i = pd.read_sql_query(f"SELECT * FROM servo_scans WHERE id={lid}", conn)
            if df_p.empty and df_i.empty: return no_update
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine='xlsxwriter') as writer:
                if not df_p.empty: df_p.to_excel(writer, sheet_name=f'Scan_{lid}_Points', index=False)
                if not df_i.empty: df_i.to_excel(writer, sheet_name=f'Scan_{lid}_Info', index=False)
            buf.seek(0);
            return dcc.send_bytes(buf.read(), f"tarama_detaylari_id_{lid}.xlsx")
    except Exception as e:
        print(f"Excel export hata:{e}")
    finally:
        if conn: conn.close()
    return no_update


@app.callback(Output('tab-content-datatable', 'children'),
              [Input('visualization-tabs-main', 'active_tab'), Input('interval-component-main', 'n_intervals')])
def render_and_update_data_table(active_tab, n):
    if active_tab == "tab-datatable":
        conn, err = get_db_connection()
        if not conn: return dbc.Alert(f"DB bağlantı hatası:{err}", color="danger")
        try:
            lid = get_latest_scan_id_from_db(conn)
            if not lid: return html.P("Görüntülenecek veri yok.")
            q = f"SELECT id,derece,mesafe_cm,hiz_cm_s,x_cm,y_cm,timestamp FROM scan_points WHERE scan_id={lid} ORDER BY id DESC";
            df = pd.read_sql_query(q, conn)
            if df.empty: return html.P(f"Tarama ID {lid} için veri yok.")
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s').dt.strftime('%Y-%m-%d %H:%M:%S.%f').str[:-3]
            return dash_table.DataTable(data=df.to_dict('records'),
                                        columns=[{"name": i.replace("_", " ").title(), "id": i} for i in df.columns],
                                        style_cell={'textAlign': 'left', 'padding': '5px'},
                                        style_header={'backgroundColor': 'rgb(230,230,230)', 'fontWeight': 'bold'},
                                        style_table={'height': '70vh', 'overflowY': 'auto', 'overflowX': 'auto'},
                                        page_size=20, sort_action="native", filter_action="native")
        except Exception as e:
            return dbc.Alert(f"Tablo hatası:{e}", color="danger")
        finally:
            if conn: conn.close()
    return None


@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-regression-graph', 'figure'), Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'), Output('environment-estimation-text', 'children'),
     Output('clustered-data-store', 'data')], [Input('interval-component-main', 'n_intervals')])
def update_all_graphs(n):
    figs = [go.Figure() for _ in range(4)];
    est_cart, est_polar, clear_path = "Veri...", "Veri...", "";
    id_plot, conn, store_data = None, None, None
    try:
        conn, err_conn = get_db_connection()
        if conn and not err_conn:
            id_plot = get_latest_scan_id_from_db(conn)
            if id_plot:
                df_pts = pd.read_sql_query(
                    f"SELECT x_cm,y_cm,derece,mesafe_cm,timestamp FROM scan_points WHERE scan_id={id_plot} ORDER BY derece ASC",
                    conn)
                if not df_pts.empty:
                    df_val = df_pts[(df_pts['mesafe_cm'] > 1.0) & (df_pts['mesafe_cm'] < 250.0)].copy()
                    if len(df_val) >= 2:
                        add_sensor_position(figs[0]);
                        add_scan_rays(figs[0], df_val);
                        add_sector_area(figs[0], df_val)
                        est_cart, df_clus = analyze_environment_shape(figs[0], df_val)
                        store_data = df_clus.to_json(orient='split')
                        line_data, est_polar = analyze_polar_regression(df_val)
                        figs[1].add_trace(
                            go.Scatter(x=df_val['derece'], y=df_val['mesafe_cm'], mode='markers', name='Noktalar'))
                        if line_data: figs[1].add_trace(
                            go.Scatter(x=line_data['x'], y=line_data['y'], mode='lines', name='Regresyon',
                                       line=dict(color='red', width=3)))
                        clear_path = find_clearest_path(df_val);
                        update_polar_graph(figs[2], df_val);
                        update_time_series_graph(figs[3], df_val)
                    else:
                        est_cart = "Analiz için yetersiz geçerli nokta.";add_sensor_position(figs[0])
                else:
                    est_cart = f"Tarama ID {id_plot} için nokta yok.";add_sensor_position(figs[0])
            else:
                est_cart = "Tarama başlatın.";add_sensor_position(figs[0])
        else:
            est_cart = f"DB Bağlantı Hatası:{err_conn}";add_sensor_position(figs[0])
    except Exception as e:
        import traceback;print(
            f"KRİTİK HATA:Grafikleme:{e}\n{traceback.format_exc()}");est_cart = f"Kritik Grafikleme Hatası:{e}";add_sensor_position(
            figs[0])
    finally:
        if conn: conn.close()
    titles = ['Ortamın 2D Haritası (Analizli)', 'Açıya Göre Mesafe Regresyonu', 'Polar Grafik',
              'Zaman Serisi - Mesafe'];
    common_legend = dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                         bgcolor="rgba(255,255,255,0.7)", bordercolor="Black", borderwidth=1)
    for i, fig in enumerate(figs):
        fig.update_layout(title_text=titles[i], uirevision=id_plot or 'initial_load', legend=common_legend)
        if i == 0:
            fig.update_layout(xaxis_title="Yatay Mesafe (cm)", yaxis_title="Dikey Mesafe (cm)", yaxis_scaleanchor="x",
                              yaxis_scaleratio=1)
        elif i == 1:
            fig.update_layout(xaxis_title="Açı (Derece)", yaxis_title="Mesafe (cm)")
    final_est_text = html.Div(
        [html.P(clear_path, className="fw-bold text-primary", style={'fontSize': '1.1em'}), html.Hr(), html.P(est_cart),
         html.Hr(), html.P(est_polar)])
    return figs[0], figs[1], figs[2], figs[3], final_est_text, store_data


@app.callback(
    [Output("cluster-info-modal", "is_open"), Output("modal-title", "children"), Output("modal-body", "children")],
    [Input("scan-map-graph", "clickData")], [State("clustered-data-store", "data")], prevent_initial_call=True, )
def display_cluster_info(clickData, stored_data):
    if not clickData or not stored_data: return False, no_update, no_update
    try:
        df_clus = pd.read_json(stored_data, orient='split')
        if 'cluster' not in df_clus.columns: return False, "Hata", "Küme verisi ('cluster' sütunu) bulunamadı."
        pt_data = clickData["points"][0];
        custom_label = pt_data.get('customdata')
        if custom_label is not None:
            cl_label = custom_label
        else:
            clk_x, clk_y = pt_data["x"], pt_data["y"]
            dists = np.sqrt((df_clus['y_cm'] - clk_x) ** 2 + (df_clus['x_cm'] - clk_y) ** 2)
            if dists.empty: return False, "Hata", "En yakın nokta mesafesi hesaplanamadı."
            cl_label = df_clus.loc[dists.idxmin()]['cluster']
        if cl_label == -1:
            title, body = "Gürültü Noktası", "Bu nokta gürültü olarak sınıflandırıldı."
        elif cl_label == -2:
            title, body = "Analiz Yapılamadı", "Bu bölge için analiz yapılamadı (yetersiz veri)."
        else:
            cl_df = df_clus[df_clus['cluster'] == cl_label];
            n_pts = len(cl_df)
            w = (cl_df['y_cm'].max() - cl_df['y_cm'].min()) if n_pts > 0 else 0;
            d = (cl_df['x_cm'].max() - cl_df['x_cm'].min()) if n_pts > 0 else 0
            title = f"Küme #{int(cl_label)} Detayları";
            body = html.Div([html.P(f"Nokta Sayısı:{n_pts}"), html.P(f"Yaklaşık Genişlik:{w:.1f} cm"),
                             html.P(f"Yaklaşık Derinlik:{d:.1f} cm")])
        return True, title, body
    except Exception as e:
        import traceback;print(
            f"Modal HATA:{e}\n{traceback.format_exc()}");return True, "Hata", f"Küme bilgisi gösterilemedi:{e}"

