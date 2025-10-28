#!/bin/bash
# ============================================================================
# CEMENTAI OPTIMIZER - COMPLETE GCP DEPLOYMENT SCRIPT
# ============================================================================

set -e  # Exit on error

# Configuration
PROJECT_ID="cementai-optimiser"
REGION="us-central1"
SERVICE_NAME="cementai-optimizer-api"
DATASET="cementai_prod"

echo "=========================================="
echo "CementAI Optimizer - GCP Deployment"
echo "=========================================="

# ============================================================================
# 1. SETUP GCP PROJECT
# ============================================================================

echo "Step 1: Setting up GCP project..."

# Set project
gcloud config set project $PROJECT_ID

# Enable required APIs
echo "Enabling APIs..."
gcloud services enable \
    aiplatform.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    pubsub.googleapis.com \
    vision.googleapis.com \
    iam.googleapis.com

# ============================================================================
# 2. CREATE STORAGE BUCKETS
# ============================================================================

echo "Step 2: Creating Cloud Storage buckets..."

gsutil mb -l $REGION gs://${PROJECT_ID}-staging 2>/dev/null || echo "Staging bucket exists"
gsutil mb -l $REGION gs://${PROJECT_ID}-models 2>/dev/null || echo "Models bucket exists"
gsutil mb -l $REGION gs://${PROJECT_ID}-data 2>/dev/null || echo "Data bucket exists"

# ============================================================================
# 3. SETUP BIGQUERY
# ============================================================================

echo "Step 3: Setting up BigQuery..."

# Create dataset
bq mk --dataset --location=$REGION ${PROJECT_ID}:${DATASET} 2>/dev/null || echo "Dataset exists"

# Run BigQuery ML models setup
echo "Creating BigQuery tables and models..."
bq query --use_legacy_sql=false < bigquery_models.sql

# ============================================================================
# 4. SETUP PUB/SUB FOR REAL-TIME DATA INGESTION
# ============================================================================

echo "Step 4: Setting up Pub/Sub..."

# Create topics
gcloud pubsub topics create ot-signals 2>/dev/null || echo "Topic ot-signals exists"
gcloud pubsub topics create ai-recommendations 2>/dev/null || echo "Topic ai-recommendations exists"

# Create subscriptions
gcloud pubsub subscriptions create ot-signals-sub \
    --topic=ot-signals \
    --ack-deadline=60 2>/dev/null || echo "Subscription exists"

# ============================================================================
# 5. DEPLOY CLOUD RUN API SERVICE
# ============================================================================

echo "Step 5: Deploying Cloud Run API service..."

# Build container
echo "Building container image..."
gcloud builds submit --tag gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest .

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy ${SERVICE_NAME} \
    --image gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --min-instances 1 \
    --max-instances 10 \
    --set-env-vars PROJECT_ID=${PROJECT_ID}

# Get service URL
SERVICE_URL=$(gcloud run services describe ${SERVICE_NAME} --region=$REGION --format='value(status.url)')
echo "API Service deployed at: $SERVICE_URL"

# ============================================================================
# 6. FINE-TUNE GEMINI PRO
# ============================================================================

echo "Step 6: Fine-tuning Gemini Pro..."

# Upload training data
python vertex_ai_integration.py

echo "Gemini fine-tuning initiated. Check Vertex AI console for progress."

# ============================================================================
# 7. SETUP SCHEDULED QUERIES
# ============================================================================

echo "Step 7: Setting up scheduled queries..."

# Create scheduled query for real-time predictions (every 5 minutes)
bq mk --transfer_config \
    --project_id=$PROJECT_ID \
    --data_source=scheduled_query \
    --display_name="CementAI Real-time Predictions" \
    --target_dataset=$DATASET \
    --schedule="every 5 minutes" \
    --params='{
        "query": "CREATE OR REPLACE TABLE `'$PROJECT_ID'.'$DATASET'.predictions_live` AS SELECT * FROM `'$PROJECT_ID'.'$DATASET'.features` WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 10 MINUTE)",
        "destination_table_name_template": "predictions_live",
        "write_disposition": "WRITE_TRUNCATE"
    }' 2>/dev/null || echo "Scheduled query exists"

# ============================================================================
# 8. SETUP IAM PERMISSIONS
# ============================================================================

echo "Step 8: Setting up IAM permissions..."

# Grant Cloud Run service account access to BigQuery
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format="value(projectNumber)")
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.dataEditor"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/bigquery.jobUser"

gcloud projects add-iam-policy-binding $PROJECT_ID \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/aiplatform.user"

# ============================================================================
# 9. SETUP MONITORING
# ============================================================================

echo "Step 9: Setting up monitoring..."

# Create log sink for API requests
gcloud logging sinks create cementai-api-logs \
    bigquery.googleapis.com/projects/${PROJECT_ID}/datasets/${DATASET} \
    --log-filter='resource.type="cloud_run_revision"' 2>/dev/null || echo "Log sink exists"

# ============================================================================
# 10. VERIFICATION
# ============================================================================

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "📍 API Service URL: $SERVICE_URL"
echo "📊 BigQuery Dataset: ${PROJECT_ID}:${DATASET}"
echo "🧠 Vertex AI Console: https://console.cloud.google.com/vertex-ai/models?project=${PROJECT_ID}"
echo ""
echo "Test the API:"
echo "curl ${SERVICE_URL}/health"
echo ""
echo "Get recommendations:"
echo "curl '${SERVICE_URL}/api/v1/recommendations?plant_id=plant_01&line_id=line_2'"
echo ""
echo "=========================================="

# ============================================================================
# 11. GENERATE SAMPLE DATA (OPTIONAL)
# ============================================================================

read -p "Generate sample data for testing? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]
then
    echo "Generating sample data..."
    python -c "
from google.cloud import bigquery
import random
from datetime import datetime, timedelta

client = bigquery.Client()

# Generate 1000 sample data points
rows = []
base_time = datetime.now() - timedelta(days=7)

for i in range(1000):
    timestamp = base_time + timedelta(minutes=i*5)
    row = {
        'timestamp': timestamp.isoformat(),
        'plant_id': 'plant_01',
        'line_id': 'line_2',
        'kiln_inlet_temp_c': random.uniform(1410, 1430),
        'kiln_outlet_temp_c': random.uniform(1440, 1460),
        'preheater_stage1_temp_c': random.uniform(850, 900),
        'preheater_stage2_temp_c': random.uniform(750, 800),
        'preheater_stage3_temp_c': random.uniform(650, 700),
        'preheater_stage4_temp_c': random.uniform(550, 600),
        'id_fan_speed_pct': random.uniform(70, 80),
        'id_fan_power_kw': random.uniform(45, 55),
        'pa_fan_speed_pct': random.uniform(60, 70),
        'sa_fan_speed_pct': random.uniform(50, 60),
        'raw_mill_power_kw': random.uniform(800, 900),
        'raw_mill_load_pct': random.uniform(60, 75),
        'finish_mill_power_kw': random.uniform(1800, 2000),
        'finish_mill_load_pct': random.uniform(65, 75),
        'separator_speed_pct': random.uniform(75, 85),
        'bag_filter_dp_kpa': random.uniform(2.0, 3.0),
        'bag_reverse_cycle_s': random.randint(100, 150),
        'esp_load_pct': random.uniform(70, 80),
        'stack_temp_c': random.uniform(340, 380),
        'stack_pm_mg_per_nm3': random.uniform(30, 60),
        'stack_opacity_pct': random.uniform(40, 60),
        'feed_rate_tph': random.uniform(135, 155),
        'clinker_temp_c': random.uniform(1435, 1450),
        'alt_fuel_pct': random.uniform(35, 50),
        'coal_feed_rate_tph': random.uniform(18, 22),
        'o2_pct': random.uniform(2.0, 3.5),
        'total_power_kw': random.uniform(3500, 4000),
        'energy_kwh_per_ton': random.uniform(85, 105)
    }
    rows.append(row)

# Insert data
table_id = f'${PROJECT_ID}.${DATASET}.ot_signals'
errors = client.insert_rows_json(table_id, rows)
if errors:
    print('Errors:', errors)
else:
    print(f'✅ Inserted {len(rows)} sample data points')
"
fi

echo ""
echo "=========================================="
echo "Setup complete! Check the links above to verify deployment."
echo "=========================================="
