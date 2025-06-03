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
DEFAULT_UI_STEPS_PER_REVOLUTION = 4096

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
            dbc.Col(html.Button('Başlat', id='start-scan-button', n_clicks=0,
                                className="btn btn-success btn-lg w-100 mb-2"), width=6),
            dbc.Col(html.Button('Taramayı Durdur', id='stop-scan-button', n_clicks=0,
                                className="btn btn-danger btn-lg w-100 mb-2"), width=6)
        ]),
        html.Div(id='scan-status-message', style={'marginTop': '10px', 'minHeight': '40px', 'textAlign': 'center'},
                 className="mb-3"),
        html.Hr(),
        html.H6("Tarama Parametreleri:", className="mt-2"),
        dbc.InputGroup([dbc.InputGroupText("Adım Açısı (°)", style={"width": "150px"}),
                        dbc.Input(id="step-angle-input", type="number", value=DEFAULT_UI_SCAN_STEP_ANGLE, min=0.1, max=45, step=0.1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Buzzer Mes. (cm)", style={"width": "150px"}),
                        dbc.Input(id="buzzer-distance-input", type="number", value=DEFAULT_UI_BUZZER_DISTANCE, min=0, max=200, step=1)], className="mb-2"),
        dbc.InputGroup([dbc.InputGroupText("Motor Adım/Tur", style={"width": "150px"}),
                        dbc.Input(id="steps-per-rev-input", type="number", value=DEFAULT_UI_STEPS_PER_REVOLUTION, min=500, max=10000, step=1)], className="mb-2"),
        dbc.Checkbox(id="invert-motor-checkbox", label="Motor Yönünü Ters Çevir", value=DEFAULT_UI_INVERT_MOTOR, className="mt-2 mb-2"),
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
    dcc.Interval(id='interval-component-main', interval=2500, n_intervals=0), # Hızı biraz arttıralım
    dcc.Interval(id='interval-component-system', interval=3000, n_intervals=0),
])


# ==============================================================================
# --- YARDIMCI FONKSİYONLAR ---
# ==============================================================================
def get_db_connection():
    try:
        if not os.path.exists(DB_PATH): return None, f"Veritabanı dosyası ({DB_PATH}) bulunamadı."
        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn, None
    except Exception as e: return None, f"DB Bağlantı Hatası: {e}"

def estimate_geometric_shape(df):
    if len(df) < 15: return "Şekil tahmini için yetersiz nokta."
    try:
        points = df[['x_cm', 'y_cm']].values
        hull = ConvexHull(points)
        hull_area = hull.volume
        min_x, max_x = df['x_cm'].min(), df['x_cm'].max()
        min_y, max_y = df['y_cm'].min(), df['y_cm'].max()
        width, depth = max_y - min_y, max_x - min_x
        if width < 1 or depth < 1: return "Algılanan şekil çok küçük."
        bbox_area = width * depth
        fill_factor = hull_area / bbox_area if bbox_area > 0 else 0
        aspect_ratio = width / depth
        if aspect_ratio > 5 and depth < 30: return "Tahmin: Geniş ve ince bir yüzey (Duvar)."
        if aspect_ratio < 0.25 and depth > 50: return "Tahmin: Dar ve derin bir boşluk (Koridor)."
        if fill_factor > 0.80:
            return "Tahmin: Dolgun, dikdörtgensel bir nesne." if 0.8 > aspect_ratio or aspect_ratio > 1.25 else "Tahmin: Kutu veya dairesel bir nesne."
        if fill_factor < 0.4: return "Tahmin: İçbükey bir yapı (Köşe)."
        return "Tahmin: Düzensiz veya karmaşık bir yapı."
    except Exception: return "Analiz hatası (Convex Hull)."

def get_latest_scan_id_from_db(conn_param=None):
    #... (Bu ve diğer tüm yardımcı fonksiyonlar ve callbackler önceki gibi aynıdır)
    pass # Kısa tutmak için çıkarıldı, kendi kodunuzda silmeyin.

# ==============================================================================
# --- CALLBACK FONKSİYONLARI ---
# ==============================================================================
# Önceki yanıtta paylaşılan TÜM callback fonksiyonları burada yer almalıdır.
# handle_start_scan_script, handle_stop_scan_script, update_realtime_values,
# update_analysis_panel, update_system_card, export_csv_callback,
# export_excel_callback, render_and_update_data_table, update_all_graphs,
# display_cluster_info
#
# Not: Cevabın çok uzamaması için bu fonksiyonlar tekrar eklenmemiştir.
# Lütfen bir önceki cevaptaki tam koddan bu fonksiyonları alıp buraya yapıştırın.
# VEYA İSTERSENİZ BU KISMI DA DOLDURABİLİRİM.
#