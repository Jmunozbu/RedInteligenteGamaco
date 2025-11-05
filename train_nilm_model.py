#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Entrena un modelo de clasificación NILM con XGBoost
y guarda el modelo + escalador + codificador de etiquetas.
"""

import pandas as pd
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler, LabelEncoder
from sklearn.metrics import classification_report, confusion_matrix
from xgboost import XGBClassifier
import joblib

# === CONFIGURACIÓN ===
DATA_PATH = "data_filtrado.csv"
MODEL_PATH = "nilm_model_xgb.joblib"

# === 1. Cargar dataset ===
df = pd.read_csv(DATA_PATH)

target_col = "load"  # cambia si tu etiqueta tiene otro nombre
feature_cols = [c for c in df.columns if c not in [target_col, "device", "ts"]]

print(f"Características usadas: {feature_cols}")
print(f"Total de muestras: {len(df)}")

X = df[feature_cols].values
y = df[target_col].values

# === 2. Codificar etiquetas (texto → números) ===
label_enc = LabelEncoder()
y_encoded = label_enc.fit_transform(y)

print(f"Clases detectadas: {list(label_enc.classes_)}")

# === 3. Dividir en entrenamiento y prueba ===
X_train, X_test, y_train, y_test = train_test_split(
    X, y_encoded, test_size=0.2, random_state=42, stratify=y_encoded
)

# === 4. Escalar características ===
scaler = StandardScaler()
X_train_scaled = scaler.fit_transform(X_train)
X_test_scaled = scaler.transform(X_test)

# === 5. Entrenar modelo ===
model = XGBClassifier(
    n_estimators=250,
    learning_rate=0.05,
    max_depth=8,
    subsample=0.9,
    colsample_bytree=0.9,
    random_state=42,
)

print("Entrenando modelo...")
model.fit(X_train_scaled, y_train)

# === 6. Evaluación ===
y_pred = model.predict(X_test_scaled)

print("\n=== Resultados del modelo ===")
print(confusion_matrix(y_test, y_pred))
print(classification_report(y_test, y_pred, target_names=label_enc.classes_))

# === 7. Guardar modelo completo ===
joblib.dump(
    {
        "model": model,
        "scaler": scaler,
        "encoder": label_enc,
        "features": feature_cols,
    },
    MODEL_PATH,
)

print(f"\nModelo exportado a: {MODEL_PATH}")

# === 8. Prueba rápida ===
sample = X_test_scaled[0:1]
pred = model.predict(sample)[0]
print(f"Predicción de ejemplo: {label_enc.inverse_transform([pred])[0]}")
