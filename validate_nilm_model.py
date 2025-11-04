#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Validación cruzada del modelo NILM con XGBoost
Calcula F1 macro y matriz de confusión promedio.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix, f1_score
from xgboost import XGBClassifier
import seaborn as sns
import matplotlib.pyplot as plt

DATA_PATH = "data_filtrado.csv"

# === 1. Cargar y preparar datos ===
df = pd.read_csv(DATA_PATH)
target_col = "load"
features = [c for c in df.columns if c not in [target_col, "device", "ts"]]
X = df[features].values
y = df[target_col].values

label_enc = LabelEncoder()
y_enc = label_enc.fit_transform(y)
classes = label_enc.classes_

# === 2. Escalado ===
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# === 3. Configurar modelo ===
model = XGBClassifier(
    n_estimators=200,
    learning_rate=0.05,
    max_depth=5,
    subsample=0.9,
    colsample_bytree=0.9,
    random_state=42,
)

# === 4. Validación cruzada ===
cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
y_pred = cross_val_predict(model, X_scaled, y_enc, cv=cv)

# === 5. Métricas ===
f1_macro = f1_score(y_enc, y_pred, average="macro")
print(f"F1 macro promedio (5-fold): {f1_macro:.3f}\n")
print(classification_report(y_enc, y_pred, target_names=classes))

# === 6. Matriz de confusión ===
cm = confusion_matrix(y_enc, y_pred)
sns.heatmap(cm, annot=True, fmt="d", cmap="Blues",
            xticklabels=classes, yticklabels=classes)
plt.xlabel("Predicho")
plt.ylabel("Real")
plt.title("Matriz de confusión (validación cruzada)")
plt.tight_layout()
plt.show()
