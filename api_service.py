"""
CementAI Optimizer - Cloud Run API Service
Real-time prediction and control API
"""

from flask import Flask, request, jsonify
from google.cloud import bigquery
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel
import os
from datetime import datetime

app = Flask(__name__)

# Initialize clients
PROJECT_ID = os.getenv("PROJECT_ID", "cementai-optimiser")
bq_client = bigquery.Client(project=PROJECT_ID)
aiplatform.init(project=PROJECT_ID, location="us-central1")

# Load fine-tuned model
TUNED_MODEL_ID = os.getenv("TUNED_MODEL_ID", "cementgpt-v1")
gemini_model = GenerativeModel(f"projects/{PROJECT_ID}/locations/us-central1/models/{TUNED_MODEL_ID}")

# ============================================================================
# API ENDPOINTS
# ============================================================================

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({"status": "healthy", "service": "cementai-optimizer"}), 200


@app.route('/api/v1/predict/energy', methods=['POST'])
def predict_energy():
    """
    Predict energy consumption for given plant parameters
    
    Request body:
    {
        "plant_id": "plant_01",
        "line_id": "line_2",
        "feed_rate_tph": 145,
        "alt_fuel_pct": 45,
        "mill_power_kw": 1850,
        "separator_speed_pct": 82,
        "id_fan_speed_pct": 76,
        "kiln_outlet_temp_c": 1445
    }
    """
    try:
        data = request.json
        
        # Create prediction query
        query = f"""
        SELECT predicted_energy_kwh_per_ton
        FROM ML.PREDICT(
          MODEL `cementai_prod.energy_regressor_v1`,
          (
            SELECT
              {data['feed_rate_tph']} AS feed_rate_tph,
              {data['alt_fuel_pct']} AS alt_fuel_pct,
              {data['mill_power_kw']} AS finish_mill_power_kw,
              {data['separator_speed_pct']} AS separator_speed_pct,
              {data['id_fan_speed_pct']} AS id_fan_speed_pct,
              {data['kiln_outlet_temp_c']} AS kiln_outlet_temp_c,
              0.0 AS raw_mill_power_kw,
              50.0 AS raw_mill_load_pct,
              0.0 AS pa_fan_speed_pct,
              0.0 AS sa_fan_speed_pct,
              1420 AS kiln_inlet_temp_c,
              25.0 AS temp_delta_c,
              1442 AS clinker_temp_c,
              0.98 AS thermal_efficiency,
              2.5 AS bag_filter_dp_kpa,
              360 AS stack_temp_c,
              300 AS stack_heat_loss_approx_kw,
              1800 AS roll5m_power_kw,
              1850 AS roll1h_power_kw,
              5.0 AS feed_rate_variability,
              92.0 AS energy_kwh_ton_lag1h,
              0 AS coal_feed_rate_tph,
              2.5 AS o2_pct,
              70 AS finish_mill_load_pct,
              50 AS id_fan_power_kw
          )
        )
        """
        
        result = bq_client.query(query).result()
        prediction = next(result).predicted_energy_kwh_per_ton
        
        return jsonify({
            "plant_id": data['plant_id'],
            "line_id": data['line_id'],
            "predicted_energy_kwh_per_ton": round(prediction, 2),
            "timestamp": datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/predict/pm_risk', methods=['POST'])
def predict_pm_risk():
    """
    Predict dust emission risk
    
    Request body:
    {
        "plant_id": "plant_01",
        "line_id": "line_2",
        "bag_filter_dp_kpa": 2.1,
        "bag_reverse_cycle_s": 120,
        "esp_load_pct": 75,
        "stack_temp_c": 370,
        "id_fan_speed_pct": 76
    }
    """
    try:
        data = request.json
        
        query = f"""
        SELECT
          predicted_pm_exceed_flag,
          predicted_pm_exceed_flag_probs[OFFSET(1)].prob AS pm_risk_probability
        FROM ML.PREDICT(
          MODEL `cementai_prod.pm_risk_classifier_v1`,
          (
            SELECT
              {data['bag_filter_dp_kpa']} AS bag_filter_dp_kpa,
              {data['bag_reverse_cycle_s']} AS bag_reverse_cycle_s,
              {data['esp_load_pct']} AS esp_load_pct,
              {data['stack_temp_c']} AS stack_temp_c,
              {data['id_fan_speed_pct']} AS id_fan_speed_pct,
              50.0 AS stack_opacity_pct,
              145.0 AS feed_rate_tph,
              5.0 AS feed_rate_variability,
              2.5 AS bag_dp_lag30m
          )
        )
        """
        
        result = bq_client.query(query).result()
        row = next(result)
        
        return jsonify({
            "plant_id": data['plant_id'],
            "line_id": data['line_id'],
            "pm_risk_probability": round(row.pm_risk_probability, 3),
            "predicted_exceedance": bool(row.predicted_pm_exceed_flag),
            "risk_level": "HIGH" if row.pm_risk_probability > 0.7 else "MEDIUM" if row.pm_risk_probability > 0.4 else "LOW",
            "timestamp": datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/recommendations', methods=['GET'])
def get_recommendations():
    """
    Get current AI recommendations
    Query params: plant_id, line_id
    """
    try:
        plant_id = request.args.get('plant_id', 'plant_01')
        line_id = request.args.get('line_id', 'line_2')
        
        query = f"""
        SELECT
          timestamp,
          recommendation_type,
          action_recommendation,
          expected_impact,
          confidence_score,
          priority,
          energy_kwh_per_ton,
          predicted_energy_kwh_per_ton,
          energy_gap_kwh,
          pm_risk_probability
        FROM `cementai_prod.ai_recommendations`
        WHERE plant_id = '{plant_id}'
          AND line_id = '{line_id}'
        ORDER BY timestamp DESC
        LIMIT 5
        """
        
        result = bq_client.query(query).result()
        
        recommendations = []
        for row in result:
            recommendations.append({
                "timestamp": row.timestamp.isoformat(),
                "type": row.recommendation_type,
                "action": row.action_recommendation,
                "impact": row.expected_impact,
                "confidence": round(row.confidence_score, 3),
                "priority": row.priority,
                "current_energy": round(row.energy_kwh_per_ton, 2),
                "optimal_energy": round(row.predicted_energy_kwh_per_ton, 2),
                "energy_gap": round(row.energy_gap_kwh, 2),
                "pm_risk": round(row.pm_risk_probability, 3) if row.pm_risk_probability else None
            })
        
        return jsonify({
            "plant_id": plant_id,
            "line_id": line_id,
            "recommendations": recommendations,
            "count": len(recommendations)
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/explain', methods=['POST'])
def explain_recommendation():
    """
    Generate natural language explanation using Gemini
    
    Request body:
    {
        "recommendation_type": "ENERGY_EXCESS",
        "current_energy": 95.2,
        "predicted_energy": 89.5,
        "action": "Reduce separator speed to 78%"
    }
    """
    try:
        data = request.json
        
        prompt = f"""
        You are CementGPT, an expert AI assistant for cement plant optimization.
        
        Explain this recommendation to a plant operator:
        
        Issue: {data['recommendation_type']}
        Current energy: {data['current_energy']} kWh/ton
        Optimal energy: {data['predicted_energy']} kWh/ton
        Recommended action: {data['action']}
        
        Provide a clear, concise explanation (3-4 sentences) covering:
        1. What the problem is
        2. Why the action will help
        3. Expected savings or improvement
        
        Be specific and use plant terminology.
        """
        
        response = gemini_model.generate_content(prompt)
        
        return jsonify({
            "recommendation": data,
            "explanation": response.text,
            "model": TUNED_MODEL_ID,
            "timestamp": datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/chat', methods=['POST'])
def chat_with_agent():
    """
    Conversational interface with CementGPT
    
    Request body:
    {
        "message": "Why did energy spike at 11:15 AM?",
        "plant_id": "plant_01",
        "line_id": "line_2"
    }
    """
    try:
        data = request.json
        user_message = data['message']
        plant_id = data.get('plant_id', 'plant_01')
        line_id = data.get('line_id', 'line_2')
        
        # Get recent plant context
        context_query = f"""
        SELECT *
        FROM `cementai_prod.predictions_live`
        WHERE plant_id = '{plant_id}'
          AND line_id = '{line_id}'
        ORDER BY timestamp DESC
        LIMIT 1
        """
        
        result = bq_client.query(context_query).result()
        context = next(result)
        
        # Build context-aware prompt
        prompt = f"""
        You are CementGPT, an expert AI assistant for cement plant optimization.
        
        Current plant state ({plant_id}/{line_id}):
        - Energy: {context.energy_kwh_per_ton} kWh/ton
        - Mill power: {context.finish_mill_power_kw} kW
        - Separator speed: {context.separator_speed_pct}%
        - ID fan: {context.id_fan_speed_pct}%
        - Stack temp: {context.stack_temp_c}°C
        - PM risk: {context.pm_risk_probability}
        
        User question: {user_message}
        
        Provide a helpful, specific answer based on the current plant state.
        """
        
        response = gemini_model.generate_content(prompt)
        
        return jsonify({
            "user_message": user_message,
            "agent_response": response.text,
            "plant_context": {
                "energy_kwh_per_ton": context.energy_kwh_per_ton,
                "mill_power_kw": context.finish_mill_power_kw,
                "pm_risk": context.pm_risk_probability
            },
            "timestamp": datetime.now().isoformat()
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/action/execute', methods=['POST'])
def execute_action():
    """
    Execute a control action (placeholder for DCS/PLC integration)
    
    Request body:
    {
        "plant_id": "plant_01",
        "line_id": "line_2",
        "action_type": "SEPARATOR_SPEED",
        "target_value": 78,
        "approved_by": "operator_123"
    }
    """
    try:
        data = request.json
        
        # Log action to audit trail
        audit_query = f"""
        INSERT INTO `cementai_prod.action_audit_log`
        (timestamp, plant_id, line_id, action_type, target_value, approved_by, status)
        VALUES (
            CURRENT_TIMESTAMP(),
            '{data['plant_id']}',
            '{data['line_id']}',
            '{data['action_type']}',
            {data['target_value']},
            '{data['approved_by']}',
            'EXECUTED'
        )
        """
        bq_client.query(audit_query).result()
        
        # TODO: Send command to DCS/PLC via OPC UA or REST API
        # control_response = send_to_dcs(data)
        
        return jsonify({
            "status": "SUCCESS",
            "message": f"Action {data['action_type']} executed",
            "target_value": data['target_value'],
            "timestamp": datetime.now().isoformat(),
            "audit_logged": True
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/v1/metrics/realtime', methods=['GET'])
def get_realtime_metrics():
    """
    Get real-time plant metrics
    Query params: plant_id, line_id
    """
    try:
        plant_id = request.args.get('plant_id', 'plant_01')
        line_id = request.args.get('line_id', 'line_2')
        
        query = f"""
        SELECT
          timestamp,
          energy_kwh_per_ton,
          predicted_energy_kwh_per_ton,
          energy_gap_kwh,
          total_power_kw,
          feed_rate_tph,
          finish_mill_power_kw,
          separator_speed_pct,
          id_fan_speed_pct,
          stack_temp_c,
          pm_risk_probability,
          bag_filter_dp_kpa,
          alt_fuel_pct
        FROM `cementai_prod.predictions_live`
        WHERE plant_id = '{plant_id}'
          AND line_id = '{line_id}'
        ORDER BY timestamp DESC
        LIMIT 1
        """
        
        result = bq_client.query(query).result()
        row = next(result)
        
        return jsonify({
            "plant_id": plant_id,
            "line_id": line_id,
            "timestamp": row.timestamp.isoformat(),
            "energy": {
                "current_kwh_per_ton": round(row.energy_kwh_per_ton, 2),
                "optimal_kwh_per_ton": round(row.predicted_energy_kwh_per_ton, 2),
                "gap_kwh_per_ton": round(row.energy_gap_kwh, 2),
                "total_power_kw": round(row.total_power_kw, 1)
            },
            "process": {
                "feed_rate_tph": round(row.feed_rate_tph, 1),
                "mill_power_kw": round(row.finish_mill_power_kw, 1),
                "separator_speed_pct": round(row.separator_speed_pct, 1),
                "id_fan_speed_pct": round(row.id_fan_speed_pct, 1),
                "stack_temp_c": round(row.stack_temp_c, 1),
                "alt_fuel_pct": round(row.alt_fuel_pct, 1)
            },
            "quality": {
                "pm_risk_probability": round(row.pm_risk_probability, 3),
                "bag_filter_dp_kpa": round(row.bag_filter_dp_kpa, 2)
            }
        }), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
