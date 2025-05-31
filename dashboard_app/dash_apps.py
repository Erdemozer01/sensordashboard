# dashboard_app/dash_apps.py (Tüm Callback'ler Tam ve Düzenlenmiş)
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
import psutil  # Sistem bilgilerini almak için

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

DEFAULT_UI_SCAN_EXTENT_ANGLE = 135  # Merkezden her iki yana taranacak açı
DEFAULT_UI_SCAN_STEP_ANGLE = 10

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- LAYOUT BİLEŞENLERİ (Yanıt #56'daki gibi) ---
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
        dbc.InputGroup([dbc.InputGroupText("Tarama Genişliği (Merkezden ±°)", style={"width": "200px"}),
                        dbc.Input(id="scan-extent-input", type="number", value=DEFAULT_UI_SCAN_EXTENT_ANGLE, min=10,
                                  max=179, step=5)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "200px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])
stats_panel = dbc.Card([
    dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
    dbc.CardBody(html.Div(id='realtime-values'))  # Children callback ile dolacak
], className="mb-3")
system_card = dbc.Card([
    dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
    dbc.CardBody(html.Div(id='system-status-values'))  # Children callback ile dolacak
], className="mb-3")
export_card = dbc.Card([
    dbc.CardHeader("Veri Dışa Aktarma (En Son Tarama)", className="bg-light"),
    dbc.CardBody([
        dbc.Button('En Son Taramayı CSV İndir', id='export-csv-button', color="primary", className="w-100 mb-2"),
        dcc.Download(id='download-csv'),
        dbc.Button('En Son Taramayı Excel İndir', id='export-excel-button', color="success", className="w-100"),
        dcc.Download(id='download-excel'),
    ])
], className="mb-3")
analysis_card = dbc.Card([
    dbc.CardHeader("Tarama Analizi (En Son Tarama)", className="bg-dark text-white"),
    dbc.CardBody(html.Div(id='analysis-output'))  # Children callback ile dolacak
])
visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Harita"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi")
])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col([control_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), stats_panel,
                 dbc.Row(html.Div(style={"height": "15px"})), system_card,
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


def get_latest_scan_id_from_db(conn_param=None):
    print("DEBUG: get_latest_scan_id_from_db çağrıldı.")
    internal_conn = False;
    conn_to_use = conn_param;
    latest_id = None;
    error_msg = None
    if not conn_to_use:
        conn_to_use, error_msg = get_db_connection()
        if error_msg: print(f"DEBUG: get_latest_scan_id_from_db -> get_db_connection hatası: {error_msg}"); return None
        internal_conn = True
    if conn_to_use:
        try:
            df_scan = pd.read_sql_query(
                "SELECT id FROM servo_scans WHERE status = 'running' ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if df_scan.empty:
                df_scan = pd.read_sql_query("SELECT id FROM servo_scans ORDER BY start_time DESC LIMIT 1", conn_to_use)
            if not df_scan.empty:
                latest_id = int(df_scan['id'].iloc[0])
            else:
                print("DEBUG: DB'de hiç tarama kaydı bulunamadı (get_latest_scan_id).")
        except Exception as e:
            print(f"DEBUG: Son tarama ID alınırken hata: {e}")
        finally:
            if internal_conn and conn_to_use: conn_to_use.close()
    return latest_id


# --- CALLBACK FONKSİYONLARI ---

@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('scan-extent-input', 'value'),
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, scan_extent_val, step_angle_val):
    if n_clicks_start is None or n_clicks_start == 0: return dash.no_update
    print(f"Dash: 'Taramayı Başlat' tıklandı. n_clicks: {n_clicks_start}")
    extent_a = scan_extent_val if scan_extent_val is not None else DEFAULT_UI_SCAN_EXTENT_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE
    if not (10 <= extent_a <= 179): return dbc.Alert("Geçersiz tarama genişliği (10-179 arası)!", color="danger")
    if not (1 <= step_a <= 45): return dbc.Alert("Geçersiz adım açısı (1-45 arası)!", color="danger")
    current_pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip()
            if pid_str: current_pid = int(pid_str)
        except:
            current_pid = None
    if current_pid and is_process_running(current_pid): return dbc.Alert(f"Betik çalışıyor (PID: {current_pid}).",
                                                                         color="warning")
    if os.path.exists(LOCK_FILE_PATH_FOR_DASH) and (not current_pid or not is_process_running(current_pid)):
        print(f"Dash: Kalıntı kilit/PID dosyası bulundu. Siliniyor...")
        try:
            if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH)
            if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH)
        except OSError as e:
            return dbc.Alert(f"Kalıntı kilit/PID silinemedi: {e}.", color="danger")
    try:
        python_executable = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(
            f"HATA: Sensör betiği ({SENSOR_SCRIPT_PATH}) bulunamadı!", color="danger")
        cmd = [python_executable, SENSOR_SCRIPT_PATH, "--scan_extent", str(extent_a), "--step_angle", str(step_a)]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, start_new_session=True)
        time.sleep(3.0)  # PID dosyasının oluşması için biraz daha bekle
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf_new:
                    pid_str_new = pf_new.read().strip();
                if pid_str_new: new_pid = int(pid_str_new)
                if new_pid and is_process_running(new_pid):
                    return dbc.Alert(f"Betik başlatıldı (PID: {new_pid}).", color="success", duration=5000)
                else:
                    return dbc.Alert(f"Betik başlatıldı ama PID ({new_pid}) ile process bulunamadı.", color="warning",
                                     duration=5000)
            except Exception as e:
                return dbc.Alert(f"PID okunurken hata ({PID_FILE_PATH_FOR_DASH}): {e}", color="warning", duration=5000)
        else:
            # Betik hemen sonlandı mı diye kontrol et (opsiyonel)
            # poll_status = process.poll()
            # if poll_status is not None:
            #     _, stderr = process.communicate(timeout=1)
            #     return dbc.Alert(f"PID dosyası oluşmadı. Betik erken sonlandı (kod: {poll_status}). Hata: {stderr.decode(errors='ignore')}", color="danger")
            return dbc.Alert(f"PID dosyası ({PID_FILE_PATH_FOR_DASH}) oluşmadı. Betik loglarını kontrol edin.",
                             color="danger", duration=5000)
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken genel hata: {str(e)}", color="danger", duration=5000)
    return dash.no_update


@app.callback(
    Output('scan-status-message', 'children', allow_duplicate=True),
    [Input('stop-scan-button', 'n_clicks')],
    prevent_initial_call=True
)
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
            print(f"Dash: Sensör betiği (PID: {pid_to_kill}) için SIGTERM gönderiliyor...")
            os.kill(pid_to_kill, signal.SIGTERM);
            time.sleep(2.0)
            if is_process_running(pid_to_kill):
                print(f"Dash: SIGTERM yanıt vermedi, SIGKILL gönderiliyor (PID: {pid_to_kill})...")
                os.kill(pid_to_kill, signal.SIGKILL);
                time.sleep(0.5)

            # Dosyaların temizlendiğini varsayalım (atexit yapar)
            if not os.path.exists(PID_FILE_PATH_FOR_DASH) and not os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durduruldu.", color="info", duration=4000)
            else:  # atexit henüz temizlemediyse
                return dbc.Alert(
                    f"Sensör betiği (PID: {pid_to_kill}) durdurma komutu aldı, dosyalar kısa süre içinde temizlenmeli.",
                    color="warning", duration=5000)
        except Exception as e:
            return dbc.Alert(f"Sensör betiği (PID: {pid_to_kill}) durdurulurken hata: {e}", color="danger")
    else:
        msg = "Çalışan betik bulunamadı.";
        cleaned = False
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH): os.remove(LOCK_FILE_PATH_FOR_DASH); cleaned = True
        if os.path.exists(PID_FILE_PATH_FOR_DASH): os.remove(PID_FILE_PATH_FOR_DASH); cleaned = True
        if cleaned: msg += " Kalıntı kilit/PID dosyaları temizlendi."
        return dbc.Alert(msg, color="warning", duration=4000)
    return dash.no_update


@app.callback(
    Output('realtime-values', 'children'),
    [Input('interval-component-main', 'n_intervals')]
)
def update_realtime_values(n_intervals):
    # print(f"--- update_realtime_values tetiklendi (Interval: {n_intervals}) ---") # Çok sık loglar
    conn, error = get_db_connection()
    angle_str, distance_str, speed_str = "--°", "-- cm", "-- cm/s"
    if conn:
        latest_id = get_latest_scan_id_from_db(conn_param=conn)
        if latest_id:
            try:
                df = pd.read_sql_query(
                    f"SELECT angle_deg, mesafe_cm, hiz_cm_s FROM scan_points WHERE scan_id = {latest_id} ORDER BY id DESC LIMIT 1",
                    conn)
                if not df.empty:
                    angle_val, dist_val, speed_val = df.iloc[0]
                    angle_str = f"{angle_val:.0f}°" if pd.notnull(angle_val) else "--°"
                    distance_str = f"{dist_val:.1f} cm" if pd.notnull(dist_val) else "-- cm"
                    speed_str = f"{speed_val:.1f} cm/s" if pd.notnull(speed_val) else "-- cm/s"
            except Exception as e:
                print(f"Anlık değerler alınırken hata: {e}")
        # else: print("Anlık Değerler: Gösterilecek tarama ID'si bulunamadı.")
        if conn: conn.close()  # get_latest_scan_id_from_db conn_param alırsa kapatmaz, burada kapatmalıyız.
    # elif error: print(f"DB Bağlantı Hatası (Anlık Değerler): {error}")
    return [dbc.Row([
        dbc.Col(html.Div([html.H6("Mevcut Açı:"), html.H4(id='current-angle', children=angle_str)]), width=4,
                className="text-center"),
        dbc.Col(html.Div([html.H6("Mevcut Mesafe:"), html.H4(id='current-distance', children=distance_str)]), width=4,
                className="text-center"),
        dbc.Col(html.Div([html.H6("Anlık Hız:"), html.H4(id='current-speed', children=speed_str)]), width=4,
                className="text-center")
    ])]


@app.callback(
    [Output('scan-map-graph', 'figure'), Output('polar-graph', 'figure'), Output('time-series-graph', 'figure')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    # Bu fonksiyonun tam içeriği bir önceki cevabımda (Yanıt #46) bulunmaktadır.
    # Lütfen o cevaptaki tam ve düzeltilmiş fonksiyonu buraya kopyalayın.
    # Temel mantık: get_latest_scan_id_from_db -> veri çek -> 3 grafik oluştur/döndür.
    # (Aşağıda Yanıt #46'daki mantıkla doldurulmuş ve iyileştirilmiş hali)
    print(f"--- update_all_graphs ÇAĞRILDI (Interval No: {n_intervals}) ---")
    conn, error_msg_conn = get_db_connection()
    id_to_plot = None
    if conn: id_to_plot = get_latest_scan_id_from_db(conn_param=conn)

    ui_revision_key = str(id_to_plot if id_to_plot else "no_scan") + f"_main_{n_intervals}"
    base_layout = dict(uirevision=ui_revision_key, plot_bgcolor='rgba(248,248,248,0.95)')
    fig_map = go.Figure().update_layout(title_text='2D Kartezyen Harita (Veri bekleniyor...)', **base_layout)
    fig_polar = go.Figure().update_layout(title_text='Polar Grafik (Veri bekleniyor...)', **base_layout)
    fig_time = go.Figure().update_layout(title_text='Zaman Serisi - Mesafe (Veri bekleniyor...)', **base_layout)

    if error_msg_conn and not conn:
        print(f"Dash Grafik: DB bağlantı hatası: {error_msg_conn}")
        titles = [f'2D Harita (DB Hatası)', f'Polar Grafik (DB Hatası)', f'Zaman Serisi (DB Hatası)']
        for fig, title in zip([fig_map, fig_polar, fig_time], titles): fig.update_layout(title_text=title)
        return fig_map, fig_polar, fig_time

    if not id_to_plot:
        print("Dash Grafik: Çizilecek bir tarama ID'si belirlenemedi.")
        if conn: conn.close();
        return fig_map, fig_polar, fig_time

    df_points = pd.DataFrame();
    df_scan_info = pd.DataFrame()
    scan_status_str = "Bilinmiyor";
    start_time_str = "N/A";
    scan_extent_str = "?";
    scan_step_str = "?"
    if conn:
        try:
            df_scan_info = pd.read_sql_query(
                f"SELECT status, start_time, scan_extent_angle_setting, step_angle_setting FROM servo_scans WHERE id = {id_to_plot}",
                conn)
            if not df_scan_info.empty:
                scan_status_str = df_scan_info['status'].iloc[0]
                start_time_epoch = df_scan_info['start_time'].iloc[0]
                start_time_str = time.strftime('%H:%M:%S (%d-%m-%y)', time.localtime(start_time_epoch))
                scan_extent_str = str(int(df_scan_info['scan_extent_angle_setting'].iloc[0])) if pd.notnull(
                    df_scan_info['scan_extent_angle_setting'].iloc[0]) else "?"
                scan_step_str = str(int(df_scan_info['step_angle_setting'].iloc[0])) if pd.notnull(
                    df_scan_info['step_angle_setting'].iloc[0]) else "?"
            df_points = pd.read_sql_query(
                f"SELECT angle_deg, mesafe_cm, x_cm, y_cm, timestamp FROM scan_points WHERE scan_id = {id_to_plot} ORDER BY id ASC",
                conn)
        except Exception as e:
            print(f"Grafik için DB okuma hatası (ID: {id_to_plot}): {e}")

    title_suffix = f"(ID: {id_to_plot}, +/-{scan_extent_str}°, Adım:{scan_step_str}°, Başl: {start_time_str}, Dur: {scan_status_str})"
    if not df_points.empty:
        max_plot_dist = 200.0
        df_points['mesafe_cm'] = pd.to_numeric(df_points['mesafe_cm'], errors='coerce');
        df_points['x_cm'] = pd.to_numeric(df_points['x_cm'], errors='coerce');
        df_points['y_cm'] = pd.to_numeric(df_points['y_cm'], errors='coerce');
        df_points['angle_deg'] = pd.to_numeric(df_points['angle_deg'], errors='coerce');
        df_points['timestamp'] = pd.to_numeric(df_points['timestamp'], errors='coerce')
        df_valid = df_points[
            (df_points['mesafe_cm'] > 0.1) & (df_points['mesafe_cm'] < max_plot_dist) & df_points['x_cm'].notna() &
            df_points['y_cm'].notna() & df_points['angle_deg'].notna() & df_points['timestamp'].notna()].copy()
        if not df_valid.empty:
            fig_map.add_trace(go.Scatter(x=df_valid['y_cm'], y=df_valid['x_cm'], mode='lines+markers', name='Sınır',
                                         marker=dict(size=5, color=df_valid['mesafe_cm'], colorscale='Viridis',
                                                     showscale=False), line=dict(color='dodgerblue')))
            polygon_plot_x = [0.0] + list(df_valid['y_cm']);
            polygon_plot_y = [0.0] + list(df_valid['x_cm'])
            if len(df_valid) > 1: polygon_plot_x.append(0.0); polygon_plot_y.append(0.0)
            fig_map.add_trace(
                go.Scatter(x=polygon_plot_x, y=polygon_plot_y, fill="toself", fillcolor='rgba(0,176,246,0.2)',
                           line=dict(color='rgba(255,255,255,0)'), showlegend=False))
            fig_map.add_trace(
                go.Scatter(x=[0], y=[0], mode='markers', marker=dict(size=10, symbol='diamond', color='red'),
                           name='Sensör'))
            fig_map.update_layout(title_text='2D Harita ' + title_suffix, xaxis_title="Yatay (cm)",
                                  yaxis_title="İleri (cm)", yaxis_scaleanchor="x", yaxis_scaleratio=1,
                                  margin=dict(l=40, r=40, t=60, b=40))
            fig_polar.add_trace(
                go.Scatterpolar(r=df_valid['mesafe_cm'], theta=df_valid['angle_deg'], mode='lines+markers',
                                name='Mesafe',
                                marker=dict(color=df_valid['mesafe_cm'], colorscale='Viridis', showscale=True,
                                            colorbar_title_text="Mesafe(cm)")))
            fig_polar.update_layout(title_text='Polar Grafik ' + title_suffix,
                                    polar=dict(radialaxis=dict(visible=True, range=[0, max_plot_dist]),
                                               angularaxis=dict(direction="counterclockwise", ticksuffix="°")))
            if 'timestamp' in df_valid.columns:
                df_time = df_valid.sort_values(by='timestamp');
                datetime_series = pd.to_datetime(df_time['timestamp'], unit='s')
                fig_time.add_trace(
                    go.Scatter(x=datetime_series, y=df_time['mesafe_cm'], mode='lines+markers', name='Mesafe (cm)'))
                fig_time.update_xaxes(type='date', tickformat='%H:%M:%S')
            fig_time.update_layout(title_text='Zaman Serisi - Mesafe ' + title_suffix, xaxis_title="Zaman",
                                   yaxis_title="Mesafe (cm)", margin=dict(l=40, r=40, t=60, b=40))
        else:
            for fig, name in zip([fig_map, fig_polar, fig_time],
                                 ["2D Harita", "Polar Grafik", "Zaman Serisi"]): fig.update_layout(
                title_text=name + ' ' + title_suffix + " (Geçerli Veri Yok)")
    else:
        for fig, name in zip([fig_map, fig_polar, fig_time],
                             ["2D Harita", "Polar Grafik", "Zaman Serisi"]): fig.update_layout(
            title_text=name + ' ' + title_suffix + " (Nokta Verisi Yok)")
    if conn: conn.close()
    return fig_map, fig_polar, fig_time


@app.callback(
    [Output('analysis-output', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_analysis_panel(n_intervals):
    conn, error = get_db_connection()
    area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"
    scan_info_text = "Analiz için veri bekleniyor..."
    latest_id = None
    if conn: latest_id = get_latest_scan_id_from_db(conn_param=conn)
    if conn and latest_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm, start_time, status FROM servo_scans WHERE id = {latest_id}",
                conn)
            if not df_scan.empty:
                area_val, per_val, w_val, d_val, st_epoch, status = df_scan.iloc[0]
                st_str = time.strftime('%H:%M:%S', time.localtime(st_epoch))
                scan_info_text = f"Son Analiz (ID: {latest_id}, {st_str}, Durum: {status}):"
                area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı";
                perimeter_str = f"{per_val:.2f} cm" if pd.notnull(per_val) else "Hesaplanmadı"
                width_str = f"{w_val:.2f} cm" if pd.notnull(w_val) else "Hesaplanmadı";
                depth_str = f"{d_val:.2f} cm" if pd.notnull(d_val) else "Hesaplanmadı"
        except Exception as e:
            print(f"Analiz paneli hatası: {e}"); scan_info_text = "Analiz verileri alınırken hata."
    elif error:
        scan_info_text = f"DB Bağlantı Hatası (Analiz): {error}"
    if conn: conn.close()
    analysis_children_content = [html.H5(scan_info_text, style={'marginBottom': '10px'}), dbc.Row(
        [dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(area_str)]),
         dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(perimeter_str)])]), dbc.Row(
        [dbc.Col([html.H6("Max Genişlik:"), html.H4(width_str)]),
         dbc.Col([html.H6("Max Derinlik:"), html.H4(depth_str)])], className="mt-2")]
    return [analysis_children_content]


@app.callback(
    [Output('system-status-values', 'children')],
    [Input('interval-component-system', 'n_intervals')]
)
def update_system_card(n_intervals):
    script_status_text, status_class_name = "Beklemede", "text-secondary";
    pid = None
    if os.path.exists(PID_FILE_PATH_FOR_DASH):
        try:
            with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                pid_str = pf.read().strip();
            if pid_str: pid = int(pid_str)
        except:
            pass
    if pid and is_process_running(pid):
        script_status_text, status_class_name = "Çalışıyor", "text-success"
    else:
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
            script_status_text, status_class_name = "Durum Belirsiz (Kilit Var)", "text-warning"
        else:
            script_status_text, status_class_name = "Çalışmıyor", "text-danger"
    cpu_percent, ram_percent = 0.0, 0.0
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1);
        virtual_mem = psutil.virtual_memory();
        ram_percent = virtual_mem.percent
        cpu_percent = round(max(0, min(100, cpu_percent)), 1);
        ram_percent = round(max(0, min(100, ram_percent)), 1)
    except Exception as e:
        print(f"CPU/RAM (psutil) okuma hatası: {e}")
    system_card_children = [dbc.Row([dbc.Col(
        html.Div([html.H6("Sensör Betiği:"), html.H5(children=script_status_text, className=status_class_name)]))],
                                    className="mb-2"), dbc.Row([dbc.Col(html.Div([html.H6("Pi CPU Kullanımı:"),
                                                                                  dbc.Progress(value=cpu_percent,
                                                                                               label=f"{cpu_percent}%",
                                                                                               color="success",
                                                                                               style={"height": "20px"},
                                                                                               className="mb-1")])),
                                                                dbc.Col(html.Div([html.H6("Pi RAM Kullanımı:"),
                                                                                  dbc.Progress(value=ram_percent,
                                                                                               label=f"{ram_percent}%",
                                                                                               color="info",
                                                                                               style={"height": "20px"},
                                                                                               className="mb-1")]))])]
    return [system_card_children]


@app.callback(Output('download-csv', 'data'), [Input('export-csv-button', 'n_clicks')], prevent_initial_call=True)
def export_csv_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection();
    latest_id = get_latest_scan_id_from_db(conn_param=conn)
    if conn and latest_id:
        try:
            df = pd.read_sql_query(f"SELECT * FROM scan_points WHERE scan_id = {latest_id} ORDER BY id ASC", conn)
            return dcc.send_data_frame(df.to_csv, f"tarama_id_{latest_id}.csv", index=False)
        except Exception as e:
            print(f"CSV indirme hatası: {e}")
        finally:
            conn.close()
    return dash.no_update


@app.callback(Output('download-excel', 'data'), [Input('export-excel-button', 'n_clicks')], prevent_initial_call=True)
def export_excel_callback(n_clicks):
    if n_clicks is None or n_clicks == 0: return dash.no_update
    conn, error = get_db_connection();
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
        finally:
            conn.close()
    return dash.no_update