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
SENSOR_SCRIPT_LOCK_FILE = '/tmp/sensor_scan_script.lock'
SENSOR_SCRIPT_PID_FILE = '/tmp/sensor_scan_script.pid'

# Panel için varsayılan değerler
DEFAULT_UI_SCAN_STEP_ANGLE = 10
DEFAULT_UI_BUZZER_DISTANCE = 10
DEFAULT_UI_INVERT_MOTOR = False
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096 # Kalibrasyonla bulduğumuz doğru değer

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# ==============================================================================
# --- LAYOUT (ARAYÜZ) BİLEŞENLERİ ---
# ==============================================================================
title_card = dbc.Row(
    [dbc.Col(html.H1("Dream Pi Kullanıcı Paneli", className="text-center my-3 mb-5"), width=12), html.Hr()])

# GÜNCELLENMİŞ KONTROL PANELİ
control_panel = dbc.Card([
    dbc.CardHeader("Tarama Kontrol ve Ayarları", className="bg-primary text-white"),
    dbc.CardBody([
        dbc.Row([
            # Buton ismi "Otomatik Taramayı Başlat" olarak güncellendi
            dbc.Col(html.Button('Otomatik Taramayı Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        # BAŞLANGIÇ VE BİTİŞ AÇISI GİRİŞLERİ KALDIRILDI
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1,
                                  max=45, step=0.1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Buzzer Mes. (cm)", style={"width": "150px"}),
                        dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0,
                                  max=200, step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}),
                        dbc.Input(id="steps-per-rev-input", type="number", value=DEFAULT_UI_STEPS_PER_REVOLUTION, min=500,
                                  max=10000, step=1)], className="mb-2"),
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
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================

# GÜNCELLENMİŞ CALLBACK
@app.callback(
    Output('scan-status-message', 'children'),
    [Input('start-scan-button', 'n_clicks')],
    [
        # State'ler arasından start_angle ve end_angle kaldırıldı
        State('step-angle-input', 'value'),
        State('buzzer-distance-input', 'value'),
        State('invert-motor-checkbox', 'value'),
        State('steps-per-rev-input', 'value')
    ],
    prevent_initial_call=True)
def handle_start_scan_script(n_clicks_start, step_angle_val, buzzer_distance_val,
                             invert_motor_val, steps_per_rev_val):
    if n_clicks_start == 0: return no_update

    # Değer atamaları güncellendi
    step_a, buzzer_d, invert_dir, steps_per_rev = \
        (step_angle_val if step_angle_val is not None else DEFAULT_UI_SCAN_STEP_ANGLE), \
        (buzzer_distance_val if buzzer_distance_val is not None else DEFAULT_UI_BUZZER_DISTANCE), \
        bool(invert_motor_val), \
        (steps_per_rev_val if steps_per_rev_val is not None else DEFAULT_UI_STEPS_PER_REVOLUTION)

    # Kontroller güncellendi
    if not (0.1 <= abs(step_a) <= 45): return dbc.Alert("Adım açısı 0.1-45 arasında olmalı!", color="danger")
    if not (0 <= buzzer_d <= 200): return dbc.Alert("Buzzer mesafesi 0-200cm arasında olmalı!", color="danger")
    if not (500 <= steps_per_rev <= 10000): return dbc.Alert("Motor Adım/Tur değeri 500-10000 arasında olmalı!", color="danger")

    pid = None
    if os.path.exists(SENSOR_SCRIPT_PID_FILE):
        try:
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf:
                pid_str = pf.read().strip(); pid = int(pid_str) if pid_str else None
        except: pid = None
    if pid and is_process_running(pid): return dbc.Alert(f"Sensör betiği çalışıyor (PID:{pid}). Önce durdurun.", color="warning")

    for fp in [SENSOR_SCRIPT_LOCK_FILE, SENSOR_SCRIPT_PID_FILE]:
        if os.path.exists(fp):
            try: os.remove(fp)
            except OSError as e: return dbc.Alert(f"Kalıntı dosya ({fp}) silinemedi: {e}.", color="danger")

    try:
        py_exec = sys.executable
        if not os.path.exists(SENSOR_SCRIPT_PATH): return dbc.Alert(f"Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}", color="danger")
        
        # GÜNCELLENMİŞ KOMUT LİSTESİ
        # start_angle ve end_angle argümanları kaldırıldı
        cmd = [
            py_exec, SENSOR_SCRIPT_PATH,
            "--step_angle", str(step_a),
            "--buzzer_distance", str(buzzer_d),
            "--invert_motor_direction", str(invert_dir),
            "--steps_per_rev", str(steps_per_rev)
        ]

        log_path = os.path.join(PROJECT_ROOT_DIR, 'sensor_script.log')
        with open(log_path, 'w') as log_f:
            subprocess.Popen(cmd, start_new_session=True, stdout=log_f, stderr=log_f)
        time.sleep(2.5)
        if os.path.exists(SENSOR_SCRIPT_PID_FILE):
            with open(SENSOR_SCRIPT_PID_FILE, 'r') as pf_new:
                new_pid = pf_new.read().strip()
            return dbc.Alert(f"Otomatik tarama başlatıldı (PID:{new_pid}).", color="success")
        else:
            log_disp = f"PID dosyası ({SENSOR_SCRIPT_PID_FILE}) oluşmadı. "
            if os.path.exists(log_path):
                try:
                    with open(log_path, 'r') as f_log:
                        lines = "".join(f_log.readlines()[-10:]);log_disp_detail = (lines[:500] + '...') if len(lines) > 500 else lines
                    if log_disp_detail.strip():
                        log_disp += "Logdan son satırlar:"; return dbc.Alert([html.Span(log_disp), html.Br(), html.Pre(log_disp_detail, style={'whiteSpace': 'pre-wrap', 'maxHeight': '150px', 'overflowY': 'auto', 'fontSize': '0.8em', 'backgroundColor': '#f0f0f0', 'border': '1px solid #ccc', 'padding': '5px'})], color="danger")
                    else: log_disp += f"'{os.path.basename(log_path)}' boş."
                except Exception as e_l: log_disp += f"Log okuma hatası: {e_l}"
            else: log_disp += f"Log dosyası ('{os.path.basename(log_path)}') bulunamadı."
            return dbc.Alert(log_disp, color="danger")
    except Exception as e:
        return dbc.Alert(f"Sensör betiği başlatma hatası: {e}", color="danger")


# --- (Diğer tüm fonksiyonlar ve callback'ler aynı kalacak) ---
# ... handle_stop_scan_script ...
# ... update_realtime_values ...
# ... update_analysis_panel ...
# ... update_system_card ...
# ... export_csv_callback ...
# ... export_excel_callback ...
# ... render_and_update_data_table ...
# ... update_all_graphs ...
# ... display_cluster_info ...

# BU KISIMLARDA DEĞİŞİKLİK OLMADIĞI İÇİN TEKRAR EKLENMEMİŞTİR
# KODUNUZDA BU FONKSİYONLARI OLDUĞU GİBİ BIRAKINIZ.
# EĞER İSTERSENİZ BU KISIMLARI DA TAM OLARAK EKLEYEBİLİRİM.
# (Not: Okunabilirliği artırmak için kısa tutulmuştur)
# (KOD BLOKLARI UZUN OLDUĞU İÇİN KISALTILMIŞTIR)

def is_process_running(pid):
    if pid is None: return False
    try: return psutil.pid_exists(pid)
    except Exception: return False
def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5); return conn, None
    except Exception as e: return None, f"DB Hatası: {e}"
# ... (Diğer tüm yardımcı fonksiyonlar ve callback'ler burada yer alıyor)
# ... (Kodun geri kalanı değişmeden devam ediyor)