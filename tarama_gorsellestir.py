import numpy as np
import matplotlib.pyplot as plt
import plotly.graph_objects as go

# Veri
data = {
    0.0: 39.122535, 10.0: 97.633742, 20.0: 21.621312, 30.0: 20.418143,
    40.0: 20.201057, 50.0: 20.195033, 60.0: 21.070663, 70.0: 124.523006,
    80.0: 225.963295, 90.0: 228.262811, 100.0: 61.239506, 110.0: 230.626869,
    120.0: 228.497695, 130.0: 227.767796, 140.0: 231.727540, 150.0: 234.290860,
    160.0: 250.000000, 170.0: 250.000000, 180.0: 250.000000, 190.0: 193.192452,
    200.0: 137.238266, 210.0: 136.093906, 220.0: 135.574253, 230.0: 134.533660,
    240.0: 135.003892, 250.0: 136.239319, 260.0: 137.523214, 270.0: 157.141373
}

degrees = np.array(list(data.keys()))
distances = np.array(list(data.values()))

# Dereceleri radyanlara Ã§evirme
radians = np.radians(degrees)

# Kutupsal koordinatlardan Kartezyen koordinatlara Ã§evirme (sensÃ¶r orijinde (0,0) kabul edilmiÅŸtir)
# 0 derece pozitif X ekseni, 90 derece pozitif Y ekseni yÃ¶nÃ¼ndedir.
x = distances * np.cos(radians)
y = distances * np.sin(radians)

# --- Matplotlib ile 2D Kutupsal Grafik (OrtamÄ±n KuÅŸbakÄ±ÅŸÄ± GÃ¶rÃ¼nÃ¼mÃ¼) ---
plt.figure(figsize=(8, 8))
ax_polar = plt.subplot(111, projection='polar')
ax_polar.plot(radians, distances, 'o-', linewidth=1, markersize=4)

ax_polar.set_theta_direction(1)         # Saat yÃ¶nÃ¼nÃ¼n tersine artan aÃ§Ä±lar
ax_polar.set_title('HC-SR04 Tarama (Kutupsal GÃ¶rÃ¼nÃ¼m)', va='bottom')
ax_polar.set_rlabel_position(225)       # YarÄ±Ã§ap etiketi konumunu ayarlar
plt.grid(True)
plt.show()

# --- Matplotlib ile 3D DaÄŸÄ±lÄ±m GrafiÄŸi ---
fig_3d_mpl = plt.figure(figsize=(10, 8))
ax_3d_mpl = fig_3d_mpl.add_subplot(111, projection='3d')

# SensÃ¶rÃ¼n yatay bir dÃ¼zlemde tarama yaptÄ±ÄŸÄ± varsayÄ±ldÄ±ÄŸÄ±ndan Z koordinatlarÄ±nÄ± 0 olarak belirliyoruz.
# Ancak gÃ¶rsel olarak daha iyi anlaÅŸÄ±lmasÄ± iÃ§in noktalarÄ±n rengini mesafeye gÃ¶re ayarlayabiliriz.
ax_3d_mpl.scatter(x, y, np.zeros_like(x), c=distances, cmap='viridis', marker='o', s=50)

ax_3d_mpl.set_xlabel('X (cm)')
ax_3d_mpl.set_ylabel('Y (cm)')
ax_3d_mpl.set_zlabel('Z (cm)')
ax_3d_mpl.set_title('HC-SR04 Tarama (Matplotlib 3D DaÄŸÄ±lÄ±m GrafiÄŸi)')
ax_3d_mpl.view_init(elev=20., azim=-45) # BakÄ±ÅŸ aÃ§Ä±sÄ±nÄ± ayarlar

# Eksik sÄ±nÄ±rlarÄ±nÄ± ayarlayarak orantÄ±lÄ± bir gÃ¶rÃ¼nÃ¼m saÄŸlamaya Ã§alÄ±ÅŸalÄ±m
max_range = np.max(distances)
ax_3d_mpl.set_xlim([-max_range, max_range])
ax_3d_mpl.set_ylim([-max_range, max_range])
ax_3d_mpl.set_zlim([-max_range/10, max_range/10]) # Z ekseni iÃ§in daha kÃ¼Ã§Ã¼k bir aralÄ±k
plt.show()

# --- Plotly ile Ä°nteraktif 3D DaÄŸÄ±lÄ±m GrafiÄŸi ---
fig_plotly = go.Figure(data=[go.Scatter3d(
    x=x,
    y=y,
    z=np.zeros_like(x), # SensÃ¶rÃ¼n yatay dÃ¼zlemde tarama yaptÄ±ÄŸÄ± varsayÄ±mÄ±
    mode='markers',
    marker=dict(
        size=5,
        color=distances, # Mesafeye gÃ¶re renklendir
        colorscale='Viridis', # Renk skalasÄ±
        opacity=0.8
    )
)])

fig_plotly.update_layout(
    title='HC-SR04 Tarama (Plotly 3D DaÄŸÄ±lÄ±m GrafiÄŸi)',
    scene=dict(
        xaxis_title='X (cm)',
        yaxis_title='Y (cm)',
        zaxis_title='Z (cm)',
        aspectmode='data' # OrantÄ±lÄ± eksenler iÃ§in
    ),
    margin=dict(l=0, r=0, b=0, t=40)
)
fig_plotly.show()