#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Visualiza la importancia de características del modelo NILM entrenado (XGBoost).
"""

import joblib
import pandas as pd
import matplotlib.pyplot as plt

MODEL_PATH = "nilm_model_xgb.joblib"

# === 1. Cargar modelo ===
bundle = joblib.load(MODEL_PATH)
model = bundle["model"]
features = bundle["features"]

# === 2. Importancias ===
booster = model.get_booster()
imp = booster.get_score(importance_type="gain")
imp_series = pd.Series(imp).reindex([f"f{i}" for i in range(len(features))]).fillna(0.0)
imp_series.index = features
imp_series = imp_series.sort_values(ascending=False)

print("Importancia (gain):")
print(imp_series)

# === 3. Graficar ===
imp_series.plot(kind="barh", color="steelblue")
plt.gca().invert_yaxis()
plt.title("Importancia de características (XGBoost Gain)")
plt.xlabel("Ganancia media en splits")
plt.tight_layout()
plt.show()
