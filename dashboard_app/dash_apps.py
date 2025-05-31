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
import psutil

# --- Sabitler ---
try:
    PROJECT_ROOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
except NameError:
    PROJECT_ROOT_DIR = os.getcwd()
DB_FILENAME = 'live_scan_data.sqlite3';
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)
SENSOR_SCRIPT_FILENAME = 'sensor_script.py';
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)
LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.lock';
PID_FILE_PATH_FOR_DASH = '/tmp/sensor_scan_script.pid'

DEFAULT_UI_SCAN_EXTENT_ANGLE = 135  # Merkezden her iki yana taranacak açı
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
        dbc.InputGroup([dbc.InputGroupText("Tarama Genişliği (Merkezden ±°)", style={"width": "200px"}),
                        dbc.Input(id="scan-extent-input", type="number", value=DEFAULT_UI_SCAN_EXTENT_ANGLE, min=10,
                                  max=179, step=5)], className="mb-2"),  # Değişti
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "200px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=1, max=45,
                                  step=1)], className="mb-2"),
    ])
])
# ... (stats_panel, system_card, export_card, analysis_card, visualization_tabs - Yanıt #50'deki gibi) ...
# (Bu bileşenlerin tanımları bir önceki tam kod cevabınızdaki (#50) gibi kalabilir.)
stats_panel = dbc.Card([dbc.CardHeader("Anlık Sensör Değerleri", className="bg-info text-white"),
                        dbc.CardBody([html.Div(id='realtime-values')])], className="mb-3")
system_card = dbc.Card([dbc.CardHeader("Sistem Durumu", className="bg-secondary text-white"),
                        dbc.CardBody([html.Div(id='system-status-values')])], className="mb-3")
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
                 export_card], md=4, className="mb-3"),
        dbc.Col([visualization_tabs, dbc.Row(html.Div(style={"height": "15px"})), analysis_card], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=1500, n_intervals=0),
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# --- HELPER FONKSİYONLAR (Yanıt #50'deki gibi) ---
# ... (is_process_running, get_db_connection, get_latest_scan_id_from_db) ...
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
        if not os.path.exists(DB_PATH): return None, f"DB dosyası ({DB_PATH}) yok."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5);
        return conn, None
    except Exception as e:
        return None, f"DB Bağlantı Hatası: {e}"


def get_latest_scan_id_from_db(conn_param=None):  # Aynı
    internal_conn = False;
    conn_to_use = conn_param;
    latest_id = None
    if not conn_to_use: conn_to_use, error = get_db_connection();
    if error and not conn_to_use: print(f"DB Hatası (get_latest_scan_id): {error}"); return None
    internal_conn = (not conn_param and conn_to_use)
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
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [State('scan-extent-input', 'value'),  # Değişti
     State('step-angle-input', 'value')],
    prevent_initial_call=True
)
def handle_start_scan_script(n_clicks_start, scan_extent_val, step_angle_val):
    if n_clicks_start is None or n_clicks_start == 0: return dash.no_update

    extent_a = scan_extent_val if scan_extent_val is not None else DEFAULT_UI_SCAN_EXTENT_ANGLE
    step_a = step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE

    if not (10 <= extent_a <= 179):  # Örnek sınırlar
        return dbc.Alert("Geçersiz tarama genişliği (10-179 arası olmalı)!", color="danger")
    if not (1 <= step_a <= 45):
        return dbc.Alert("Geçersiz adım açısı (1-45 arası olmalı)!", color="danger")

    # PID ve Kilit Kontrolü (Aynı)
    # ... (Yanıt #50'deki gibi) ...
    current_pid = None;  # ... (PID/Lock kontrolü)
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
        if not os.path.exists(SENSOR_SCRIPT_PATH):
            return dbc.Alert(f"HATA: Sensör betiği ({SENSOR_SCRIPT_PATH}) bulunamadı!", color="danger")

        # sensor_script.py'ye yeni argümanları gönder
        cmd = [
            python_executable, SENSOR_SCRIPT_PATH,
            "--scan_extent", str(extent_a),  # Yeni argüman adı
            "--step_angle", str(step_a)
        ]
        print(f"Dash: Betik başlatılıyor: {' '.join(cmd)}")
        process = subprocess.Popen(cmd, start_new_session=True)
        time.sleep(2.5)
        # ... (PID dosyası kontrolü ve geri bildirim - Yanıt #50'deki gibi) ...
        if os.path.exists(PID_FILE_PATH_FOR_DASH):
            new_pid = None;
            try:
                with open(PID_FILE_PATH_FOR_DASH, 'r') as pf:
                    pid_str = pf.read().strip()
                if pid_str: new_pid = int(pid_str)
                if new_pid and is_process_running(new_pid): return dbc.Alert(f"Betik başlatıldı (PID: {new_pid}).",
                                                                             color="success")
            except:
                pass
            return dbc.Alert(f"Betik PID ({new_pid}) ile process bulunamadı/okunamadı.", color="warning")
        else:
            return dbc.Alert(f"PID dosyası oluşmadı.", color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatılırken genel hata: {str(e)}", color="danger")
    return dash.no_update


@app.callback(
    [Output('calculated-area', 'children'),  # 1. Output
     Output('perimeter-length', 'children'),  # 2. Output
     Output('max-width', 'children'),  # 3. Output
     Output('max-depth', 'children')],  # 4. Output
    [Input('interval-component-main', 'n_intervals')]
    # Dropdown kaldırıldığı için selected_scan_id Input'u artık yok
)
def update_analysis_panel(n_intervals):
    print(f"--- update_analysis_panel tetiklendi --- n_intervals: {n_intervals}")  # Debug
    conn, error = get_db_connection()
    area_str, perimeter_str, width_str, depth_str = "-- cm²", "-- cm", "-- cm", "-- cm"  # Varsayılanlar

    latest_id = None
    if conn:  # Sadece bağlantı varsa ID almayı dene
        latest_id = get_latest_scan_id_from_db(conn_param=conn)

    print(f"Analiz için kullanılacak Tarama ID: {latest_id}")  # Debug

    if conn and latest_id:
        try:
            df_scan = pd.read_sql_query(
                f"SELECT hesaplanan_alan_cm2, cevre_cm, max_genislik_cm, max_derinlik_cm FROM servo_scans WHERE id = {latest_id}",
                conn
            )
            print(f"Analiz için çekilen df_scan verisi (ID: {latest_id}):\n{df_scan.to_string()}")  # Debug
            if not df_scan.empty:
                area_val = df_scan['hesaplanan_alan_cm2'].iloc[0]
                per_val = df_scan['cevre_cm'].iloc[0]
                w_val = df_scan['max_genislik_cm'].iloc[0]
                d_val = df_scan['max_derinlik_cm'].iloc[0]

                area_str = f"{area_val:.2f} cm²" if pd.notnull(area_val) else "Hesaplanmadı"
                perimeter_str = f"{per_val:.2f} cm" if pd.notnull(per_val) else "Hesaplanmadı"
                width_str = f"{w_val:.2f} cm" if pd.notnull(w_val) else "Hesaplanmadı"
                depth_str = f"{d_val:.2f} cm" if pd.notnull(d_val) else "Hesaplanmadı"
                print(
                    f"İşlenmiş analiz değerleri: Alan={area_str}, Çevre={perimeter_str}, Genişlik={width_str}, Derinlik={depth_str}")  # Debug
            else:
                print(f"Analiz: Tarama ID {latest_id} için servo_scans tablosunda veri yok veya sütunlar eksik.")
        except Exception as e:
            print(f"Analiz paneli DB sorgu hatası (ID: {latest_id}): {e}")
            area_str, perimeter_str, width_str, depth_str = "Hata", "Hata", "Hata", "Hata"
        # finally: conn.close() # get_latest_scan_id_from_db kendi bağlantısını açıp kapattıysa bu gereksiz
        # get_db_connection burada açtıysa en sonda kapatılmalı.
    elif error:  # get_db_connection'dan hata geldiyse
        print(f"DB Bağlantı Hatası (Analiz Paneli): {error}")
        area_str, perimeter_str, width_str, depth_str = "DB Yok", "DB Yok", "DB Yok", "DB Yok"
    else:  # Ne bağlantı var ne de hata mesajı (get_db_connection None döndürdüyse ve latest_id de None ise)
        print("Analiz: Gösterilecek tarama ID'si bulunamadı (DB bağlantısı sonrası).")

    # Layout'ta analysis-output ID'li Div'in children'ını güncelliyoruz.
    # Bu children, daha önce layout'ta tanımladığımız Row/Col yapısını içermeli.
    analysis_children_content = [
        dbc.Row([
            dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(children=area_str)]),
            # id='calculated-area' kaldırıldı, doğrudan children
            dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(children=perimeter_str)])
        ]),
        dbc.Row([
            dbc.Col([html.H6("Max Genişlik:"), html.H4(children=width_str)]),
            dbc.Col([html.H6("Max Derinlik:"), html.H4(children=depth_str)])
        ], className="mt-2")
    ]
    print(f"Analiz paneli için döndürülen children: {analysis_children_content}")

    # Bu callback artık doğrudan analysis-output div'inin children'ını güncelliyor.
    # Output('calculated-area', 'children') gibi ayrı ayrı Output'lar yerine tek bir Output var.
    # Eğer layout'unuzda 'calculated-area', 'perimeter-length' vb. ID'lere sahip
    # ayrı H4'ler varsa ve onları ayrı ayrı güncellemek istiyorsanız, Output tanımı ve return
    # bir önceki cevaptaki (#50) gibi olmalı.
    # Şimdiki layout'unuzda (Yanıt #50'den gelen):
    # analysis_card = dbc.Card([dbc.CardHeader(...), dbc.CardBody(html.Div(id='analysis-output'))])
    # Bu durumda, analysis-output'un children'ını yukarıdaki `analysis_children_content` ile güncellemek doğru.

    if conn: conn.close()  # Eğer bu fonksiyon içinde açıldıysa bağlantıyı kapat
    return [analysis_children_content]  # Tek bir Output olduğu için tek bir değer listesi

# handle_stop_scan_script (Yanıt #50'deki gibi)
# update_realtime_values (Yanıt #50'deki gibi)
# update_all_graphs (Yanıt #50'deki gibi, dropdown input'u olmadan en son taramayı alır)
# update_analysis_panel (Yanıt #50'deki gibi, dropdown input'u olmadan en son taramayı alır)
# update_system_card (Yanıt #50'deki gibi, psutil ile)
# export_csv_callback (Yanıt #50'deki gibi, dropdown state'i olmadan en son taramayı alır)
# export_excel_callback (Yanıt #50'deki gibi, dropdown state'i olmadan en son taramayı alır)

# ÖNEMLİ: Diğer tüm callback fonksiyonlarının (update_realtime_values, update_all_graphs, vb.)
# tam ve güncel hallerini bir önceki tam kod cevabınızdan (Yanıt #50) alıp
# buraya dikkatlice kopyalamanız gerekmektedir.
# Özellikle, artık bir 'scan-select-dropdown' olmadığı için, bu callback'lerin
# Input/State listelerinden bu dropdown'a yapılan referansları çıkarmanız ve
# bunun yerine `get_latest_scan_id_from_db()` helper fonksiyonunu kullanarak
# en son tarama ID'sini alıp ona göre işlem yapmaları gerekir.
# Yanıt #50'deki kod zaten bu mantıkla (dropdown olmadan) yazılmıştı.