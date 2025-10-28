# CementAI Optimizer - Quick Reference Card

## 🚀 Essential Commands

### Setup & Deployment
```bash
# Set project
gcloud config set project cementai-optimiser

# Deploy everything
chmod +x deploy.sh && ./deploy.sh

# Check API status
curl https://YOUR_API_URL/health
```

### BigQuery ML Models

**Train energy model:**
```sql
CREATE OR REPLACE MODEL `cementai_prod.energy_regressor_v1`
OPTIONS(model_type='BOOSTED_TREE_REGRESSOR', input_label_cols=['energy_kwh_per_ton'])
AS SELECT * FROM `cementai_prod.features`;
```

**Predict in real-time:**
```sql
SELECT * FROM ML.PREDICT(
  MODEL `cementai_prod.energy_regressor_v1`,
  (SELECT * FROM `cementai_prod.features` LIMIT 1)
);
```

**Get recommendations:**
```sql
SELECT * FROM `cementai_prod.ai_recommendations`
WHERE priority = 'HIGH'
ORDER BY timestamp DESC
LIMIT 5;
```

### Vertex AI / Gemini

**Fine-tune Gemini:**
```python
from vertexai.preview.tuning import sft

job = sft.train(
    source_model="gemini-1.5-pro-002",
    train_dataset="gs://your-bucket/training.jsonl",
    epochs=10,
    tuned_model_display_name="cementgpt-v1"
)
```

**Use CementGPT:**
```python
from vertexai.generative_models import GenerativeModel

model = GenerativeModel("cementgpt-v1")
response = model.generate_content("Your question here")
print(response.text)
```

### API Endpoints (curl examples)

**Get recommendations:**
```bash
curl "https://YOUR_API_URL/api/v1/recommendations?plant_id=plant_01&line_id=line_2"
```

**Predict energy:**
```bash
curl -X POST https://YOUR_API_URL/api/v1/predict/energy \
  -H "Content-Type: application/json" \
  -d '{
    "plant_id": "plant_01",
    "feed_rate_tph": 145,
    "mill_power_kw": 1850,
    "separator_speed_pct": 82
  }'
```

**Chat with AI:**
```bash
curl -X POST https://YOUR_API_URL/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Why is energy high?",
    "plant_id": "plant_01"
  }'
```

**Get real-time metrics:**
```bash
curl "https://YOUR_API_URL/api/v1/metrics/realtime?plant_id=plant_01&line_id=line_2"
```

### Monitoring

**View logs:**
```bash
gcloud logging read "resource.type=cloud_run_revision" --limit 50
```

**Check model performance:**
```sql
SELECT * FROM ML.EVALUATE(
  MODEL `cementai_prod.energy_regressor_v1`,
  (SELECT * FROM `cementai_prod.features` WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))
);
```

**Query audit log:**
```sql
SELECT * FROM `cementai_prod.action_audit_log`
ORDER BY timestamp DESC
LIMIT 100;
```

## 📊 Key Metrics Queries

**Energy savings today:**
```sql
SELECT
  DATE(timestamp) as date,
  AVG(energy_kwh_per_ton) as avg_energy,
  AVG(predicted_energy_kwh_per_ton) as optimal_energy,
  AVG(energy_gap_kwh) as avg_gap
FROM `cementai_prod.predictions_live`
WHERE DATE(timestamp) = CURRENT_DATE()
GROUP BY date;
```

**PM risk incidents:**
```sql
SELECT
  timestamp,
  pm_risk_probability,
  bag_filter_dp_kpa,
  stack_temp_c
FROM `cementai_prod.predictions_live`
WHERE pm_risk_probability > 0.7
  AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
ORDER BY pm_risk_probability DESC;
```

**Actions executed today:**
```sql
SELECT
  action_type,
  COUNT(*) as count,
  AVG(target_value) as avg_target
FROM `cementai_prod.action_audit_log`
WHERE DATE(timestamp) = CURRENT_DATE()
GROUP BY action_type;
```

## 🎯 Critical Questions for Plant

**Data Coverage Check:**
```
"What percentage of energy-relevant OT signals (kiln, mills, fans, stack, 
bag filters, utilities) are currently available in real time for AI optimization?"
```

**Signals Checklist:**
- [ ] Kiln inlet/outlet temps
- [ ] Preheater stage temps (1-4)
- [ ] ID/PA/SA fan speeds & power
- [ ] Mill power (raw + finish)
- [ ] Separator speed
- [ ] Bag filter DP, ESP load
- [ ] Stack temp, PM, opacity
- [ ] Feed rate, clinker temp
- [ ] Alternative fuel %
- [ ] Quality lab data (LSF/SM/AM, Blaine)

## 🔧 Troubleshooting

**Models not training:**
```bash
# Check BigQuery dataset
bq ls cementai_prod

# Check feature table
bq show cementai_prod.features
```

**API not responding:**
```bash
# Check Cloud Run logs
gcloud run services logs read cementai-optimizer-api --region=us-central1

# Restart service
gcloud run services update cementai-optimizer-api --region=us-central1
```

**Gemini fine-tuning status:**
```bash
# List tuning jobs
gcloud ai custom-jobs list --region=us-central1 --filter="displayName:cementgpt"
```

## 📞 Quick Contacts

- **API Docs:** https://api.cementai.com/docs
- **Dashboard:** https://console.cloud.google.com/run?project=cementai-optimiser
- **Support:** support@cementai.com

---

**Team:** Agentic Architects  
**Leader:** Ramamurthy Valavandan  
**Hackathon:** Google Cloud Gen AI Exchange 2025
