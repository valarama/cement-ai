-- ============================================================================
-- CEMENTAI OPTIMIZER - BIGQUERY ML MODELS
-- ============================================================================

-- ============================================================================
-- 1. SETUP: Create Dataset and Feature Tables
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS `cementai_prod`
OPTIONS (
  description = "CementAI Optimizer Production Dataset",
  location = "us-central1"
);

-- Raw OT Signals Table
CREATE OR REPLACE TABLE `cementai_prod.ot_signals` (
  timestamp TIMESTAMP,
  plant_id STRING,
  line_id STRING,
  -- Kiln & Preheater
  kiln_inlet_temp_c FLOAT64,
  kiln_outlet_temp_c FLOAT64,
  preheater_stage1_temp_c FLOAT64,
  preheater_stage2_temp_c FLOAT64,
  preheater_stage3_temp_c FLOAT64,
  preheater_stage4_temp_c FLOAT64,
  -- Fans
  id_fan_speed_pct FLOAT64,
  id_fan_power_kw FLOAT64,
  pa_fan_speed_pct FLOAT64,
  sa_fan_speed_pct FLOAT64,
  -- Mills
  raw_mill_power_kw FLOAT64,
  raw_mill_load_pct FLOAT64,
  finish_mill_power_kw FLOAT64,
  finish_mill_load_pct FLOAT64,
  separator_speed_pct FLOAT64,
  -- Dust Control
  bag_filter_dp_kpa FLOAT64,
  bag_reverse_cycle_s INT64,
  esp_load_pct FLOAT64,
  stack_temp_c FLOAT64,
  stack_pm_mg_per_nm3 FLOAT64,
  stack_opacity_pct FLOAT64,
  -- Process
  feed_rate_tph FLOAT64,
  clinker_temp_c FLOAT64,
  alt_fuel_pct FLOAT64,
  coal_feed_rate_tph FLOAT64,
  o2_pct FLOAT64,
  -- Energy
  total_power_kw FLOAT64,
  energy_kwh_per_ton FLOAT64
)
PARTITION BY DATE(timestamp)
CLUSTER BY plant_id, line_id;

-- ============================================================================
-- 2. FEATURE ENGINEERING
-- ============================================================================

CREATE OR REPLACE TABLE `cementai_prod.features` AS
WITH 
  base AS (
    SELECT *,
      -- Temperature deltas
      (kiln_outlet_temp_c - kiln_inlet_temp_c) AS temp_delta_c,
      -- Thermal efficiency proxy
      (clinker_temp_c / NULLIF(kiln_outlet_temp_c, 0)) AS thermal_efficiency,
      -- Stack heat loss
      (stack_temp_c - 100) * 1.2 AS stack_heat_loss_approx_kw
    FROM `cementai_prod.ot_signals`
    WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
  ),
  
  rolling_features AS (
    SELECT *,
      -- 5-minute rolling averages
      AVG(total_power_kw) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 300 PRECEDING AND CURRENT ROW
      ) AS roll5m_power_kw,
      
      AVG(energy_kwh_per_ton) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 300 PRECEDING AND CURRENT ROW
      ) AS roll5m_energy_kwh_ton,
      
      -- 1-hour rolling averages
      AVG(total_power_kw) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW
      ) AS roll1h_power_kw,
      
      -- Standard deviations (variability indicators)
      STDDEV(feed_rate_tph) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY UNIX_SECONDS(timestamp)
        RANGE BETWEEN 3600 PRECEDING AND CURRENT ROW
      ) AS feed_rate_variability,
      
      -- Lag features (previous hour)
      LAG(energy_kwh_per_ton, 12) OVER (
        PARTITION BY plant_id, line_id 
        ORDER BY timestamp
      ) AS energy_kwh_ton_lag1h
      
    FROM base
  )

SELECT * FROM rolling_features
WHERE energy_kwh_per_ton IS NOT NULL;

-- ============================================================================
-- 3. MODEL 1: ENERGY CONSUMPTION PREDICTOR
-- ============================================================================

CREATE OR REPLACE MODEL `cementai_prod.energy_regressor_v1`
OPTIONS(
  model_type='BOOSTED_TREE_REGRESSOR',
  input_label_cols=['energy_kwh_per_ton'],
  max_iterations=100,
  learn_rate=0.05,
  subsample=0.8,
  max_depth=8,
  min_tree_child_weight=10,
  l1_reg=0.1,
  l2_reg=0.1,
  early_stop=TRUE,
  data_split_method='AUTO_SPLIT',
  enable_global_explain=TRUE
) AS
SELECT
  -- Target
  energy_kwh_per_ton,
  -- Process features
  feed_rate_tph,
  alt_fuel_pct,
  coal_feed_rate_tph,
  o2_pct,
  -- Kiln features
  kiln_inlet_temp_c,
  kiln_outlet_temp_c,
  temp_delta_c,
  clinker_temp_c,
  thermal_efficiency,
  -- Fan features
  id_fan_speed_pct,
  id_fan_power_kw,
  pa_fan_speed_pct,
  sa_fan_speed_pct,
  -- Mill features
  raw_mill_power_kw,
  raw_mill_load_pct,
  finish_mill_power_kw,
  finish_mill_load_pct,
  separator_speed_pct,
  -- Dust control
  bag_filter_dp_kpa,
  stack_temp_c,
  stack_heat_loss_approx_kw,
  -- Rolling features
  roll5m_power_kw,
  roll1h_power_kw,
  feed_rate_variability,
  energy_kwh_ton_lag1h
FROM `cementai_prod.features`
WHERE timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  AND energy_kwh_per_ton BETWEEN 60 AND 150  -- Filter outliers
  AND feed_rate_tph > 0;

-- Evaluate model
SELECT
  mean_absolute_error,
  mean_squared_error,
  mean_squared_log_error,
  median_absolute_error,
  r2_score,
  explained_variance
FROM ML.EVALUATE(
  MODEL `cementai_prod.energy_regressor_v1`,
  (
    SELECT * FROM `cementai_prod.features`
    WHERE timestamp BETWEEN 
      TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
      AND TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
  )
);

-- Feature importance
SELECT
  feature,
  importance
FROM ML.FEATURE_IMPORTANCE(MODEL `cementai_prod.energy_regressor_v1`)
ORDER BY importance DESC
LIMIT 20;

-- ============================================================================
-- 4. MODEL 2: DUST EMISSION RISK CLASSIFIER
-- ============================================================================

-- Create binary labels for PM exceedances
CREATE OR REPLACE TABLE `cementai_prod.pm_labels` AS
SELECT
  timestamp,
  plant_id,
  line_id,
  stack_pm_mg_per_nm3,
  CASE 
    WHEN stack_pm_mg_per_nm3 > 50 THEN 1  -- Regulatory threshold
    ELSE 0
  END AS pm_exceed_flag
FROM `cementai_prod.ot_signals`
WHERE stack_pm_mg_per_nm3 IS NOT NULL;

CREATE OR REPLACE MODEL `cementai_prod.pm_risk_classifier_v1`
OPTIONS(
  model_type='LOGISTIC_REG',
  input_label_cols=['pm_exceed_flag'],
  l1_reg=0.01,
  l2_reg=0.01,
  enable_global_explain=TRUE,
  data_split_method='AUTO_SPLIT'
) AS
SELECT
  l.pm_exceed_flag,
  f.bag_filter_dp_kpa,
  f.bag_reverse_cycle_s,
  f.esp_load_pct,
  f.stack_temp_c,
  f.stack_opacity_pct,
  f.id_fan_speed_pct,
  f.feed_rate_tph,
  f.feed_rate_variability,
  -- Lag features
  LAG(f.bag_filter_dp_kpa, 6) OVER (
    PARTITION BY f.plant_id, f.line_id ORDER BY f.timestamp
  ) AS bag_dp_lag30m
FROM `cementai_prod.features` f
JOIN `cementai_prod.pm_labels` l USING (timestamp, plant_id, line_id)
WHERE f.timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);

-- Evaluate classifier
SELECT
  roc_auc,
  accuracy,
  precision,
  recall,
  f1_score,
  log_loss
FROM ML.EVALUATE(
  MODEL `cementai_prod.pm_risk_classifier_v1`,
  (
    SELECT
      l.pm_exceed_flag,
      f.*
    FROM `cementai_prod.features` f
    JOIN `cementai_prod.pm_labels` l USING (timestamp, plant_id, line_id)
    WHERE f.timestamp BETWEEN 
      TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
      AND TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY)
  )
);

-- ============================================================================
-- 5. MODEL 3: QUALITY DEVIATION PREDICTOR
-- ============================================================================

-- Assume quality lab data joined
CREATE OR REPLACE MODEL `cementai_prod.quality_classifier_v1`
OPTIONS(
  model_type='DNN_CLASSIFIER',
  input_label_cols=['quality_flag'],
  hidden_units=[128, 64, 32],
  dropout=0.2,
  batch_size=32,
  max_iterations=50,
  enable_global_explain=TRUE
) AS
SELECT
  CASE
    WHEN blaine_fineness BETWEEN 320 AND 350 
      AND strength_28d >= 43 THEN 'OK'
    WHEN blaine_fineness < 310 OR strength_28d < 40 THEN 'REJECT'
    ELSE 'WARNING'
  END AS quality_flag,
  raw_lsf_ratio,
  raw_sm_ratio,
  raw_am_ratio,
  clinker_temp_c,
  kiln_outlet_temp_c,
  alt_fuel_pct,
  feed_rate_variability,
  finish_mill_power_kw,
  separator_speed_pct
FROM `cementai_prod.quality_lab_data`
WHERE timestamp < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY);

-- ============================================================================
-- 6. REAL-TIME PREDICTION PIPELINE
-- ============================================================================

CREATE OR REPLACE TABLE `cementai_prod.predictions_live` AS
WITH
  latest_data AS (
    SELECT * FROM `cementai_prod.features`
    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)
  ),
  
  energy_preds AS (
    SELECT
      timestamp,
      plant_id,
      line_id,
      predicted_energy_kwh_per_ton
    FROM ML.PREDICT(
      MODEL `cementai_prod.energy_regressor_v1`,
      TABLE latest_data
    )
  ),
  
  pm_preds AS (
    SELECT
      timestamp,
      plant_id,
      line_id,
      predicted_pm_exceed_flag_probs[OFFSET(1)].prob AS pm_risk_probability,
      predicted_pm_exceed_flag
    FROM ML.PREDICT(
      MODEL `cementai_prod.pm_risk_classifier_v1`,
      TABLE latest_data
    )
  )

SELECT
  ld.*,
  ep.predicted_energy_kwh_per_ton,
  (ld.energy_kwh_per_ton - ep.predicted_energy_kwh_per_ton) AS energy_gap_kwh,
  pp.pm_risk_probability,
  pp.predicted_pm_exceed_flag
FROM latest_data ld
LEFT JOIN energy_preds ep USING (timestamp, plant_id, line_id)
LEFT JOIN pm_preds pp USING (timestamp, plant_id, line_id);

-- ============================================================================
-- 7. AI RECOMMENDATION ENGINE
-- ============================================================================

CREATE OR REPLACE TABLE `cementai_prod.ai_recommendations` AS
WITH
  current_state AS (
    SELECT * FROM `cementai_prod.predictions_live`
    WHERE timestamp = (SELECT MAX(timestamp) FROM `cementai_prod.predictions_live`)
  )

SELECT
  timestamp,
  plant_id,
  line_id,
  
  -- Recommendation logic
  CASE
    -- High PM risk
    WHEN pm_risk_probability > 0.7 AND bag_filter_dp_kpa < 2.5
      THEN 'PM_RISK_HIGH'
    
    -- Energy optimization opportunity
    WHEN energy_gap_kwh > 5
      THEN 'ENERGY_EXCESS'
    
    -- Stack heat loss
    WHEN stack_heat_loss_approx_kw > 500
      THEN 'HEAT_LOSS_HIGH'
    
    -- Mill overloading
    WHEN finish_mill_power_kw > 2000 AND finish_mill_load_pct < 70
      THEN 'MILL_INEFFICIENT'
    
    ELSE 'OPTIMAL'
  END AS recommendation_type,
  
  -- Specific action
  CASE
    WHEN pm_risk_probability > 0.7 AND bag_filter_dp_kpa < 2.5
      THEN CONCAT('Extend bag reverse cycle by +60s; reduce ID fan to ', 
                  CAST(ROUND(id_fan_speed_pct * 0.97) AS STRING), '%')
    
    WHEN energy_gap_kwh > 5
      THEN CONCAT('Reduce mill power by ', CAST(ROUND(energy_gap_kwh * 30) AS STRING), ' kW')
    
    WHEN stack_heat_loss_approx_kw > 500
      THEN CONCAT('Reduce ID fan speed from ', CAST(ROUND(id_fan_speed_pct) AS STRING), 
                  '% to ', CAST(ROUND(id_fan_speed_pct * 0.96) AS STRING), '%')
    
    WHEN finish_mill_power_kw > 2000 AND finish_mill_load_pct < 70
      THEN CONCAT('Reduce separator speed from ', CAST(ROUND(separator_speed_pct) AS STRING),
                  '% to ', CAST(ROUND(separator_speed_pct * 0.95) AS STRING), '%')
    
    ELSE 'No action required - operating optimally'
  END AS action_recommendation,
  
  -- Expected impact
  CASE
    WHEN pm_risk_probability > 0.7
      THEN CONCAT('Prevent PM spike (', CAST(ROUND(pm_risk_probability * 100) AS STRING), '% probability)')
    
    WHEN energy_gap_kwh > 5
      THEN CONCAT('Save ', CAST(ROUND(energy_gap_kwh * feed_rate_tph) AS STRING), 
                  ' kWh/hour = $', CAST(ROUND(energy_gap_kwh * feed_rate_tph * 0.08) AS STRING), '/hour')
    
    WHEN stack_heat_loss_approx_kw > 500
      THEN CONCAT('Recover ', CAST(ROUND(stack_heat_loss_approx_kw * 0.3) AS STRING), ' kW heat')
    
    ELSE NULL
  END AS expected_impact,
  
  -- Confidence score
  CASE
    WHEN pm_risk_probability > 0.7 THEN pm_risk_probability
    WHEN energy_gap_kwh > 5 THEN 0.92
    WHEN stack_heat_loss_approx_kw > 500 THEN 0.88
    ELSE 0.95
  END AS confidence_score,
  
  -- Priority
  CASE
    WHEN pm_risk_probability > 0.8 OR energy_gap_kwh > 10 THEN 'HIGH'
    WHEN pm_risk_probability > 0.6 OR energy_gap_kwh > 5 THEN 'MEDIUM'
    ELSE 'LOW'
  END AS priority,
  
  -- Current state (for context)
  energy_kwh_per_ton,
  predicted_energy_kwh_per_ton,
  energy_gap_kwh,
  pm_risk_probability,
  bag_filter_dp_kpa,
  stack_heat_loss_approx_kw,
  finish_mill_power_kw,
  separator_speed_pct,
  id_fan_speed_pct
  
FROM current_state;

-- ============================================================================
-- 8. MODEL MONITORING & RETRAINING
-- ============================================================================

CREATE OR REPLACE PROCEDURE `cementai_prod.retrain_models`()
BEGIN
  -- Retrain energy model with last 30 days
  CREATE OR REPLACE MODEL `cementai_prod.energy_regressor_v1`
  OPTIONS(
    model_type='BOOSTED_TREE_REGRESSOR',
    input_label_cols=['energy_kwh_per_ton'],
    max_iterations=100,
    learn_rate=0.05,
    subsample=0.8
  ) AS
  SELECT * FROM `cementai_prod.features`
  WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
    AND energy_kwh_per_ton IS NOT NULL;

  -- Log performance
  INSERT INTO `cementai_prod.model_performance_log` (
    timestamp,
    model_name,
    mae,
    rmse,
    r2_score
  )
  SELECT
    CURRENT_TIMESTAMP() AS timestamp,
    'energy_regressor_v1' AS model_name,
    mean_absolute_error AS mae,
    SQRT(mean_squared_error) AS rmse,
    r2_score
  FROM ML.EVALUATE(
    MODEL `cementai_prod.energy_regressor_v1`,
    (SELECT * FROM `cementai_prod.features`
     WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY))
  );
END;

-- ============================================================================
-- 9. SCHEDULED QUERIES
-- ============================================================================

-- Update predictions every 5 minutes
-- (Set up in Cloud Scheduler)

-- Retrain models daily at 2 AM
-- (Set up in Cloud Scheduler to call retrain_models procedure)
