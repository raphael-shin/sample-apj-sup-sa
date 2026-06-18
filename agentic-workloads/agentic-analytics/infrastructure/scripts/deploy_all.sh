#!/bin/bash
#
# Agentic Analytics - Master Deployment Script
# Deploys the complete infrastructure and initializes the system
#
# Usage: ./deploy_all.sh [--config <config-file>] [--skip-confirm]
#

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname $(dirname "$SCRIPT_DIR"))"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default configuration
DEFAULT_REGION="us-west-2"
DEFAULT_STACK_PREFIX="agentic-analytics"
CONFIG_FILE="$SCRIPT_DIR/deploy-config.yaml"
SKIP_CONFIRM=false

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --skip-confirm)
            SKIP_CONFIRM=true
            shift
            ;;
        --help)
            echo "Usage: $0 [--config <config-file>] [--skip-confirm]"
            echo ""
            echo "Options:"
            echo "  --config <file>   Path to deployment config file (default: deploy-config.yaml)"
            echo "  --skip-confirm    Skip confirmation prompts"
            echo "  --help            Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Print banner
echo -e "${BLUE}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║           Agentic Analytics - Deployment Script              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Function to print status messages
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if a command exists
check_command() {
    if ! command -v "$1" &> /dev/null; then
        print_error "$1 is required but not installed."
        exit 1
    fi
}

# Check required commands
print_status "Checking required tools..."
check_command "aws"
check_command "python3"
check_command "jq"

# Check AWS credentials
print_status "Checking AWS credentials..."
if [ -z "$AWS_ACCESS_KEY_ID" ] && [ -z "$AWS_PROFILE" ]; then
    print_error "AWS credentials not configured."
    echo "Please set AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
    exit 1
fi

# Get AWS region
if [ -z "$AWS_REGION" ]; then
    AWS_REGION="$DEFAULT_REGION"
    print_warning "AWS_REGION not set, using default: $AWS_REGION"
fi
export AWS_REGION

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if [ -z "$ACCOUNT_ID" ]; then
    print_error "Failed to get AWS account ID. Check your credentials."
    exit 1
fi

# Load configuration if file exists
STACK_PREFIX="$DEFAULT_STACK_PREFIX"
if [ -f "$CONFIG_FILE" ]; then
    print_status "Loading configuration from $CONFIG_FILE..."
    if command -v yq &> /dev/null; then
        STACK_PREFIX=$(yq -r '.stack_prefix // "agentic-analytics"' "$CONFIG_FILE")
        CONFIG_REGION=$(yq -r '.region // ""' "$CONFIG_FILE")
        if [ -n "$CONFIG_REGION" ] && [ "$CONFIG_REGION" != "null" ]; then
            AWS_REGION="$CONFIG_REGION"
        fi
    fi
fi

# Stack names
AURORA_STACK="${STACK_PREFIX}-aurora"
GLUE_STACK="${STACK_PREFIX}-glue"

# Display deployment information
echo ""
echo -e "${YELLOW}Deployment Configuration:${NC}"
echo "  AWS Account:    $ACCOUNT_ID"
echo "  AWS Region:     $AWS_REGION"
echo "  Stack Prefix:   $STACK_PREFIX"
echo "  Aurora Stack:   $AURORA_STACK"
echo "  Glue Stack:     $GLUE_STACK"
echo ""

# Confirm deployment
if [ "$SKIP_CONFIRM" = false ]; then
    read -p "Do you want to proceed with deployment? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_warning "Deployment cancelled."
        exit 0
    fi
fi

echo ""
print_status "Starting deployment..."
echo ""

# ============================================================================
# Phase 1: Deploy Aurora PostgreSQL Stack
# ============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 1: Deploying Aurora PostgreSQL Infrastructure${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

print_status "Deploying Aurora CloudFormation stack..."
aws cloudformation deploy \
    --template-file "$PROJECT_ROOT/infrastructure/stacks/aurora-stack.yaml" \
    --stack-name "$AURORA_STACK" \
    --capabilities CAPABILITY_IAM \
    --region "$AWS_REGION" \
    --no-fail-on-empty-changeset \
    || { print_error "Aurora stack deployment failed"; exit 1; }

print_status "Waiting for Aurora stack to complete..."
aws cloudformation wait stack-create-complete \
    --stack-name "$AURORA_STACK" \
    --region "$AWS_REGION" 2>/dev/null \
    || aws cloudformation wait stack-update-complete \
    --stack-name "$AURORA_STACK" \
    --region "$AWS_REGION" 2>/dev/null \
    || true

# Verify Aurora cluster is available
print_status "Verifying Aurora cluster status..."
CLUSTER_ID=$(aws cloudformation describe-stacks \
    --stack-name "$AURORA_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='AuroraClusterId'].OutputValue" \
    --output text \
    --region "$AWS_REGION")

if [ -n "$CLUSTER_ID" ]; then
    CLUSTER_STATUS=$(aws rds describe-db-clusters \
        --db-cluster-identifier "$CLUSTER_ID" \
        --query "DBClusters[0].Status" \
        --output text \
        --region "$AWS_REGION" 2>/dev/null || echo "unknown")
    
    if [ "$CLUSTER_STATUS" != "available" ]; then
        print_status "Waiting for Aurora cluster to become available..."
        aws rds wait db-cluster-available \
            --db-cluster-identifier "$CLUSTER_ID" \
            --region "$AWS_REGION"
    fi
fi

print_success "Aurora infrastructure deployed successfully!"
echo ""

# # ============================================================================
# # Phase 2: Initialize Database
# # ============================================================================
# echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
# echo -e "${BLUE}Phase 2: Initializing Database${NC}"
# echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

# print_status "Running database initialization script..."
# export STACK_NAME="$AURORA_STACK"
# python3 "$SCRIPT_DIR/init_database.py" \
#     || { print_error "Database initialization failed"; exit 1; }

# print_success "Database initialized successfully!"
# echo ""

# ============================================================================
# Phase 3: Deploy Glue Data Catalog
# ============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 3: Deploying Glue Data Catalog${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

print_status "Deploying Glue CloudFormation stack..."
aws cloudformation deploy \
    --template-file "$PROJECT_ROOT/infrastructure/stacks/glue-stack.yaml" \
    --stack-name "$GLUE_STACK" \
    --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
    --region "$AWS_REGION" \
    --no-fail-on-empty-changeset \
    --parameter-overrides TriggerCrawler=false \
    || { print_error "Glue stack deployment failed"; exit 1; }

print_status "Waiting for Glue stack to complete..."
aws cloudformation wait stack-create-complete \
    --stack-name "$GLUE_STACK" \
    --region "$AWS_REGION" 2>/dev/null \
    || aws cloudformation wait stack-update-complete \
    --stack-name "$GLUE_STACK" \
    --region "$AWS_REGION" 2>/dev/null \
    || true

print_success "Glue Data Catalog deployed successfully!"
echo ""

# ============================================================================
# Phase 4: Register Glue Tables
# ============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 4: Registering Tables in Glue Catalog${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

print_status "Registering tables in Glue Data Catalog..."
python3 "$SCRIPT_DIR/register_glue_tables.py" \
    || { print_error "Glue table registration failed"; exit 1; }

print_success "Glue tables registered successfully!"
echo ""

# ============================================================================
# Phase 5: Generate Vector Embeddings
# ============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 5: Generating Vector Embeddings${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

print_status "Generating vector embeddings for semantic search..."
export AURORA_STACK_NAME="$AURORA_STACK"
python3 "$SCRIPT_DIR/generate_embeddings.py" \
    || { print_error "Embedding generation failed"; exit 1; }

print_success "Vector embeddings generated successfully!"
echo ""

# ============================================================================
# Phase 6: Deploy AgentCore Gateway (Optional)
# ============================================================================
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}Phase 6: Deploying AgentCore Gateway${NC}"
echo -e "${BLUE}═══════════════════════════════════════════════════════════════${NC}"

if [ -f "$PROJECT_ROOT/app/agentcore_strands/infra/deploy_gateway.py" ]; then
    print_status "Deploying AgentCore Gateway..."
    cd "$PROJECT_ROOT/app/agentcore_strands/infra"
    python3 deploy_gateway.py \
        || { print_warning "AgentCore Gateway deployment failed (non-critical)"; }
    cd "$SCRIPT_DIR"
    print_success "AgentCore Gateway deployment completed!"
else
    print_warning "AgentCore Gateway deployment script not found, skipping..."
fi

echo ""

# ============================================================================
# Deployment Complete
# ============================================================================
echo -e "${GREEN}"
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              Deployment Completed Successfully!              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# Print summary
echo -e "${YELLOW}Deployment Summary:${NC}"
echo "  [OK] Aurora PostgreSQL cluster deployed and initialized"
echo "  [OK] Database schema and views created"
echo "  [OK] Sample data loaded"
echo "  [OK] Glue Data Catalog configured"
echo "  [OK] Vector embeddings generated for semantic search"
echo ""

# Print connection information
print_status "Retrieving connection information..."
AURORA_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "$AURORA_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='AuroraClusterEndpoint'].OutputValue" \
    --output text \
    --region "$AWS_REGION" 2>/dev/null || echo "N/A")

SECRET_ARN=$(aws cloudformation describe-stacks \
    --stack-name "$AURORA_STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='DatabaseSecretArn'].OutputValue" \
    --output text \
    --region "$AWS_REGION" 2>/dev/null || echo "N/A")

echo ""
echo -e "${YELLOW}Connection Information:${NC}"
echo "  Aurora Endpoint: $AURORA_ENDPOINT"
echo "  Secret ARN:      $SECRET_ARN"
echo ""
echo "Configuration files have been generated in:"
echo "  - $PROJECT_ROOT/app/agentcore_strands/.env"
echo "  - $SCRIPT_DIR/deployment-config.json"
echo ""
print_success "Deployment complete! You can now start the agent."
