#!/bin/bash
# Package and upload deployment artifacts to S3
# Uses content-hash versioning for Lambda packages
#
# Usage:
#   ./package_and_upload.sh <bucket-name>                              # Full upload (demo mode)
#   ./package_and_upload.sh <bucket-name> --deploy-mode workshop       # Skip UI build and demo-mode Lambdas
#   ./package_and_upload.sh --workshop-studio <workshop-id>            # Upload to Workshop Studio assets
#   ./package_and_upload.sh --workshop-studio <workshop-id> --deploy-mode workshop

set -e

# Parse arguments
WORKSHOP_STUDIO=false
DEPLOY_MODE="demo"
WORKSHOP_ID=""
BUCKET=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --workshop-studio)
            WORKSHOP_STUDIO=true
            WORKSHOP_ID=$2
            shift 2
            ;;
        --deploy-mode)
            DEPLOY_MODE=$2
            shift 2
            ;;
        *)
            BUCKET=$1
            shift
            ;;
    esac
done

if [ "$WORKSHOP_STUDIO" = true ]; then
    if [ -z "$WORKSHOP_ID" ]; then
        echo "Usage: $0 --workshop-studio <workshop-id> [--deploy-mode workshop]"
        exit 1
    fi
    BUCKET="ws-assets-us-east-1"
    REGION="us-east-1"
    PREFIX="$WORKSHOP_ID"
    echo "Workshop Studio mode: uploading to s3://$BUCKET/$PREFIX/"
else
    REGION=${AWS_REGION:-us-west-2}
    PREFIX=""
    if [ -z "$BUCKET" ]; then
        echo "Usage: $0 <bucket-name> [--deploy-mode workshop]"
        echo "       $0 --workshop-studio <workshop-id> [--deploy-mode workshop]"
        exit 1
    fi
    echo "Packaging and uploading to s3://$BUCKET..."
fi

if [ "$DEPLOY_MODE" = "workshop" ]; then
    echo "Deploy mode: workshop (skipping UI build and demo-mode Lambdas)"
else
    echo "Deploy mode: demo (full packaging)"
fi

# Helper to build S3 path
s3_path() {
    local key=$1
    if [ -n "$PREFIX" ]; then
        echo "$PREFIX/$key"
    else
        echo "$key"
    fi
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(dirname "$SCRIPT_DIR")"
PROJECT_ROOT="$(dirname "$INFRA_DIR")"
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

echo "Packaging and uploading to s3://$BUCKET..."

# Function to package Lambda with hash-based versioning
package_lambda() {
    local name=$1
    local source_dir=$2
    shift 2
    local source_files=("$@")
    
    # Default to handler.py if no files specified
    if [ ${#source_files[@]} -eq 0 ]; then
        source_files=("handler.py")
    fi
    
    cd "$source_dir"
    zip -j "$TEMP_DIR/${name}.zip" "${source_files[@]}" > /dev/null
    
    # Compute hash
    if command -v sha256sum &> /dev/null; then
        HASH=$(sha256sum "$TEMP_DIR/${name}.zip" | cut -c1-8)
    else
        HASH=$(shasum -a 256 "$TEMP_DIR/${name}.zip" | cut -c1-8)
    fi
    
    local s3_key=$(s3_path "lambdas/${name}-${HASH}.zip")
    aws s3 cp "$TEMP_DIR/${name}.zip" "s3://$BUCKET/$s3_key" --region $REGION > /dev/null
    # Return just the relative key (without prefix) for CloudFormation params
    echo "lambdas/${name}-${HASH}.zip"
}

echo "Packaging Lambdas..."
DB_INIT_KEY=$(package_lambda "database_init" "$INFRA_DIR/custom-resource-lambdas/database_init")
echo "  database_init: $DB_INIT_KEY"

GLUE_KEY=$(package_lambda "glue_crawler_trigger" "$INFRA_DIR/custom-resource-lambdas/glue_crawler_trigger")
echo "  glue_crawler_trigger: $GLUE_KEY"

BEDROCK_KEY=$(package_lambda "bedrock_kb_ingestion" "$INFRA_DIR/custom-resource-lambdas/bedrock_kb_ingestion")
echo "  bedrock_kb_ingestion: $BEDROCK_KEY"

OBSERVABILITY_KEY=$(package_lambda "observability_setup" "$INFRA_DIR/custom-resource-lambdas/observability_setup")
echo "  observability_setup: $OBSERVABILITY_KEY"

# Package demo-mode artifacts (agent code, datafoundation Lambda, psycopg2 layer, amplify)
if [ "$DEPLOY_MODE" != "workshop" ]; then
    # Package datafoundation Lambda (tools for Gateway target)
    echo "Packaging datafoundation Lambda..."
    cd "$PROJECT_ROOT/app/agentcore_strands"
    zip -j "$TEMP_DIR/datafoundation_lambda.zip" tools/prebaked_sql_toolset_lambda.py > /dev/null
    aws s3 cp "$TEMP_DIR/datafoundation_lambda.zip" "s3://$BUCKET/$(s3_path "lambda/datafoundation_lambda.zip")" --region $REGION > /dev/null
    echo "  datafoundation: lambda/datafoundation_lambda.zip"

    # Package additional tool Lambdas (api_integration, custom_sql, semantic_layer)
    package_tool_lambda() {
        local short=$1
        local source_file=$2
        local pkg_dir="$TEMP_DIR/${short}_pkg"
        mkdir -p "$pkg_dir"
        cp "$PROJECT_ROOT/app/agentcore_strands/tools/$source_file" "$pkg_dir/"
        (cd "$pkg_dir" && zip -r "$TEMP_DIR/${short}.zip" . > /dev/null)
        if command -v sha256sum &> /dev/null; then
            HASH=$(sha256sum "$TEMP_DIR/${short}.zip" | cut -c1-8)
        else
            HASH=$(shasum -a 256 "$TEMP_DIR/${short}.zip" | cut -c1-8)
        fi
        local key="lambdas/${short}-${HASH}.zip"
        aws s3 cp "$TEMP_DIR/${short}.zip" "s3://$BUCKET/$(s3_path "$key")" --region $REGION > /dev/null
        echo "$key"
    }

    echo "Packaging api_integration_toolset Lambda..."
    API_INTEG_KEY=$(package_tool_lambda "api_integration_toolset" "api_integration_toolset_lambda.py")
    echo "  api_integration_toolset: $API_INTEG_KEY"

    echo "Packaging custom_sql_toolset Lambda..."
    CUSTOM_SQL_KEY=$(package_tool_lambda "custom_sql_toolset" "custom_sql_toolset_lambda.py")
    echo "  custom_sql_toolset: $CUSTOM_SQL_KEY"

    echo "Packaging semantic_layer_toolset Lambda..."
    SEMANTIC_LAYER_KEY=$(package_tool_lambda "semantic_layer_toolset" "semantic_layer_toolset_lambda.py")
    echo "  semantic_layer_toolset: $SEMANTIC_LAYER_KEY"

    # Package gateway interceptor Lambda (propagates Authorization header to targets)
    echo "Packaging gateway interceptor Lambda..."
    INTERCEPTOR_PKG="$TEMP_DIR/interceptor_pkg"
    mkdir -p "$INTERCEPTOR_PKG"
    cp "$PROJECT_ROOT/app/agentcore_strands/infra/interceptor_lambda.py" "$INTERCEPTOR_PKG/"
    cd "$INTERCEPTOR_PKG"
    zip -r "$TEMP_DIR/gateway_interceptor.zip" . > /dev/null
    if command -v sha256sum &> /dev/null; then
        HASH=$(sha256sum "$TEMP_DIR/gateway_interceptor.zip" | cut -c1-8)
    else
        HASH=$(shasum -a 256 "$TEMP_DIR/gateway_interceptor.zip" | cut -c1-8)
    fi
    INTERCEPTOR_KEY="lambdas/gateway_interceptor-${HASH}.zip"
    aws s3 cp "$TEMP_DIR/gateway_interceptor.zip" "s3://$BUCKET/$(s3_path "$INTERCEPTOR_KEY")" --region $REGION > /dev/null
    echo "  gateway_interceptor: $INTERCEPTOR_KEY"

    # Package agent code ZIP for AgentCore Runtime (CodeConfiguration)
    echo "Packaging agent code for AgentCore Runtime..."
    cd "$PROJECT_ROOT/app/agentcore_strands"
    zip -r "$TEMP_DIR/agent_code.zip" \
        unicorn_rental_agent.py \
        unicorn_rental_analytics.sop.md \
        requirements.txt \
        config.env.sample \
        -x "*.pyc" "*__pycache__*" > /dev/null
    aws s3 cp "$TEMP_DIR/agent_code.zip" "s3://$BUCKET/$(s3_path "agent/agent_code.zip")" --region $REGION > /dev/null
    echo "  agent_code: agent/agent_code.zip"

    # Package psycopg2 Lambda layer
    echo "Packaging psycopg2 Lambda layer..."
    LAYER_DIR="$TEMP_DIR/psycopg2_layer/python"
    mkdir -p "$LAYER_DIR"
    pip3 download psycopg2-binary \
        --platform manylinux2014_x86_64 \
        --only-binary=:all: \
        --python-version 312 \
        -d "$TEMP_DIR/psycopg2_downloads" 2>/dev/null
    WHEEL=$(ls "$TEMP_DIR/psycopg2_downloads"/*.whl 2>/dev/null | head -1)
    if [ -n "$WHEEL" ]; then
        unzip -q "$WHEEL" -d "$LAYER_DIR"
        cd "$TEMP_DIR/psycopg2_layer"
        zip -r "$TEMP_DIR/psycopg2-py312.zip" python > /dev/null
        aws s3 cp "$TEMP_DIR/psycopg2-py312.zip" "s3://$BUCKET/$(s3_path "layers/psycopg2-py312.zip")" --region $REGION > /dev/null
        echo "  psycopg2 layer: layers/psycopg2-py312.zip"
    else
        echo "  WARNING: Could not download psycopg2-binary wheel. Provide Psycopg2LayerArn param manually."
    fi

    # Package amplify_hosting Lambda
    echo "Packaging amplify_hosting (with common/ utilities)..."
    AMPLIFY_DIR="$INFRA_DIR/custom-resource-lambdas/amplify_hosting"
    mkdir -p "$TEMP_DIR/amplify_pkg/common"
    cp "$AMPLIFY_DIR/handler.py" "$TEMP_DIR/amplify_pkg/"
    cp "$PROJECT_ROOT/common/amplify_utils.py" "$TEMP_DIR/amplify_pkg/common/"
    cp "$PROJECT_ROOT/common/__init__.py" "$TEMP_DIR/amplify_pkg/common/"
    cd "$TEMP_DIR/amplify_pkg"
    zip -r "$TEMP_DIR/amplify_hosting.zip" . > /dev/null
    if command -v sha256sum &> /dev/null; then
        HASH=$(sha256sum "$TEMP_DIR/amplify_hosting.zip" | cut -c1-8)
    else
        HASH=$(shasum -a 256 "$TEMP_DIR/amplify_hosting.zip" | cut -c1-8)
    fi
    AMPLIFY_KEY="lambdas/amplify_hosting-${HASH}.zip"
    aws s3 cp "$TEMP_DIR/amplify_hosting.zip" "s3://$BUCKET/$(s3_path "$AMPLIFY_KEY")" --region $REGION > /dev/null
    echo "  amplify_hosting: $AMPLIFY_KEY"
else
    echo "Skipping demo-mode artifacts (workshop-only mode)"
    AMPLIFY_KEY="N/A"
    INTERCEPTOR_KEY="N/A"
    API_INTEG_KEY="N/A"
    CUSTOM_SQL_KEY="N/A"
    SEMANTIC_LAYER_KEY="N/A"
fi

echo "Uploading templates..."
cd "$INFRA_DIR/stacks"
aws s3 cp main-stack.yaml "s3://$BUCKET/$(s3_path "templates/main-stack.yaml")" --region $REGION > /dev/null
aws s3 cp aurora-stack.yaml "s3://$BUCKET/$(s3_path "templates/aurora-stack.yaml")" --region $REGION > /dev/null
aws s3 cp database-init-stack.yaml "s3://$BUCKET/$(s3_path "templates/database-init-stack.yaml")" --region $REGION > /dev/null
aws s3 cp glue-stack.yaml "s3://$BUCKET/$(s3_path "templates/glue-stack.yaml")" --region $REGION > /dev/null
aws s3 cp bedrock-kb-stack.yaml "s3://$BUCKET/$(s3_path "templates/bedrock-kb-stack.yaml")" --region $REGION > /dev/null
aws s3 cp agentcore-stack.yaml "s3://$BUCKET/$(s3_path "templates/agentcore-stack.yaml")" --region $REGION > /dev/null
aws s3 cp amplify-stack.yaml "s3://$BUCKET/$(s3_path "templates/amplify-stack.yaml")" --region $REGION > /dev/null
aws s3 cp cognito-stack.yaml "s3://$BUCKET/$(s3_path "templates/cognito-stack.yaml")" --region $REGION > /dev/null
aws s3 cp code-editor-stack.yaml "s3://$BUCKET/$(s3_path "templates/code-editor-stack.yaml")" --region $REGION > /dev/null
aws s3 cp observability-stack.yaml "s3://$BUCKET/$(s3_path "templates/observability-stack.yaml")" --region $REGION > /dev/null
aws s3 cp cube-stack.yaml "s3://$BUCKET/$(s3_path "templates/cube-stack.yaml")" --region $REGION > /dev/null

echo "Uploading schema and data..."
aws s3 cp "$PROJECT_ROOT/dataset/schema/schema.sql" "s3://$BUCKET/$(s3_path "schema/schema.sql")" --region $REGION > /dev/null
aws s3 sync "$PROJECT_ROOT/dataset/data/" "s3://$BUCKET/$(s3_path "data")/" --region $REGION > /dev/null
aws s3 cp "$PROJECT_ROOT/dataset/docs/business-context.md" "s3://$BUCKET/$(s3_path "docs/business-context.md")" --region $REGION > /dev/null

echo "Uploading SOP file..."
aws s3 cp "$PROJECT_ROOT/app/agentcore_strands/unicorn_rental_analytics.sop.md" "s3://$BUCKET/$(s3_path "sops/unicorn_rental_analytics.sop.md")" --region $REGION > /dev/null

echo "Uploading JDBC driver..."
# Download PostgreSQL JDBC driver if not present
DRIVER_PATH="$TEMP_DIR/postgresql-42.7.3.jar"
if [ ! -f "$DRIVER_PATH" ]; then
    curl -sL https://jdbc.postgresql.org/download/postgresql-42.7.3.jar -o "$DRIVER_PATH"
fi
aws s3 cp "$DRIVER_PATH" "s3://$BUCKET/$(s3_path "drivers/postgresql-42.7.3.jar")" --region $REGION > /dev/null

UI_BUILD_KEY="ui/build.zip"
if [ "$DEPLOY_MODE" != "workshop" ]; then
    echo "Building and uploading UI..."
    UI_DIR="$PROJECT_ROOT/app/ui"
    if [ -d "$UI_DIR" ]; then
        cd "$UI_DIR"
        npm install --silent
        npm run build --silent

        # Create ZIP of build directory and hash-version it so a UI source
        # change triggers a custom resource Update on subsequent deploys.
        cd build
        zip -r "$TEMP_DIR/ui-build.zip" . > /dev/null
        if command -v sha256sum &> /dev/null; then
            UI_HASH=$(sha256sum "$TEMP_DIR/ui-build.zip" | cut -c1-8)
        else
            UI_HASH=$(shasum -a 256 "$TEMP_DIR/ui-build.zip" | cut -c1-8)
        fi
        UI_BUILD_KEY="ui/build-${UI_HASH}.zip"
        aws s3 cp "$TEMP_DIR/ui-build.zip" "s3://$BUCKET/$(s3_path "$UI_BUILD_KEY")" --region $REGION > /dev/null
        echo "  Uploaded UI build: $UI_BUILD_KEY"
    else
        echo "  Warning: UI directory not found at $UI_DIR, skipping UI build"
    fi
else
    echo "Skipping UI build (workshop-only mode)"
fi

echo "Uploading project repo..."
cd "$PROJECT_ROOT"
# Create ZIP of entire repo (excluding .git, node_modules, etc.)
zip -r "$TEMP_DIR/repo.zip" . \
    -x "*.git*" \
    -x "*node_modules*" \
    -x "*.venv*" \
    -x "*__pycache__*" \
    -x "*.pyc" \
    -x "*app/ui/build*" \
    > /dev/null
aws s3 cp "$TEMP_DIR/repo.zip" "s3://$BUCKET/$(s3_path "repo/agentic-analytics.zip")" --region $REGION > /dev/null
echo "  Uploaded repo"

echo ""
echo "[OK] Upload complete"
echo ""
echo "Lambda S3 Keys:"
echo "  DatabaseInitLambdaKey=$DB_INIT_KEY"
echo "  GlueCrawlerLambdaKey=$GLUE_KEY"
echo "  BedrockKBLambdaKey=$BEDROCK_KEY"
echo "  AmplifyLambdaKey=$AMPLIFY_KEY"
echo "  InterceptorLambdaKey=$INTERCEPTOR_KEY"
echo "  ApiIntegLambdaKey=$API_INTEG_KEY"
echo "  CustomSqlLambdaKey=$CUSTOM_SQL_KEY"
echo "  SemanticLayerLambdaKey=$SEMANTIC_LAYER_KEY"
echo "  ObservabilityLambdaKey=$OBSERVABILITY_KEY"
echo "  AgentCodeS3Key=agent/agent_code.zip"
echo "  UIBuildKey=$UI_BUILD_KEY"
echo ""

if [ "$WORKSHOP_STUDIO" = true ]; then
    ARTIFACTS_BUCKET="ws-assets-us-east-1/$WORKSHOP_ID"
    TEMPLATE_URL="https://ws-assets-us-east-1.s3.us-east-1.amazonaws.com/$WORKSHOP_ID/templates/main-stack.yaml"
    DEPLOY_REGION="us-east-1"
else
    ARTIFACTS_BUCKET="$BUCKET"
    TEMPLATE_URL="https://$BUCKET.s3.$REGION.amazonaws.com/templates/main-stack.yaml"
    DEPLOY_REGION="$REGION"
fi

echo "Deploy command:"
echo "aws cloudformation create-stack \\"
echo "  --stack-name agentic-analytics-$DEPLOY_MODE \\"
echo "  --template-url $TEMPLATE_URL \\"
echo "  --parameters \\"
echo "      ParameterKey=ArtifactsBucket,ParameterValue=$ARTIFACTS_BUCKET \\"
echo "      ParameterKey=DeployMode,ParameterValue=$DEPLOY_MODE \\"
echo "      ParameterKey=DeployCube,ParameterValue=false \\"
echo "      ParameterKey=DatabaseInitLambdaKey,ParameterValue=$DB_INIT_KEY \\"
echo "      ParameterKey=GlueCrawlerLambdaKey,ParameterValue=$GLUE_KEY \\"
echo "      ParameterKey=BedrockKBLambdaKey,ParameterValue=$BEDROCK_KEY \\"
echo "      ParameterKey=AgentCodeS3Key,ParameterValue=agent/agent_code.zip \\"
echo "      ParameterKey=AmplifyLambdaKey,ParameterValue=$AMPLIFY_KEY \\"
echo "      ParameterKey=InterceptorLambdaKey,ParameterValue=$INTERCEPTOR_KEY \\"
echo "      ParameterKey=ApiIntegLambdaKey,ParameterValue=$API_INTEG_KEY \\"
echo "      ParameterKey=CustomSqlLambdaKey,ParameterValue=$CUSTOM_SQL_KEY \\"
echo "      ParameterKey=SemanticLayerLambdaKey,ParameterValue=$SEMANTIC_LAYER_KEY \\"
echo "      ParameterKey=ObservabilityLambdaKey,ParameterValue=$OBSERVABILITY_KEY \\"
echo "      ParameterKey=UIBuildKey,ParameterValue=$UI_BUILD_KEY \\"
echo "  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \\"
echo "  --region $DEPLOY_REGION"
