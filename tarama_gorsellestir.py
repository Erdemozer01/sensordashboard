import matplotlib.pyplot as plt
import numpy as np

# Verileri tanÄ±mlayÄ±n
degrees = np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0,
                    110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 180.0, 190.0,
                    200.0, 210.0, 220.0, 230.0, 240.0, 250.0, 260.0, 270.0])
distances = np.array([70.900636, 73.959323, 79.593086, 98.990220, 66.583498, 24.794133,
                     62.428756, 75.911614, 75.352710, 74.389196, 64.501197, 160.560835,
                     242.621763, 240.736991, 240.945341, 242.296130, 31.413662, 32.875074,
                     32.959139, 32.959139, 111.635910, 132.800523, 132.541328, 132.484758,
                     132.082844, 132.285779, 134.208996, 150.980500])

# Polar koordinatlara dönüştürme
radians = np.deg2rad(degrees)
x = distances * np.cos(radians)
y = distances * np.sin(radians)

# Ã‡izim
plt.figure(figsize=(8, 8))
plt.polar(radians, distances, marker='.')

# Tahmini nesneler
# KÃ¶ÅŸe
plt.plot(radians[4:7], distances[4:7], color='red', linewidth=3, label='OlasÄ± Nesne (KÃ¶ÅŸe)')

# Duvarlar
plt.plot(radians[0:4], distances[0:4], color='green', linewidth=2, label='Duvar')
plt.plot(radians[7:11], distances[7:11], color='green', linewidth=2)
plt.plot(radians[16:20], distances[16:20], color='green', linewidth=2)
plt.plot(radians[20:], distances[20:], color='green', linewidth=2)

# Eksenleri ve aÃ§Ä±klamalarÄ± ayarla
plt.title('Ultrasonik SensÃ¶r Verisi: Tahmini Ortam')
plt.xlabel('X (cm)')
plt.ylabel('Y (cm)')
plt.legend()
plt.grid(True)
plt.ylim(0, max(distances) + 20)
plt.show()