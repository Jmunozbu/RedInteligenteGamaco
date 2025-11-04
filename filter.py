import pandas as pd
from pathlib import Path

# Carpeta del script
BASE = Path(__file__).resolve().parent

# Opciones de entrada:
# 1) archivo en la misma carpeta del script:
in_path = BASE / "data.csv"

# 2) si quieres ruta absoluta, usa forward slashes o raw string:
# in_path = Path(r"C:\Users\juand\OneDrive\Documents\Uni\2025-2\Gamaco\RED\Medicion\data.csv")

out_path = in_path.with_name(in_path.stem + "_filtrado.csv")

# Cargar (utf-8-sig por si el archivo tiene BOM)
df = pd.read_csv(in_path, skipinitialspace=True, encoding="utf-8-sig")

# Filtrar: eliminar filas donde load == 'PC' y P==Q==S==0 (o NaN tratados como 0)
mask_keep = ~(
    (df["load"] == "PC") &
    df[["P", "Q", "S"]].fillna(0).eq(0).all(axis=1)
)
df_filtrado = df.loc[mask_keep].copy()

# Guardar
df_filtrado.to_csv(out_path, index=False)

print(f"Filas totales: {len(df)} | Filas guardadas: {len(df_filtrado)}")
print(f"Archivo escrito en: {out_path}")
