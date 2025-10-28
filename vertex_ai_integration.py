"""
CementAI Optimizer - Vertex AI Integration
Gemini Pro Fine-tuning + Agent Builder + Prediction Pipeline
"""

import os
from google.cloud import aiplatform
from google.cloud import bigquery
from vertexai.generative_models import GenerativeModel
from vertexai.preview.tuning import sft
import json
from datetime import datetime

# ============================================================================
# 1. INITIALIZATION
# ============================================================================

PROJECT_ID = "cementai-optimiser"
LOCATION = "us-central1"
STAGING_BUCKET = f"gs://{PROJECT_ID}-staging"

aiplatform.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)
bq_client = bigquery.Client(project=PROJECT_ID)

# ============================================================================
# 2. GEMINI PRO FINE-TUNING FOR CEMENT OPERATIONS
# ============================================================================

def create_training_data():
    """
    Create training dataset for CementGPT fine-tuning
    Format: {"input_text": "...", "output_text": "..."}
    """
    
    training_examples = [
        {
            "input_text": "Energy consumption is 95 kWh/ton. Mill power: 1850 kW, Separator speed: 82%, Feed rate: 145 tph, ID fan: 76%. What should I optimize?",
            "output_text": "Your mill is consuming excess power relative to the feed rate. The separator speed of 82% is too high for 145 tph throughput. Recommendation: Reduce separator speed to 78% to save approximately 180 kW (~$345/day) without impacting fineness. Monitor Blaine after adjustment."
        },
        {
            "input_text": "Stack temperature is 385°C, ID fan at 78%, kiln outlet temp 1445°C. Normal?",
            "output_text": "Stack temperature is elevated. At 385°C, you're losing ~120 kW of recoverable heat. Root cause: ID fan is pulling excess air. Reduce ID fan speed from 78% to 74% to lower stack temp to 350-360°C and improve thermal efficiency by ~2-3%."
        },
        {
            "input_text": "Bag filter DP dropped from 2.8 to 2.1 kPa in last hour. Stack PM at 48 mg/Nm³. Action needed?",
            "output_text": "Dropping DP indicates filter cake thinning or potential leak. PM is near threshold (50 mg/Nm³). Immediate action: Extend reverse cleaning cycle from current setting to +60 seconds. If DP doesn't recover in 30 minutes, inspect for bag damage. Risk of PM exceedance: 75%."
        },
        {
            "input_text": "Alternative fuel at 35%, can we increase to 50%? Clinker temp 1442°C, Free lime 0.9%.",
            "output_text": "Yes, but incrementally. Current clinker chemistry is stable (free lime <1%). Increase alternative fuel by +5pp to 40%, monitor for 2 hours. Watch for: (1) Clinker temp drop >10°C, (2) Free lime spike >1.2%, (3) Ring formation. If stable, proceed to 45%, then 50% over 8 hours."
        },
        {
            "input_text": "Energy spiked to 102 kWh/ton at 11:15 AM. Why?",
            "output_text": "Analyzing 11:15 AM data: Raw mill power increased from 850 to 980 kW while feed rate dropped from 145 to 132 tph. Cause: Mill overloading due to high moisture in raw material (likely rain-affected limestone). Recommendation: Reduce feed rate further to 125 tph or increase pre-drying."
        },
        {
            "input_text": "LSF is 95, SM is 2.4, AM is 1.6. Clinker quality OK?",
            "output_text": "Chemistry is excellent. LSF at 95 is ideal for strength development (optimal 90-98). SM of 2.4 provides good burnability. AM of 1.6 ensures proper flux for liquid phase formation. Expect: High C3S content, good 28-day strength (>43 MPa), low free lime risk."
        },
        {
            "input_text": "Finish mill power 1920 kW, load 68%, Blaine 335 m²/kg. Can we save energy?",
            "output_text": "Mill is operating efficiently but can be optimized. At 68% load with Blaine 335, you have headroom. Recommendation: Reduce separator speed by 2-3% to lower power by 80-100 kW. Blaine may drop to 330-332, which is still within spec for most cement types (320-350)."
        },
        {
            "input_text": "Kiln outlet temp fluctuating between 1430-1460°C. Stabilize?",
            "output_text": "Fluctuations indicate feed chemistry variability or burner instability. Solutions: (1) Improve raw mix homogenization in silos, (2) Enable feed-forward control using raw mill XRF data, (3) Adjust burner momentum if using alternative fuels. Target: <10°C variation per hour."
        },
        {
            "input_text": "CO at 350 ppm, O2 at 2.5%. Is combustion optimal?",
            "output_text": "CO is elevated (optimal <200 ppm) indicating incomplete combustion. O2 at 2.5% is acceptable but on lower end. Likely cause: Poor fuel-air mixing or wet fuel. Action: Increase primary air by 3-5% or check fuel moisture content. This will also reduce NOx formation."
        },
        {
            "input_text": "Preheater stage 4 temp is 780°C, should be 850°C. Problem?",
            "output_text": "Low preheater temp indicates heat transfer issue. Possible causes: (1) Coating buildup reducing heat exchange, (2) Bypass damper partially open, (3) Feed rate too high for kiln load. Check: Preheater pressure profile, bypass valve position, cyclone efficiency. May need shutdown for cleaning if persistent."
        }
    ]
    
    # Save to JSONL format
    training_file = f"{STAGING_BUCKET}/cementgpt_training_data.jsonl"
    with open("/tmp/training_data.jsonl", "w") as f:
        for ex in training_examples:
            f.write(json.dumps(ex) + "\n")
    
    # Upload to GCS
    os.system(f"gsutil cp /tmp/training_data.jsonl {training_file}")
    
    return training_file


def fine_tune_gemini():
    """
    Fine-tune Gemini Pro on cement plant operations
    """
    training_data_uri = create_training_data()
    
    print("Starting Gemini Pro fine-tuning...")
    
    # Initialize supervised fine-tuning
    sft_tuning_job = sft.train(
        source_model="gemini-1.5-pro-002",
        train_dataset=training_data_uri,
        # Tuning parameters
        epochs=10,
        learning_rate=0.001,
        tuned_model_display_name="cementgpt-v1",
    )
    
    print(f"Tuning job: {sft_tuning_job.resource_name}")
    print(f"Tuned model: {sft_tuning_job.tuned_model_name}")
    
    return sft_tuning_job.tuned_model_name


# ============================================================================
# 3. PREDICTION ENDPOINT DEPLOYMENT
# ============================================================================

def deploy_prediction_endpoint():
    """
    Deploy BQML models as Vertex AI endpoints for real-time inference
    """
    
    # Export BQML model to GCS
    export_query = """
    EXPORT MODEL `cementai_prod.energy_regressor_v1`
    OPTIONS(URI='gs://cementai-optimiser-staging/bqml_models/energy_regressor')
    """
    bq_client.query(export_query).result()
    
    # Register model in Vertex AI Model Registry
    model = aiplatform.Model.upload(
        display_name="cement-energy-predictor",
        artifact_uri="gs://cementai-optimiser-staging/bqml_models/energy_regressor",
        serving_container_image_uri="us-docker.pkg.dev/vertex-ai/prediction/tf2-cpu.2-11:latest",
    )
    
    # Deploy to endpoint
    endpoint = model.deploy(
        machine_type="n1-standard-4",
        min_replica_count=1,
        max_replica_count=3,
        traffic_percentage=100,
    )
    
    print(f"Model deployed to endpoint: {endpoint.resource_name}")
    return endpoint


# ============================================================================
# 4. VERTEX AI AGENT BUILDER INTEGRATION
# ============================================================================

class CementAIAgent:
    """
    Agentic AI orchestrator using Vertex AI Agent Builder
    """
    
    def __init__(self, tuned_model_name):
        self.model = GenerativeModel(tuned_model_name)
        self.bq_client = bigquery.Client()
    
    def get_current_plant_state(self, plant_id="plant_01", line_id="line_2"):
        """
        Fetch latest plant state from BigQuery
        """
        query = f"""
        SELECT *
        FROM `cementai_prod.predictions_live`
        WHERE plant_id = '{plant_id}'
          AND line_id = '{line_id}'
        ORDER BY timestamp DESC
        LIMIT 1
        """
        
        result = self.bq_client.query(query).result()
        for row in result:
            return dict(row.items())
        return None
    
    def get_recommendations(self, plant_id="plant_01", line_id="line_2"):
        """
        Fetch AI recommendations from BigQuery
        """
        query = f"""
        SELECT *
        FROM `cementai_prod.ai_recommendations`
        WHERE plant_id = '{plant_id}'
          AND line_id = '{line_id}'
        ORDER BY timestamp DESC
        LIMIT 5
        """
        
        result = self.bq_client.query(query).result()
        recommendations = []
        for row in result:
            recommendations.append(dict(row.items()))
        return recommendations
    
    def generate_explanation(self, recommendation):
        """
        Use fine-tuned Gemini to generate natural language explanation
        """
        prompt = f"""
        Explain this cement plant optimization recommendation:
        
        Current State:
        - Energy consumption: {recommendation.get('energy_kwh_per_ton', 'N/A')} kWh/ton
        - Predicted optimal: {recommendation.get('predicted_energy_kwh_per_ton', 'N/A')} kWh/ton
        - Gap: {recommendation.get('energy_gap_kwh', 'N/A')} kWh/ton
        - PM risk: {recommendation.get('pm_risk_probability', 'N/A')}
        - Stack heat loss: {recommendation.get('stack_heat_loss_approx_kw', 'N/A')} kW
        
        Recommendation Type: {recommendation.get('recommendation_type', 'N/A')}
        Action: {recommendation.get('action_recommendation', 'N/A')}
        Expected Impact: {recommendation.get('expected_impact', 'N/A')}
        Confidence: {recommendation.get('confidence_score', 'N/A')}
        
        Provide a clear, operator-friendly explanation of:
        1. Why this action is recommended
        2. How it will improve operations
        3. Any risks or considerations
        """
        
        response = self.model.generate_content(prompt)
        return response.text
    
    def autonomous_decision(self, recommendation):
        """
        Agentic decision-making: Should this action be taken automatically?
        """
        # Safety checks
        confidence = recommendation.get('confidence_score', 0)
        priority = recommendation.get('priority', 'LOW')
        
        # Auto-approve criteria
        auto_approve = (
            confidence > 0.90 and
            priority in ['MEDIUM', 'LOW'] and
            recommendation.get('recommendation_type') not in ['PM_RISK_HIGH']  # High-risk needs human
        )
        
        return {
            "auto_approve": auto_approve,
            "reason": "High confidence, low risk" if auto_approve else "Requires human approval",
            "approval_required": not auto_approve
        }
    
    def execute_action(self, recommendation, approved=False):
        """
        Execute control action (write to DCS/PLC via API)
        This is a placeholder - actual implementation would call plant control API
        """
        if not approved:
            return {"status": "PENDING_APPROVAL"}
        
        action = recommendation.get('action_recommendation', '')
        
        # Parse action and generate control commands
        # Example: "Reduce separator speed from 82% to 78%"
        
        control_command = {
            "timestamp": datetime.now().isoformat(),
            "plant_id": recommendation.get('plant_id'),
            "line_id": recommendation.get('line_id'),
            "action": action,
            "recommendation_id": recommendation.get('timestamp'),
            "status": "EXECUTED"
        }
        
        # Log to audit trail
        audit_query = f"""
        INSERT INTO `cementai_prod.action_audit_log`
        (timestamp, plant_id, line_id, action, recommendation_type, approved_by, status)
        VALUES (
            CURRENT_TIMESTAMP(),
            '{recommendation.get('plant_id')}',
            '{recommendation.get('line_id')}',
            '{action}',
            '{recommendation.get('recommendation_type')}',
            'SYSTEM_AUTO',
            'EXECUTED'
        )
        """
        self.bq_client.query(audit_query).result()
        
        return control_command


# ============================================================================
# 5. COMPUTER VISION FOR EQUIPMENT MONITORING
# ============================================================================

from google.cloud import vision

def analyze_equipment_image(image_path):
    """
    Use Cloud Vision API to detect equipment issues
    """
    client = vision.ImageAnnotatorClient()
    
    with open(image_path, 'rb') as image_file:
        content = image_file.read()
    
    image = vision.Image(content=content)
    
    # Object detection
    objects = client.object_localization(image=image).localized_object_annotations
    
    # Label detection (for condition assessment)
    labels = client.label_detection(image=image).label_annotations
    
    # Detect anomalies
    anomalies = []
    for label in labels:
        if any(keyword in label.description.lower() for keyword in ['crack', 'damage', 'wear', 'leak']):
            anomalies.append({
                "type": label.description,
                "confidence": label.score,
                "severity": "HIGH" if label.score > 0.8 else "MEDIUM"
            })
    
    return {
        "objects_detected": [obj.name for obj in objects],
        "anomalies": anomalies,
        "maintenance_required": len(anomalies) > 0
    }


# ============================================================================
# 6. MAIN ORCHESTRATION
# ============================================================================

def main():
    """
    Main orchestration flow: Observe → Predict → Decide → Act
    """
    print("=== CementAI Optimizer - Agentic AI Execution ===\n")
    
    # Step 1: Fine-tune Gemini (one-time)
    # tuned_model = fine_tune_gemini()
    tuned_model = "projects/cementai-optimiser/locations/us-central1/models/cementgpt-v1"
    
    # Step 2: Initialize agent
    agent = CementAIAgent(tuned_model)
    
    # Step 3: Observe - Get current state
    print("1. OBSERVE: Fetching plant state...")
    plant_state = agent.get_current_plant_state()
    print(f"   Energy: {plant_state.get('energy_kwh_per_ton')} kWh/ton")
    print(f"   PM Risk: {plant_state.get('pm_risk_probability')}\n")
    
    # Step 4: Predict - Get recommendations
    print("2. PREDICT: Analyzing optimization opportunities...")
    recommendations = agent.get_recommendations()
    print(f"   Found {len(recommendations)} recommendations\n")
    
    # Step 5: Decide - For each recommendation
    for idx, rec in enumerate(recommendations):
        print(f"3. RECOMMENDATION #{idx + 1}:")
        print(f"   Type: {rec.get('recommendation_type')}")
        print(f"   Action: {rec.get('action_recommendation')}")
        print(f"   Impact: {rec.get('expected_impact')}")
        print(f"   Priority: {rec.get('priority')}\n")
        
        # Generate explanation
        print("4. EXPLAIN:")
        explanation = agent.generate_explanation(rec)
        print(f"   {explanation}\n")
        
        # Autonomous decision
        print("5. DECIDE:")
        decision = agent.autonomous_decision(rec)
        print(f"   Auto-approve: {decision['auto_approve']}")
        print(f"   Reason: {decision['reason']}\n")
        
        # Execute if auto-approved
        if decision['auto_approve']:
            print("6. ACT:")
            result = agent.execute_action(rec, approved=True)
            print(f"   Status: {result['status']}\n")
        else:
            print("6. ACT: Awaiting human approval\n")
        
        print("-" * 60 + "\n")


if __name__ == "__main__":
    main()
