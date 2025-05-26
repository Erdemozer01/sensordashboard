from django_plotly_dash import DjangoDash
import dash
import dash_html_components as html # veya dash.html
import dash_core_components as dcc  # veya dash.dcc
from dash.dependencies import Input, Output, State
import plotly.graph_objects as go
import sqlite3
import pandas as pd
import os
import sys
import subprocess
import time # Buton callback'inde kısa bir bekleme için
import dash_bootstrap_components as dbc # En üste ekleyin

# --- Sabitler ---
# Projenin ana dizinini bulmaya çalışalım (manage.py'nin olduğu yer)
# Bu dosya dashboard_app içinde olduğu için iki seviye yukarı çıkmamız gerekebilir.
# Django projesinin ana dizini (manage.py'nin olduğu yer)
PROJECT_ROOT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_FILENAME = 'db.sqlite3'
DB_PATH = os.path.join(PROJECT_ROOT_DIR, DB_FILENAME)

SENSOR_SCRIPT_FILENAME = 'sensor_script.py'
SENSOR_SCRIPT_PATH = os.path.join(PROJECT_ROOT_DIR, SENSOR_SCRIPT_FILENAME)

LOCK_FILE_PATH_FOR_DASH = '/tmp/sensor_script.lock' # sensor_script.py'deki ile aynı olmalı

app = DjangoDash(
    'RealtimeSensorDashboard',
    external_stylesheets=[dbc.themes.BOOTSTRAP] # add_bootstrap_links=True yerine bunu deneyin
)

app.layout = html.Div([
    html.H1("Eş Zamanlı Ultrasonik Sensör Veri Paneli", style={'textAlign': 'center', 'marginBottom': '10px'}),

    html.Div([
        html.Button('Sensör Okumalarını Başlat', id='start-sensor-button', n_clicks=0,
                    style={'marginRight': '10px', 'padding': '10px', 'fontSize': '16px'}),
        html.Span(id='start-status-message', style={'fontSize': '16px', 'color': 'blue'})
    ], style={'textAlign': 'center', 'marginBottom': '20px'}),

    dcc.Interval(
        id='interval-component-realtime',
        interval=1*1000,  # Her 1 saniyede bir güncelle
        n_intervals=0
    ),
    html.Div(id='graphs-container', children=[
        html.Div([dcc.Graph(id='distance-profile-graph-realtime')], style={'width': '100%', 'display': 'inline-block', 'marginBottom': '10px'}),
        html.Div([dcc.Graph(id='speed-profile-graph-realtime')], style={'width': '100%', 'display': 'inline-block'}),
    ]),
    html.Div(id='data-summary-realtime', style={'padding': '20px', 'fontSize': '16px', 'marginTop': '20px', 'border': '1px solid #ddd', 'borderRadius': '5px', 'backgroundColor': '#f9f9f9'})
])

# --- Buton Callback'i ---
@app.callback(
    Output('start-status-message', 'children'),
    [Input('start-sensor-button', 'n_clicks')],
    prevent_initial_call=True
)
def handle_start_sensor_script(n_clicks):
    if n_clicks > 0:
        # Kilit dosyasının varlığına göre basit bir kontrol (asıl kontrol betikte)
        if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
             # PID dosyasından PID okuyup kontrol etmek daha iyi olabilir ama fcntl daha güvenilir
             # Şimdilik sadece kilit dosyası var mı diye bakıyoruz.
             # sensor_script.py kendi içinde zaten çalışıp çalışmadığını kontrol edecek.
             print(f"Dash: Kilit dosyası ({LOCK_FILE_PATH_FOR_DASH}) mevcut. Betik zaten çalışıyor olabilir.")
             # return "Sensör betiği zaten çalışıyor gibi görünüyor veya kilit dosyası kalmış."
             # Kullanıcıya bu mesajı göstermek kafa karıştırıcı olabilir, betik kendi mesajını bassın.

        try:
            python_executable = sys.executable # Mevcut sanal ortamdaki Python
            print(f"'{SENSOR_SCRIPT_PATH}' betiği '{python_executable}' ile başlatılıyor...")

            # Betiği arka planda ve yeni bir session'da başlat
            process = subprocess.Popen(
                [python_executable, SENSOR_SCRIPT_PATH],
                start_new_session=True, # Bu, Django sunucusu kapansa bile betiğin çalışmaya devam etmesini sağlar (isteğe bağlı)
                stdout=subprocess.PIPE, # Çıktıları yakalamak için (opsiyonel)
                stderr=subprocess.PIPE  # Hataları yakalamak için (opsiyonel)
            )

            time.sleep(1.5) # Betiğin başlaması ve kilit dosyasını oluşturması için kısa bir süre tanı

            if os.path.exists(LOCK_FILE_PATH_FOR_DASH):
                return f"Sensör betiği başlatma komutu gönderildi (PID: {process.pid}). Betik çalışıyor olmalı."
            else:
                # stdout, stderr loglanabilir veya kullanıcıya gösterilebilir
                stdout, stderr = process.communicate(timeout=2) # Kısa bir süre bekle
                error_message = f"Sensör betiği başlatıldı ancak kilit dosyası ({LOCK_FILE_PATH_FOR_DASH}) oluşmadı. "
                if stderr:
                    error_message += f"Hata Çıktısı: {stderr.decode(errors='ignore')}"
                return error_message

        except FileNotFoundError:
            return f"HATA: Sensör betiği bulunamadı: {SENSOR_SCRIPT_PATH}"
        except Exception as e:
            return f"Sensör betiği başlatılırken hata oluştu: {str(e)}"
    return dash.no_update # Butona tıklanmadıysa veya n_clicks=0 ise bir şey yapma

# --- Grafik Güncelleme Callback'i ---
@app.callback(
    [Output('distance-profile-graph-realtime', 'figure'),
     Output('speed-profile-graph-realtime', 'figure'),
     Output('data-summary-realtime', 'children')],
    [Input('interval-component-realtime', 'n_intervals')]
)
def update_realtime_graphs(n):
    conn = None
    df = pd.DataFrame()
    error_message_div = []

    try:
        if not os.path.exists(DB_PATH):
            raise FileNotFoundError(f"Veritabanı dosyası bulunamadı: {DB_PATH}")

        conn = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True, timeout=5) # Salt okunur modda
        # Son N örneği alarak performansı artırabiliriz, örneğin son 200.
        # df = pd.read_sql_query("SELECT * FROM (SELECT * FROM measurements ORDER BY ornek_no DESC LIMIT 200) ORDER BY ornek_no ASC", conn)
        df = pd.read_sql_query("SELECT * FROM measurements ORDER BY ornek_no ASC", conn)

    except sqlite3.OperationalError as e_sql:
        msg = f"Veritabanı okuma hatası: {e_sql}. Sensör betiği çalışıyor ve DB dosyası doğru yerde mi?"
        print(msg)
        error_message_div = [html.P(msg, style={'color':'red'})]
    except FileNotFoundError as e_fnf:
        msg = f"Veritabanı dosyası ({DB_PATH}) bulunamadı. Sensör betiği çalıştırıldı mı?"
        print(msg)
        error_message_div = [html.P(msg, style={'color':'orange'})]
    except Exception as e_gen:
        msg = f"Veri okunurken bilinmeyen bir hata: {e_gen}"
        print(msg)
        error_message_div = [html.P(msg, style={'color':'red'})]
    finally:
        if conn:
            conn.close()

    fig_distance = go.Figure()
    fig_speed = go.Figure()
    summary_children = error_message_div if error_message_div else [html.P("Veri bekleniyor...")]


    if not df.empty and not error_message_div:
        if not all(col in df.columns for col in ['ornek_no', 'mesafe_cm', 'hiz_cm_s']):
            summary_children = [html.P("Veritabanındaki veri formatı beklenenden farklı.", style={'color':'red'})]
        else:
            fig_distance.add_trace(go.Scatter(
                x=df['ornek_no'], y=df['mesafe_cm'], mode='lines+markers', name='Mesafe',
                line=dict(color='dodgerblue', width=2), marker=dict(size=4)
            ))
            fig_distance.update_layout(title_text='Canlı Mesafe Profili', xaxis_title="Örnek No", yaxis_title="Mesafe (cm)", yaxis=dict(rangemode='tozero'))

            fig_speed.add_trace(go.Scatter(
                x=df['ornek_no'], y=df['hiz_cm_s'], mode='lines+markers', name='Hız',
                line=dict(color='crimson', width=2), marker=dict(size=4)
            ))
            fig_speed.update_layout(title_text='Canlı Tahmini Hız Profili', xaxis_title="Örnek No", yaxis_title="Hız (cm/s)")

            try:
                min_dist = df['mesafe_cm'].min()
                max_dist = df['mesafe_cm'].max()
                avg_dist = df['mesafe_cm'].mean()
                current_dist_val = df['mesafe_cm'].iloc[-1]
                current_speed_val = df['hiz_cm_s'].iloc[-1]

                summary_children = [
                    html.H4("Anlık Durum ve İstatistikler:", style={'marginTop': '0px', 'marginBottom':'10px'}),
                    html.Table([
                        html.Tr([html.Td("Son Okunan Mesafe:"), html.Td(f"{current_dist_val:.2f} cm")]),
                        html.Tr([html.Td("Son Tahmini Hız:"), html.Td(f"{current_speed_val:.2f} cm/s")]),
                        html.Tr([html.Td("Minimum Mesafe (Tüm Veri):"), html.Td(f"{min_dist:.2f} cm")]),
                        html.Tr([html.Td("Maksimum Mesafe (Tüm Veri):"), html.Td(f"{max_dist:.2f} cm")]),
                        html.Tr([html.Td("Ortalama Mesafe (Tüm Veri):"), html.Td(f"{avg_dist:.2f} cm")]),
                        html.Tr([html.Td("Toplam Örnek Sayısı:"), html.Td(f"{len(df)}")])
                    ])
                ]
            except Exception as e_summary:
                summary_children = [html.P(f"Özet istatistikleri hesaplanırken hata: {e_summary}")]

    # Eğer df boşsa veya hata varsa, grafiklere en azından başlık ekle
    if df.empty or error_message_div:
        fig_distance.update_layout(title_text='Canlı Mesafe Profili (Veri Bekleniyor/Hata)')
        fig_speed.update_layout(title_text='Canlı Tahmini Hız Profili (Veri Bekleniyor/Hata)')


    return fig_distance, fig_speed, summary_children