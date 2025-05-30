# dashboard_app/dash_apps.py (GEÇİCİ HATA AYIKLAMA SÜRÜMÜ)

from django_plotly_dash import DjangoDash
import dash
from dash import html, dcc, Output, Input, State, no_update, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd
import os
import time
import numpy as np



# Not: Bu testte kullanılmayacak olsalar da, hata almamak için kütüphaneler kalabilir.
from scipy.spatial import ConvexHull
from simplification.cutil import simplify_coords

app = DjangoDash('RealtimeSensorDashboard', external_stylesheets=[dbc.themes.BOOTSTRAP])

# --- Layout'ta değişiklik yok, aynı kalıyor ---
title_card = dbc.Row([dbc.Col(html.H1("Dream Pi - 2D Alan Tarama Sistemi", className="text-center my-3"), width=12)])
# ... (Diğer tüm layout bileşenleriniz aynı kalacak, buraya kopyalamıyorum)
# ... analysis_card, estimation_card, visualization_tabs vb. hepsi aynı...

# Layout'un tam tanımı (Değişiklik yok)
analysis_card = dbc.Card([dbc.CardHeader("Tarama Analizi (En Son Tarama)"), dbc.CardBody([
    dbc.Row([dbc.Col([html.H6("Hesaplanan Alan:"), html.H4(id='calculated-area')]),
             dbc.Col([html.H6("Çevre Uzunluğu:"), html.H4(id='perimeter-length')])]),
    dbc.Row([dbc.Col([html.H6("Max Genişlik:"), html.H4(id='max-width')]),
             dbc.Col([html.H6("Max Derinlik:"), html.H4(id='max-depth')])], className="mt-2")])])
estimation_card = dbc.Card([dbc.CardHeader("Ortam Şekli Tahmini"),
                            dbc.CardBody(html.H4(id='environment-estimation-text', className="text-center"))])
visualization_tabs = dbc.Tabs([
    dbc.Tab(dcc.Graph(id='scan-map-graph', style={'height': '75vh'}), label="2D Kartezyen Harita"),
    dbc.Tab(dcc.Graph(id='polar-graph', style={'height': '75vh'}), label="Polar Grafik"),
    dbc.Tab(dcc.Graph(id='time-series-graph', style={'height': '75vh'}), label="Zaman Serisi (Mesafe)"),
    dbc.Tab(dcc.Loading(
        children=[dash_table.DataTable(id='scan-data-table', style_table={'height': '70vh', 'overflowY': 'auto'})]),
            label="Veri Tablosu")])

app.layout = dbc.Container(fluid=True, children=[
    title_card,
    dbc.Row([
        dbc.Col(
            # Sol sütundaki diğer kartlar (control_panel, stats_panel vb.) burada olmalı
            # Kısalık için eklenmedi ama sizin kodunuzda olmalı
            md=4
        ),
        dbc.Col([
            visualization_tabs,
            dbc.Row(html.Div(style={"height": "15px"})),
            dbc.Row([dbc.Col(analysis_card, md=8), dbc.Col(estimation_card, md=4)])
        ], md=8)
    ]),
    dcc.Interval(id='interval-component-main', interval=3000, n_intervals=0)
])


# ####################################################################
# ##### TÜM ESKİ CALLBACK'LER GEÇİCİ OLARAK DEVRE DIŞI BIRAKILDI #####
# ####################################################################

# @app.callback(...)
# def update_analysis_panel(n_intervals):
#     # BU FONKSİYON ŞİMDİLİK DEVRE DIŞI
#     return "--", "--", "--", "--"

# @app.callback(...)
# def update_data_table(n_intervals):
#     # BU FONKSİYON ŞİMDİLİK DEVRE DIŞI
#     return [], []

# @app.callback(...)
# def update_all_graphs(n_intervals):
#     # ORİJİNAL KARMAŞIK FONKSİYONUNUZ ŞİMDİLİK DEVRE DIŞI
#     # ...
#     pass


###############################################################
#####             GEÇİCİ TEST CALLBACK'İ                  #####
###############################################################
# Sadece bu callback çalışacak

# Hata Ayıklama - Adım 2 Kodu

@app.callback(
    [Output('scan-map-graph', 'figure'),
     Output('polar-graph', 'figure'),
     Output('time-series-graph', 'figure'),
     Output('environment-estimation-text', 'children')],
    [Input('interval-component-main', 'n_intervals')]
)
def update_all_graphs(n_intervals):
    print("DEBUG ADIM 2: Ham veri çizim testi başlatıldı.")
    conn, error = get_db_connection()
    fig_map = go.Figure().update_layout(title_text="Adım 2: Ham Veri Çizimi...")

    if not conn:
        return fig_map, go.Figure(), go.Figure(), "DB Hatası"

    try:
        id_to_plot = get_latest_scan_id_from_db(conn_param=conn)
        if not id_to_plot:
            return fig_map, go.Figure(), go.Figure(), "ID Yok"

        df_points = pd.read_sql_query(f"SELECT x_cm, y_cm FROM scan_points WHERE scan_id = {id_to_plot}", conn)

        if not df_points.empty:
            print("DEBUG ADIM 2 BAŞARILI: Veri çizime gönderiliyor.")
            fig_map.add_trace(go.Scatter(x=df_points['y_cm'], y=df_points['x_cm'], mode='markers', name='Ham Veri'))
            fig_map.update_layout(title_text="Adım 2 Başarılı: Ham Veri Görünüyor!")
        else:
            fig_map.update_layout(title_text="Adım 2: Çizilecek veri yok.")

    except Exception as e:
        print(f"HATA: Adım 2 sırasında bir istisna oluştu: {e}")
        fig_map.update_layout(title_text=f"Adım 2'de Hata: {e}")
    finally:
        if conn:
            conn.close()

    return fig_map, go.Figure(), go.Figure(), "Çizim Testi"