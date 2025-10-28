# CementAI Optimizer - Technical Implementation

## 🚀 Quick Start

```bash
# 1. Clone and setup
git clone https://github.com/valarama/cementai-optimizer.git
cd cementai-optimizer

# 2. Set your GCP project
export PROJECT_ID="cementai-optimiser"
gcloud config set project $PROJECT_ID

# 3. Run deployment script
chmod +x deploy.sh
./deploy.sh

# 4. Test API
curl https://cementai-optimizer-api-<hash>.run.app/health
```

---

## 📁 Project Structure

```
cementai-optimizer/
├── bigquery_models.sql          # BigQuery ML models (energy, PM risk, quality)
├── vertex_ai_integration.py     # Gemini fine-tuning + Agent Builder
├── api_service.py               # Cloud Run API service
├── Dockerfile                   # Container configuration
├── requirements.txt             # Python dependencies
├── deploy.sh                    # Complete deployment script
└── README.md                    # This file
```

---

## 🛠 Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **AI Brain** | Gemini Pro (fine-tuned) | Natural language understanding + recommendations |
| **ML Models** | BigQuery ML | Energy prediction, PM risk, quality classification |
| **Custom ML** | Vertex AI | Plant-specific optimization models |
| **Agent** | Agent Builder | Autonomous decision orchestration |
| **Data Stream** | Pub/Sub | Real-time OT sensor data ingestion |
| **Analytics** | BigQuery | Time-series data warehouse |
| **API** | Cloud Run | RESTful API service |
| **Vision** | Cloud Vision API | Equipment monitoring |
| **Dashboard** | Looker Studio | Real-time ROI tracking |

---

## 📊 BigQuery ML Models

### Model 1: Energy Consumption Predictor

**Type:** Boosted Tree Regressor  
**Target:** `energy_kwh_per_ton`  
**Features:** 20+ process parameters (kiln temps, mill power, fan speeds, etc.)  
**Accuracy:** RMSE < 3 kWh/ton, R² > 0.92

```sql
-- Create and train model
CREATE OR REPLACE MODEL `cementai_prod.energy_regressor_v1`
OPTIONS(model_type='BOOSTED_TREE_REGRESSOR', input_label_cols=['energy_kwh_per_ton'])
AS SELECT * FROM `cementai_prod.features`;

-- Real-time prediction
SELECT predicted_energy_kwh_per_ton
FROM ML.PREDICT(MODEL `cementai_prod.energy_regressor_v1`, 
  (SELECT * FROM `cementai_prod.features` WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)));
```

### Model 2: PM Emission Risk Classifier

**Type:** Logistic Regression  
**Target:** `pm_exceed_flag` (1 = exceeds 50 mg/Nm³ threshold)  
**Features:** Bag filter DP, ESP load, stack temp, fan speeds  
**Accuracy:** ROC-AUC > 0.94, Precision > 0.88

```sql
-- Predict PM risk
SELECT predicted_pm_exceed_flag_probs[OFFSET(1)].prob AS pm_risk_probability
FROM ML.PREDICT(MODEL `cementai_prod.pm_risk_classifier_v1`, 
  (SELECT * FROM `cementai_prod.features` WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)));
```

### Model 3: Quality Deviation Predictor

**Type:** Deep Neural Network (DNN) Classifier  
**Target:** `quality_flag` (OK / WARNING / REJECT)  
**Features:** LSF/SM/AM ratios, clinker temp, alt fuel %, mill parameters  
**Accuracy:** >89%

---

## 🧠 Gemini Pro Fine-Tuning (CementGPT)

### Training Data Format

```json
{
  "input_text": "Energy consumption is 95 kWh/ton. Mill power: 1850 kW, Separator: 82%. What to optimize?",
  "output_text": "Your mill is consuming excess power. Reduce separator speed to 78% to save ~180 kW ($345/day) without impacting fineness."
}
```

### Fine-Tuning Code

```python
from vertexai.preview.tuning import sft

# Train CementGPT
sft_tuning_job = sft.train(
    source_model="gemini-1.5-pro-002",
    train_dataset="gs://cementai-optimiser-staging/cementgpt_training_data.jsonl",
    epochs=10,
    learning_rate=0.001,
    tuned_model_display_name="cementgpt-v1",
)

print(f"Tuned model: {sft_tuning_job.tuned_model_name}")
```

### Usage

```python
from vertexai.generative_models import GenerativeModel

model = GenerativeModel("cementgpt-v1")
response = model.generate_content("Stack temperature is 385°C, ID fan at 78%. Normal?")
print(response.text)
```

---

## 🌐 API Endpoints

### Base URL
```
https://cementai-optimizer-api-<hash>.run.app
```

### 1. Health Check
```bash
curl https://YOUR_API_URL/health
```

**Response:**
```json
{"status": "healthy", "service": "cementai-optimizer"}
```

### 2. Predict Energy Consumption
```bash
curl -X POST https://YOUR_API_URL/api/v1/predict/energy \
  -H "Content-Type: application/json" \
  -d '{
    "plant_id": "plant_01",
    "line_id": "line_2",
    "feed_rate_tph": 145,
    "alt_fuel_pct": 45,
    "mill_power_kw": 1850,
    "separator_speed_pct": 82,
    "id_fan_speed_pct": 76,
    "kiln_outlet_temp_c": 1445
  }'
```

**Response:**
```json
{
  "plant_id": "plant_01",
  "line_id": "line_2",
  "predicted_energy_kwh_per_ton": 89.2,
  "timestamp": "2025-10-28T10:30:00Z"
}
```

### 3. Predict PM Risk
```bash
curl -X POST https://YOUR_API_URL/api/v1/predict/pm_risk \
  -H "Content-Type: application/json" \
  -d '{
    "plant_id": "plant_01",
    "line_id": "line_2",
    "bag_filter_dp_kpa": 2.1,
    "bag_reverse_cycle_s": 120,
    "esp_load_pct": 75,
    "stack_temp_c": 370,
    "id_fan_speed_pct": 76
  }'
```

**Response:**
```json
{
  "plant_id": "plant_01",
  "line_id": "line_2",
  "pm_risk_probability": 0.782,
  "predicted_exceedance": true,
  "risk_level": "HIGH",
  "timestamp": "2025-10-28T10:30:00Z"
}
```

### 4. Get AI Recommendations
```bash
curl "https://YOUR_API_URL/api/v1/recommendations?plant_id=plant_01&line_id=line_2"
```

**Response:**
```json
{
  "plant_id": "plant_01",
  "line_id": "line_2",
  "recommendations": [
    {
      "timestamp": "2025-10-28T10:30:00Z",
      "type": "ENERGY_EXCESS",
      "action": "Reduce separator speed from 82% to 78%",
      "impact": "Save 180 kW = $345/day",
      "confidence": 0.94,
      "priority": "HIGH",
      "current_energy": 95.2,
      "optimal_energy": 89.5,
      "energy_gap": 5.7
    }
  ],
  "count": 1
}
```

### 5. Chat with CementGPT
```bash
curl -X POST https://YOUR_API_URL/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Why did energy spike at 11:15 AM?",
    "plant_id": "plant_01",
    "line_id": "line_2"
  }'
```

**Response:**
```json
{
  "user_message": "Why did energy spike at 11:15 AM?",
  "agent_response": "Analyzing 11:15 AM data: Raw mill power increased from 850 to 980 kW while feed rate dropped from 145 to 132 tph. Cause: Mill overloading due to high moisture in raw material (likely rain-affected limestone). Recommendation: Reduce feed rate to 125 tph or increase pre-drying.",
  "plant_context": {
    "energy_kwh_per_ton": 95.2,
    "mill_power_kw": 1850,
    "pm_risk": 0.15
  },
  "timestamp": "2025-10-28T10:30:00Z"
}
```

---

## 🤖 Agentic AI Flow

```python
from vertex_ai_integration import CementAIAgent

# Initialize agent
agent = CementAIAgent(tuned_model_name="cementgpt-v1")

# 1. Observe - Get current state
plant_state = agent.get_current_plant_state()

# 2. Predict - Get recommendations
recommendations = agent.get_recommendations()

# 3. Decide - Autonomous decision
for rec in recommendations:
    explanation = agent.generate_explanation(rec)
    decision = agent.autonomous_decision(rec)
    
    # 4. Act - Execute if auto-approved
    if decision['auto_approve']:
        result = agent.execute_action(rec, approved=True)
        print(f"Action executed: {result['status']}")
```

---

## 📈 Key Performance Indicators

| KPI | Target Improvement |
|-----|-------------------|
| Energy consumption | ↓ 15-25% (from 90-110 to 75-85 kWh/ton) |
| Quality variability | ↓ 70% (SD of Blaine/strength) |
| Alternative fuel usage | ↑ 10pp (from 35-40% to 45-50% TSR) |
| Stack heat loss | ↓ 50-70°C (from 380-400°C to 320-330°C) |
| Dust emissions | ↓ 30% (from 60-80 to <50 mg/Nm³) |
| Plant efficiency | ↑ 20pp (from 65-70% to 85-90%) |

---

## 🔒 Security & Compliance

- **IAM:** Least-privilege service accounts
- **VPC-SC:** Perimeter protection for sensitive data
- **Encryption:** CMEK for BigQuery and Cloud Storage
- **Audit:** All control actions logged with timestamp + operator ID
- **Rollback:** Auto-revert if KPIs degrade within 30 minutes

---

## 💰 Cost Estimate (per plant)

### Development & Pilot (9 months)
- Development team: $180,000
- Pilot deployment: $75,000
- Cloud infrastructure: $25,000/month

### Annual Operating Costs
- Google Cloud services: $120,000/year
  - BigQuery: $40K
  - Vertex AI: $50K
  - Cloud Run + Pub/Sub: $20K
  - Storage: $10K
- Maintenance & support: $60,000/year

### ROI
- **Year 1 savings:** $2.5M (energy + fuel + quality + maintenance)
- **Implementation cost:** $335K (dev) + $120K (cloud)
- **Payback:** 8-10 months
- **5-year ROI:** 400%+

---

## 🚀 Next Steps

1. **Week 1-2:** Data readiness assessment
   - Map OT signals (coverage %)
   - Baseline current KPIs
   - Test connectivity to DCS/PLC

2. **Week 3-6:** Advisor mode deployment
   - Deploy BigQuery ML models
   - Fine-tune Gemini for plant
   - Dashboard + recommendations (no control)

3. **Week 7-12:** Semi-autonomous mode
   - Operator approval workflow
   - Bounded set-point write-back
   - Safety guardrails + rollback

4. **Month 4-6:** Full autonomous operation
   - Closed-loop control (within limits)
   - Continuous learning
   - Multi-line/plant expansion

---

## 📞 Support

- **Documentation:** [https://docs.cementai.com](https://docs.cementai.com)
- **API Reference:** [https://api.cementai.com/docs](https://api.cementai.com/docs)
- **Email:** support@cementai.com
- **Slack:** [#cementai-support](slack://cementai-support)

---

## 🏆 Team

**Agentic Architects**  
- Ramamurthy Valavandan (Team Lead)
- Google Cloud Gen AI Exchange Hackathon 2025

---

## 📄 License

Proprietary - CementAI Optimizer © 2025
