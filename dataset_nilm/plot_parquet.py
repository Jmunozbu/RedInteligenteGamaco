# plot_parquet.py
import pandas as pd
import matplotlib.pyplot as plt

archivo = "processed/features_2025-10-06.parquet"   # mismo directorio
variable = "FREQ"          # <- cámbialo por la columna que quieras (ej: "U_L1", "FREQ", ...)

df = pd.read_parquet(archivo)

# Asegurar zona horaria legible (convierte de UTC a America/Bogota si aplica)
if isinstance(df.index, pd.DatetimeIndex):
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC").tz_convert("America/Bogota")
    else:
        df.index = df.index.tz_convert("America/Bogota")
else:
    raise ValueError("El índice no es DatetimeIndex. Revisa el parquet.")

# Validar variable
if variable not in df.columns:
    raise KeyError(f"'{variable}' no está en el archivo. Disponibles: {list(df.columns)}")

# Graficar variable vs tiempo (índice)
plt.figure(figsize=(10,5))
plt.plot(df.index, df[variable], label=variable)
plt.ylim([min(0,1.2*min(df[variable])),1.2*max(df[variable])])
plt.xlabel("Tiempo (America/Bogota)")
plt.ylabel(variable)
plt.title(f"{variable} vs Tiempo")
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()
