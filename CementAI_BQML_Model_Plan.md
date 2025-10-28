# CementAI Optimizer - BQML Model Development Plan

## Phase 1: Data Foundation (Week 1-2)

### Feature Engineering in BigQuery

```sql
-- Create time-series feature table
CREATE OR REPLACE TABLE `cementai.plant_features` AS
WITH 
  raw_signals AS (
    SELECT 
      timestamp,
      plant_id,
      line_id,
      kiln_inlet_temp_c,
      kiln_outlet_temp_c,
      mill_power_kw,
      id_fan_speed_pct,
      af_fuel_pct,
      bag_filter_dp_kpa,
      stack_temp_c,
      feed_rate_tph,
      clinker_temp_c
    FROM `cementai.iot_signals`
  ),
  
  rolling_features AS (
    SELECT *,
      -- 5-minute rolling averages
      AVG(mill_power_kw) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 300 PRECEDING AND CURRENT ROW
      ) AS roll5m_power_kw,
      
      -- 1-hour rolling averages
      AVG(mill_power_kw) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW
      ) AS roll1h_power_kw,
      
      -- Temperature delta
      (kiln_outlet_temp_c - kiln_inlet_temp_c) AS temp_delta_c
      
    FROM raw_signals
  )

SELECT * FROM rolling_features;
```

---

## Phase 2: Core Prediction Models

### Model 1: Energy Consumption Predictor (kWh/ton)

**Purpose:** Predict energy usage and identify optimization opportunities

```sql
-- Train energy regression model
CREATE OR REPLACE MODEL `cementai.energy_regressor`
OPTIONS(
  model_type='BOOSTED_TREE_REGRESSOR',
  input_label_cols=['energy_kwh_per_ton'],
  max_iterations=50,
  learn_rate=0.1,
  subsample=0.8
) AS
SELECT
  feed_rate_tph,
  mill_power_kw,
  id_fan_speed_pct,
  af_fuel_pct,
  kiln_outlet_temp_c,
  stack_temp_c,
  bag_filter_dp_kpa,
  roll5m_power_kw,
  roll1h_power_kw,
  temp_delta_c,
  energy_kwh_per_ton  -- Label from meter readings
FROM `cementai.plant_features`
WHERE timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND energy_kwh_per_ton IS NOT NULL;

-- Evaluate model
SELECT
  mean_absolute_error,
  mean_squared_error,
  r2_score
FROM ML.EVALUATE(
  MODEL `cementai.energy_regressor`,
  (SELECT * FROM `cementai.plant_features`
   WHERE timestamp BETWEEN 
     TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
     AND TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))
);

-- Real-time prediction
CREATE OR REPLACE TABLE `cementai.predictions_energy` AS
SELECT 
  f.*,
  p.predicted_energy_kwh_per_ton,
  (f.energy_kwh_per_ton - p.predicted_energy_kwh_per_ton) AS energy_gap_kwh
FROM ML.PREDICT(
  MODEL `cementai.energy_regressor`,
  (SELECT * FROM `cementai.plant_features`
   WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE))
) p
JOIN `cementai.plant_features` f USING (timestamp, plant_id);
```

**Use Case:** When `energy_gap_kwh > 5`, trigger optimization alert

---

### Model 2: Dust/PM Emission Risk Classifier

**Purpose:** Predict bag filter issues and dust escape events

```sql
-- Create PM risk flag (binary label)
CREATE OR REPLACE TABLE `cementai.pm_labels` AS
SELECT
  timestamp,
  plant_id,
  CASE 
    WHEN stack_pm_mg_per_nm3 > 50 THEN 1  -- Threshold violation
    ELSE 0
  END AS pm_exceed_flag
FROM `cementai.emissions_data`;

-- Train classification model
CREATE OR REPLACE MODEL `cementai.pm_risk_classifier`
OPTIONS(
  model_type='LOGISTIC_REG',
  input_label_cols=['pm_exceed_flag']
) AS
SELECT
  f.bag_filter_dp_kpa,
  f.bag_reverse_cycle_s,
  f.esp_load_pct,
  f.stack_temp_c,
  f.id_fan_speed_pct,
  f.moisture_pct,
  l.pm_exceed_flag
FROM `cementai.plant_features` f
JOIN `cementai.pm_labels` l USING (timestamp, plant_id)
WHERE f.timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);

-- Predict risk in real-time
SELECT
  timestamp,
  predicted_pm_exceed_flag_probs[OFFSET(1)].prob AS pm_risk_probability,
  bag_filter_dp_kpa,
  stack_temp_c
FROM ML.PREDICT(
  MODEL `cementai.pm_risk_classifier`,
  (SELECT * FROM `cementai.plant_features`
   WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE))
)
WHERE predicted_pm_exceed_flag_probs[OFFSET(1)].prob > 0.7;  -- High risk alert
```

---

### Model 3: Heat Loss Quantifier (WHR Opportunity)

**Purpose:** Calculate recoverable energy from stack and cooler

```sql
-- Train heat loss regression
CREATE OR REPLACE MODEL `cementai.heat_loss_regressor`
OPTIONS(
  model_type='LINEAR_REG',
  input_label_cols=['stack_heat_loss_kw']
) AS
SELECT
  stack_temp_c,
  gas_flow_nm3h,
  o2_pct,
  kiln_load_pct,
  -- Calculated label: heat loss = f(temp, flow, specific heat)
  (stack_temp_c - 100) * gas_flow_nm3h * 0.32 AS stack_heat_loss_kw
FROM `cementai.plant_features`
WHERE timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY);

-- Identify WHR opportunities
SELECT
  DATE(timestamp) AS date,
  AVG(predicted_stack_heat_loss_kw) AS avg_recoverable_kw,
  AVG(predicted_stack_heat_loss_kw) * 24 * 0.08 AS daily_value_usd  -- $0.08/kWh
FROM ML.PREDICT(
  MODEL `cementai.heat_loss_regressor`,
  (SELECT * FROM `cementai.plant_features`)
)
GROUP BY date
ORDER BY date DESC;
```

---

### Model 4: Quality Stability Predictor

**Purpose:** Predict quality deviations before grinding

```sql
-- Train quality classification model
CREATE OR REPLACE MODEL `cementai.quality_classifier`
OPTIONS(
  model_type='DNN_CLASSIFIER',
  input_label_cols=['quality_flag'],
  hidden_units=[64, 32, 16]
) AS
SELECT
  raw_lsf_ratio,
  raw_sm_ratio,
  raw_am_ratio,
  clinker_temp_c,
  af_fuel_pct,
  feed_variability_index,
  CASE
    WHEN blaine_fineness BETWEEN 320 AND 350 
      AND strength_28d >= 43 THEN 'OK'
    WHEN blaine_fineness < 320 OR strength_28d < 40 THEN 'REJECT'
    ELSE 'WARNING'
  END AS quality_flag
FROM `cementai.quality_lab_data`
WHERE timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY);
```

---

## Phase 3: Optimization Rule Engine

### Decision Logic (Pseudo-SQL + Python)

```sql
-- Optimization recommendations table
CREATE OR REPLACE TABLE `cementai.ai_recommendations` AS
WITH 
  current_state AS (
    SELECT * FROM `cementai.plant_features`
    WHERE timestamp = (SELECT MAX(timestamp) FROM `cementai.plant_features`)
  ),
  
  predictions AS (
    SELECT
      energy.predicted_energy_kwh_per_ton,
      pm.predicted_pm_exceed_flag_probs[OFFSET(1)].prob AS pm_risk,
      heat.predicted_stack_heat_loss_kw
    FROM ML.PREDICT(MODEL `cementai.energy_regressor`, (SELECT * FROM current_state)) energy
    CROSS JOIN ML.PREDICT(MODEL `cementai.pm_risk_classifier`, (SELECT * FROM current_state)) pm
    CROSS JOIN ML.PREDICT(MODEL `cementai.heat_loss_regressor`, (SELECT * FROM current_state)) heat
  )

SELECT
  CASE
    WHEN predictions.pm_risk > 0.7 AND current_state.bag_filter_dp_kpa < 2.5
      THEN 'Extend bag reverse cycle by +60s; reduce ID fan by 2%'
    WHEN (current_state.energy_kwh_per_ton - predictions.predicted_energy_kwh_per_ton) > 5
      THEN CONCAT('Reduce mill power by ', 
                  CAST(ROUND((current_state.energy_kwh_per_ton - predictions.predicted_energy_kwh_per_ton) * 0.8) AS STRING), 
                  ' kW')
    WHEN predictions.predicted_stack_heat_loss_kw > 500
      THEN 'Stack heat loss high: Optimize WHR system or reduce excess air'
    ELSE 'Operating within optimal range'
  END AS recommendation,
  predictions.*,
  current_state.*
FROM predictions, current_state;
```

---

## Phase 4: Continuous Learning & Retraining

### Automated Model Refresh

```sql
-- Schedule daily retraining
CREATE OR REPLACE PROCEDURE `cementai.retrain_models`()
BEGIN
  -- Retrain energy model with last 30 days
  CREATE OR REPLACE MODEL `cementai.energy_regressor`
  OPTIONS(model_type='BOOSTED_TREE_REGRESSOR', input_label_cols=['energy_kwh_per_ton'])
  AS
  SELECT * FROM `cementai.plant_features`
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND energy_kwh_per_ton IS NOT NULL;

  -- Log retraining metrics
  INSERT INTO `cementai.model_performance_log` (timestamp, model_name, rmse)
  SELECT CURRENT_TIMESTAMP(), 'energy_regressor', mean_squared_error
  FROM ML.EVALUATE(MODEL `cementai.energy_regressor`,
    (SELECT * FROM `cementai.plant_features`
     WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)));
END;

-- Schedule to run daily at 2 AM
CREATE OR REPLACE SCHEDULE `cementai.daily_retrain`
OPTIONS (
  schedule = 'every day 02:00'
)
AS CALL `cementai.retrain_models`();
```

---

## Model Performance Targets

| Model | Metric | Target | Current (Simulated) |
|-------|--------|--------|---------------------|
| Energy Regressor | RMSE | <3 kWh/ton | 2.1 kWh/ton |
| PM Risk Classifier | ROC-AUC | >0.90 | 0.94 |
| Heat Loss Regressor | MAE | <50 kW | 32 kW |
| Quality Classifier | Accuracy | >85% | 89% |

---

## Integration with Gemini Agent

### Explainability Layer

For each BQML prediction, Gemini Pro generates human-readable explanations:

```python
# Pseudo-code for Gemini explanation
def generate_explanation(prediction_row):
    prompt = f"""
    Explain this cement plant optimization in simple terms:
    - Current energy use: {prediction_row['energy_kwh_per_ton']} kWh/ton
    - Predicted optimal: {prediction_row['predicted_energy_kwh_per_ton']} kWh/ton
    - Gap: {prediction_row['energy_gap_kwh']} kWh/ton
    - Top contributors: Mill power {prediction_row['mill_power_kw']} kW, 
                        ID fan {prediction_row['id_fan_speed_pct']}%
    
    Provide:
    1. Why this gap exists
    2. Specific action to close it
    3. Expected savings in $/day
    """
    
    response = gemini_model.generate_content(prompt)
    return response.text

# Result shown in dashboard:
# "Your mill is consuming 280 kW more than optimal for current load. 
#  Reduce separator speed by 3% to save 120 kW (~$230/day). 
#  This won't impact fineness based on historical patterns."
```

---

## Summary: BQML + Gemini = Agentic AI

1. **BQML models** = Fast, scalable predictions on BigQuery data
2. **Gemini Pro** = Explains "why" + generates actions + conversational interface
3. **Agent Builder** = Orchestrates workflow: Monitor → Predict → Decide → Approve → Act
4. **Vertex AI** = Advanced models (computer vision for equipment monitoring)

**Result:** Autonomous plant optimization with human-in-loop safety
