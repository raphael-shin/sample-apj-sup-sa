#!/usr/bin/env python3
"""
Initialize Aurora PostgreSQL database with schema, data, and views.
Reads CloudFormation outputs to get connection details.
"""
import boto3
import csv
import os
import sys
import time
import json

# Default stack name
STACK_NAME = os.environ.get('STACK_NAME', 'agentic-analytics-aurora')
AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')

# Initialize clients
cf_client = boto3.client('cloudformation', region_name=AWS_REGION)
rds_client = boto3.client('rds-data', region_name=AWS_REGION)

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
SCHEMA_PATH = os.path.join(PROJECT_ROOT, 'dataset', 'schema', 'schema.sql')
DATA_DIR = os.path.join(PROJECT_ROOT, 'dataset', 'data')


def get_stack_outputs():
    """Get CloudFormation stack outputs."""
    try:
        response = cf_client.describe_stacks(StackName=STACK_NAME)
        outputs = {}
        for output in response['Stacks'][0]['Outputs']:
            outputs[output['OutputKey']] = output['OutputValue']
        return outputs
    except Exception as e:
        print(f"Error getting stack outputs: {e}")
        sys.exit(1)


def execute_sql(resource_arn, secret_arn, database, sql, params=None):
    """Execute SQL statement via RDS Data API."""
    kwargs = {
        "resourceArn": resource_arn,
        "secretArn": secret_arn,
        "database": database,
        "sql": sql
    }
    if params:
        kwargs["parameters"] = params
    return rds_client.execute_statement(**kwargs)


def enable_pgvector(resource_arn, secret_arn, database):
    """Enable pgvector extension."""
    print("Enabling pgvector extension...")
    try:
        execute_sql(resource_arn, secret_arn, database, "CREATE EXTENSION IF NOT EXISTS vector")
        print("[OK] pgvector extension enabled")
    except Exception as e:
        print(f"[FAIL] Failed to enable pgvector: {e}")
        raise


def load_schema(resource_arn, secret_arn, database):
    """Load database schema from schema.sql."""
    print(f"Loading schema from {SCHEMA_PATH}...")
    
    if not os.path.exists(SCHEMA_PATH):
        print(f"[FAIL] Schema file not found: {SCHEMA_PATH}")
        sys.exit(1)
    
    with open(SCHEMA_PATH, 'r') as f:
        schema_sql = f.read()
    
    # Split by semicolons but handle dollar-quoted strings (for functions)
    statements = []
    current_stmt = []
    in_dollar_quote = False
    
    for line in schema_sql.split('\n'):
        stripped = line.strip()
        
        # Skip comments
        if stripped.startswith('--'):
            continue
        
        # Track dollar quotes for function bodies
        if '$$' in line and not in_dollar_quote:
            in_dollar_quote = True
        elif '$$' in line and in_dollar_quote:
            in_dollar_quote = False
        
        current_stmt.append(line)
        
        # End of statement (semicolon not in dollar quote)
        if stripped.endswith(';') and not in_dollar_quote:
            stmt = '\n'.join(current_stmt).strip()
            if stmt and not stmt.startswith('--'):
                statements.append(stmt)
            current_stmt = []
    
    # Execute each statement
    success_count = 0
    error_count = 0
    
    for i, stmt in enumerate(statements):
        if not stmt.strip():
            continue
        try:
            execute_sql(resource_arn, secret_arn, database, stmt)
            success_count += 1
            # Show progress for long operations
            if (i + 1) % 10 == 0:
                print(f"  Executed {i + 1}/{len(statements)} statements...")
        except Exception as e:
            error_str = str(e)
            # Ignore "already exists" errors
            if 'already exists' in error_str.lower():
                success_count += 1
            else:
                print(f"  Warning: Statement {i + 1} failed: {error_str[:100]}")
                error_count += 1
    
    print(f"[OK] Schema loaded: {success_count} statements executed, {error_count} errors")


# Column type mappings for proper casting
UUID_COLS = {'plan_id', 'account_id', 'user_id', 'customer_id', 'unicorn_id', 'booking_id', 
             'transaction_id', 'availability_id', 'updated_by', 'parent_transaction_id', 'tracker_id'}
INT_COLS = {'user_limit', 'current_user_count', 'employee_count', 'year_of_making', 'seat_capacity', 'failed_login_attempts'}
DECIMAL_COLS = {'storage_limit_gb', 'monthly_price', 'current_storage_usage_gb', 'horn_length_cm', 'max_speed_kmh',
                'fuel_capacity', 'hourly_rate', 'purchase_price', 'base_hourly_rate', 'total_cost', 'late_return_hours',
                'damage_cost_estimate', 'amount', 'tax_amount', 'tax_rate', 'hourly_price'}
BOOL_COLS = {'is_custom', 'is_active', 'is_available', 'is_recurring', 'is_completed'}
TIMESTAMP_COLS = {'created_at', 'updated_at', 'activated_at', 'suspended_at', 'terminated_at', 'last_login_at',
                  'locked_until', 'expected_available_at', 'start_datetime', 'end_datetime', 'actual_start_datetime',
                  'actual_end_datetime', 'processed_at', 'refunded_at', 'datetime'}
DATE_COLS = {'next_billing_date', 'trial_end_date', 'last_service_date', 'next_service_due', 'purchase_date'}
ENUM_COLS = {
    'status': {'accounts': 'account_status_enum', 'unicorn_availability': 'unicorn_availability_status_enum', 'transactions': 'transaction_status_enum'},
    'billing_cycle': 'billing_cycle_enum',
    'billing_preference': 'billing_preference_enum',
    'role': 'user_role_enum',
    'customer_type': 'customer_type_enum',
    'transaction_type': 'transaction_type_enum',
    'payment_method': 'payment_method_enum'
}


def get_cast(col, table):
    """Get SQL type cast for a column."""
    if col in UUID_COLS:
        return '::uuid'
    if col in INT_COLS:
        return '::integer'
    if col in DECIMAL_COLS:
        return '::decimal'
    if col in BOOL_COLS:
        return '::boolean'
    if col in TIMESTAMP_COLS:
        return '::timestamp'
    if col in DATE_COLS:
        return '::date'
    if col in ENUM_COLS:
        enum_type = ENUM_COLS[col]
        if isinstance(enum_type, dict):
            return f'::{enum_type.get(table, "text")}'
        return f'::{enum_type}'
    return ''


def execute_batch_sql(resource_arn, secret_arn, database, sql, param_sets):
    """Execute batch SQL statement via RDS Data API."""
    return rds_client.batch_execute_statement(
        resourceArn=resource_arn,
        secretArn=secret_arn,
        database=database,
        sql=sql,
        parameterSets=param_sets
    )


def load_csv(resource_arn, secret_arn, database, table, filename, columns=None):
    """Load CSV data into a table using batch inserts."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        print(f"  Skipping {filename} - not found")
        return 0
    
    with open(path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    if not rows:
        print(f"  Skipping {filename} - empty")
        return 0
    
    cols = columns or list(rows[0].keys())
    placeholders = ', '.join([f":{c}{get_cast(c, table)}" for c in cols])
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    
    # Use batch execute for better performance
    BATCH_SIZE = 100  # RDS Data API batch limit
    count = 0
    
    for batch_start in range(0, len(rows), BATCH_SIZE):
        batch_rows = rows[batch_start:batch_start + BATCH_SIZE]
        param_sets = []
        
        for row in batch_rows:
            params = []
            for c in cols:
                val = row.get(c, '')
                if val == '' or val is None:
                    params.append({"name": c, "value": {"isNull": True}})
                else:
                    params.append({"name": c, "value": {"stringValue": str(val)}})
            param_sets.append(params)
        
        try:
            execute_batch_sql(resource_arn, secret_arn, database, sql, param_sets)
            count += len(batch_rows)
            if count % 1000 == 0 or count == len(rows):
                print(f"    {count}/{len(rows)} rows...")
        except Exception as e:
            print(f"    Batch error at {count}: {str(e)[:80]}")
            # Fall back to single inserts
            for row in batch_rows:
                params = []
                for c in cols:
                    val = row.get(c, '')
                    if val == '' or val is None:
                        params.append({"name": c, "value": {"isNull": True}})
                    else:
                        params.append({"name": c, "value": {"stringValue": str(val)}})
                try:
                    execute_sql(resource_arn, secret_arn, database, sql, params)
                    count += 1
                except:
                    pass
    
    return count


def load_all_data(resource_arn, secret_arn, database):
    """Load all CSV data in foreign-key order."""
    print("Loading CSV data...")
    
    # Load order respects foreign key constraints
    tables = [
        ("subscription_plans", "subscription_plans.csv", None),
        ("accounts", "accounts.csv", None),
        ("users", "users.csv", None),
        ("customers", "customers.csv", [
            "customer_id", "account_id", "customer_type", "first_name", "last_name",
            "organization_name", "email", "phone_number", "address_line1", "address_line2",
            "city", "state_province", "postal_code", "country", "billing_preference",
            "created_at", "updated_at", "department", "title"
        ]),
        ("unicorns", "unicorns.csv", None),
        ("unicorn_availability", "unicorn_availability.csv", None),
        ("bookings", "bookings.csv", None),
        ("transactions", "transactions.csv", None),
        ("subscription_tracker", "subscription_tracker.csv", None),
    ]
    
    total_loaded = 0
    for table, filename, columns in tables:
        print(f"  Loading {table}...")
        count = load_csv(resource_arn, secret_arn, database, table, filename, columns)
        print(f"  [OK] Loaded {count} rows into {table}")
        total_loaded += count
    
    print(f"[OK] Data loading complete: {total_loaded} total rows")


# Analytics views definitions
VIEWS = [
    ("daily_bookings_summary", """CREATE OR REPLACE VIEW daily_bookings_summary AS
SELECT b.booking_id, b.booking_reference, CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
    u.name AS unicorn_name, b.start_datetime, b.end_datetime,
    ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0, 2) AS duration_hours,
    b.special_requests, CONCAT(us.first_name, ' ', us.last_name) AS staff_member,
    CASE WHEN b.is_completed THEN 'completed' ELSE 'confirmed' END AS status, b.pickup_location, b.created_at
FROM bookings b JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id JOIN users us ON b.user_id = us.user_id"""),

    ("monthly_revenue_summary", """CREATE OR REPLACE VIEW monthly_revenue_summary AS
SELECT TO_CHAR(t.created_at, 'YYYY-MM') AS year_month,
    SUM(CASE WHEN t.transaction_type IN ('booking_fee', 'subscription', 'storage_overage') THEN t.amount ELSE 0 END) AS total_revenue,
    SUM(CASE WHEN t.transaction_type = 'booking_fee' THEN t.amount ELSE 0 END) AS booking_fees,
    COUNT(*) AS total_transactions, COUNT(DISTINCT t.customer_id) AS unique_customers,
    COUNT(DISTINCT b.unicorn_id) AS active_unicorns,
    SUM(CASE WHEN t.transaction_type = 'refund' THEN t.amount ELSE 0 END) AS refund_amount
FROM transactions t LEFT JOIN bookings b ON t.booking_id = b.booking_id
GROUP BY TO_CHAR(t.created_at, 'YYYY-MM')"""),

    ("current_unicorn_availability", """CREATE OR REPLACE VIEW current_unicorn_availability AS
SELECT u.unicorn_id, u.name AS unicorn_name, u.friendly_name, u.account_id,
    CASE WHEN u.is_available THEN 'available'::TEXT ELSE COALESCE(ua.status::TEXT, 'out_of_service') END AS status,
    ua.reason AS status_reason, u.hourly_rate, u.color, u.breed, u.seat_capacity, u.magic_abilities
FROM unicorns u LEFT JOIN LATERAL (SELECT * FROM unicorn_availability WHERE unicorn_id = u.unicorn_id ORDER BY created_at DESC LIMIT 1) ua ON TRUE
WHERE u.is_active = TRUE"""),

    ("calendar_bookings", """CREATE OR REPLACE VIEW calendar_bookings AS
SELECT b.booking_id, b.booking_reference, DATE(b.start_datetime) AS start_date,
    b.start_datetime::TIME AS start_time, b.end_datetime::TIME AS end_time,
    CONCAT(c.first_name, ' ', c.last_name) AS customer_name, u.name AS unicorn_name,
    CONCAT(us.first_name, ' ', us.last_name) AS staff_member,
    ROUND(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0, 2) AS duration_hours,
    CASE WHEN b.is_completed THEN 'completed' ELSE 'confirmed' END AS status
FROM bookings b JOIN customers c ON b.customer_id = c.customer_id
JOIN unicorns u ON b.unicorn_id = u.unicorn_id JOIN users us ON b.user_id = us.user_id"""),

    ("customer_retention_metrics", """CREATE OR REPLACE VIEW customer_retention_metrics AS
SELECT c.customer_id, CASE WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name) ELSE c.organization_name END AS customer_name,
    c.customer_type, MIN(b.created_at) AS first_booking_date, MAX(b.created_at) AS last_booking_date,
    COUNT(b.booking_id) AS total_bookings, SUM(t.amount) AS total_spent, AVG(t.amount) AS avg_booking_value,
    CASE WHEN EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) > 90 THEN 'churned'
         WHEN EXTRACT(DAY FROM (NOW() - MAX(b.created_at))) > 30 THEN 'at_risk'
         WHEN COUNT(b.booking_id) >= 3 THEN 'active' ELSE 'new' END AS retention_segment
FROM customers c LEFT JOIN bookings b ON c.customer_id = b.customer_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id
GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name"""),

    ("top_revenue_unicorn_breeds", """CREATE OR REPLACE VIEW top_revenue_unicorn_breeds AS
SELECT u.breed, COUNT(DISTINCT u.unicorn_id) AS unicorn_count, COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue, AVG(t.amount) AS avg_booking_value,
    SUM(EXTRACT(EPOCH FROM (b.end_datetime - b.start_datetime))/3600.0) AS total_booked_hours
FROM unicorns u JOIN bookings b ON u.unicorn_id = b.unicorn_id
JOIN transactions t ON b.booking_id = t.booking_id
WHERE t.transaction_type = 'booking_fee' AND u.breed IS NOT NULL
GROUP BY u.breed ORDER BY total_revenue DESC"""),

    ("top_revenue_customers", """CREATE OR REPLACE VIEW top_revenue_customers AS
SELECT c.customer_id, CASE WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name) ELSE c.organization_name END AS customer_name,
    c.customer_type, COUNT(b.booking_id) AS total_bookings, SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value, MIN(b.start_datetime) AS first_booking_date, MAX(b.start_datetime) AS last_booking_date
FROM customers c JOIN bookings b ON c.customer_id = b.customer_id
JOIN transactions t ON b.booking_id = t.booking_id WHERE t.transaction_type = 'booking_fee'
GROUP BY c.customer_id, c.customer_type, c.first_name, c.last_name, c.organization_name ORDER BY total_revenue DESC"""),

    ("revenue_by_time_and_day", """CREATE OR REPLACE VIEW revenue_by_time_and_day AS
SELECT EXTRACT(DOW FROM b.start_datetime) AS day_of_week,
    CASE EXTRACT(DOW FROM b.start_datetime) WHEN 0 THEN 'Sunday' WHEN 1 THEN 'Monday' WHEN 2 THEN 'Tuesday'
         WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday' WHEN 6 THEN 'Saturday' END AS day_name,
    EXTRACT(HOUR FROM b.start_datetime) AS hour_of_day, COUNT(b.booking_id) AS total_bookings,
    SUM(t.amount) AS total_revenue, AVG(t.amount) AS avg_booking_value
FROM bookings b JOIN transactions t ON b.booking_id = t.booking_id WHERE t.transaction_type = 'booking_fee'
GROUP BY EXTRACT(DOW FROM b.start_datetime), EXTRACT(HOUR FROM b.start_datetime) ORDER BY total_revenue DESC"""),

    ("unicorns_due_for_maintenance", """CREATE OR REPLACE VIEW unicorns_due_for_maintenance AS
SELECT u.unicorn_id, u.account_id, u.name AS unicorn_name, u.breed, u.last_service_date, u.next_service_due, u.is_available,
    CASE WHEN u.next_service_due <= CURRENT_DATE THEN 'overdue'
         WHEN u.next_service_due <= CURRENT_DATE + INTERVAL '7 days' THEN 'due_this_week'
         WHEN u.next_service_due <= CURRENT_DATE + INTERVAL '30 days' THEN 'due_this_month' ELSE 'scheduled' END AS maintenance_urgency,
    u.next_service_due - CURRENT_DATE AS days_until_due
FROM unicorns u WHERE u.is_active = TRUE AND u.next_service_due IS NOT NULL ORDER BY u.next_service_due"""),

    ("account_subscription_status", """CREATE OR REPLACE VIEW account_subscription_status AS
SELECT a.account_id, a.account_name, a.status AS account_status, sp.plan_name, sp.monthly_price,
    sp.user_limit, sp.storage_limit_gb, a.current_user_count, a.current_storage_usage_gb,
    ROUND((a.current_user_count::DECIMAL / NULLIF(sp.user_limit, 0)) * 100, 2) AS user_limit_percent_used,
    ROUND((a.current_storage_usage_gb / NULLIF(sp.storage_limit_gb, 0)) * 100, 2) AS storage_percent_used,
    a.billing_cycle, a.next_billing_date
FROM accounts a JOIN subscription_plans sp ON a.plan_id = sp.plan_id"""),

    ("customer_lifetime_value", """CREATE OR REPLACE VIEW customer_lifetime_value AS
SELECT c.customer_id, c.account_id,
    CASE WHEN c.customer_type = 'individual' THEN CONCAT(c.first_name, ' ', c.last_name) ELSE c.organization_name END AS customer_name,
    c.customer_type, c.created_at AS customer_since, COUNT(DISTINCT b.booking_id) AS total_bookings,
    COALESCE(SUM(t.amount), 0) AS total_revenue, COALESCE(AVG(t.amount), 0) AS avg_transaction_value, MAX(b.created_at) AS last_booking_date
FROM customers c LEFT JOIN bookings b ON c.customer_id = b.customer_id
LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
GROUP BY c.customer_id, c.account_id, c.customer_type, c.first_name, c.last_name, c.organization_name, c.created_at"""),

    ("seasonal_trends", """CREATE OR REPLACE VIEW seasonal_trends AS
SELECT b.account_id, EXTRACT(MONTH FROM b.start_datetime) AS month_number, TO_CHAR(b.start_datetime, 'Month') AS month_name,
    EXTRACT(YEAR FROM b.start_datetime) AS year, COUNT(*) AS total_bookings, SUM(t.amount) AS total_revenue,
    AVG(t.amount) AS avg_booking_value, COUNT(DISTINCT b.customer_id) AS unique_customers
FROM bookings b LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
GROUP BY b.account_id, EXTRACT(MONTH FROM b.start_datetime), TO_CHAR(b.start_datetime, 'Month'), EXTRACT(YEAR FROM b.start_datetime)
ORDER BY b.account_id, year, month_number"""),

    ("customer_segmentation_by_revenue", """CREATE OR REPLACE VIEW customer_segmentation_by_revenue AS
WITH customer_totals AS (
    SELECT c.customer_id, c.account_id, c.customer_type, COUNT(b.booking_id) AS total_bookings, COALESCE(SUM(t.amount), 0) AS total_revenue
    FROM customers c LEFT JOIN bookings b ON c.customer_id = b.customer_id
    LEFT JOIN transactions t ON b.booking_id = t.booking_id AND t.transaction_type = 'booking_fee'
    GROUP BY c.customer_id, c.account_id, c.customer_type
)
SELECT account_id, CASE WHEN total_revenue >= 5000 THEN 'VIP' WHEN total_revenue >= 2000 THEN 'Premium'
    WHEN total_revenue >= 500 THEN 'Standard' ELSE 'Basic' END AS customer_segment,
    COUNT(*) AS customer_count, SUM(total_bookings) AS total_bookings, SUM(total_revenue) AS total_revenue
FROM customer_totals GROUP BY account_id, CASE WHEN total_revenue >= 5000 THEN 'VIP' WHEN total_revenue >= 2000 THEN 'Premium'
    WHEN total_revenue >= 500 THEN 'Standard' ELSE 'Basic' END"""),
]


def create_views(resource_arn, secret_arn, database):
    """Create all analytics views."""
    print("Creating analytics views...")

    success_count = 0
    for name, sql in VIEWS:
        try:
            execute_sql(resource_arn, secret_arn, database, f"DROP VIEW IF EXISTS {name} CASCADE")
            # Strip CREATE OR REPLACE so we use plain CREATE VIEW after drop
            create_sql = sql.replace("CREATE OR REPLACE VIEW", "CREATE VIEW", 1)
            execute_sql(resource_arn, secret_arn, database, create_sql)
            print(f"  [OK] Created view: {name}")
            success_count += 1
        except Exception as e:
            print(f"  [FAIL] Failed {name}: {str(e)}")
    
    print(f"[OK] Views created: {success_count}/{len(VIEWS)}")


def generate_config_files(outputs):
    """Generate configuration files for agent deployment."""
    print("Generating configuration files...")
    
    # Generate .env file for agentcore_strands
    env_content = f"""# Aurora PostgreSQL Configuration (auto-generated)
AURORA_CLUSTER_ENDPOINT={outputs.get('AuroraClusterEndpoint', '')}
AURORA_RESOURCE_ARN={outputs.get('AuroraResourceArn', '')}
DATABASE_SECRET_ARN={outputs.get('DatabaseSecretArn', '')}
DATABASE_NAME={outputs.get('DatabaseName', 'timely_unicorn')}
AWS_REGION={AWS_REGION}

# VPC Configuration
VPC_ID={outputs.get('VpcId', '')}
PRIVATE_SUBNET_1={outputs.get('PrivateSubnet1Id', '')}
PRIVATE_SUBNET_2={outputs.get('PrivateSubnet2Id', '')}
LAMBDA_SECURITY_GROUP={outputs.get('LambdaSecurityGroupId', '')}
"""
    
    env_path = os.path.join(PROJECT_ROOT, 'app', 'agentcore_strands', '.env')
    with open(env_path, 'w') as f:
        f.write(env_content)
    print(f"  [OK] Generated {env_path}")
    
    # Generate deployment config JSON
    config = {
        "stack_name": STACK_NAME,
        "region": AWS_REGION,
        "aurora": {
            "cluster_endpoint": outputs.get('AuroraClusterEndpoint', ''),
            "cluster_arn": outputs.get('AuroraClusterArn', ''),
            "resource_arn": outputs.get('AuroraResourceArn', ''),
            "secret_arn": outputs.get('DatabaseSecretArn', ''),
            "database_name": outputs.get('DatabaseName', 'timely_unicorn')
        },
        "vpc": {
            "vpc_id": outputs.get('VpcId', ''),
            "private_subnets": [
                outputs.get('PrivateSubnet1Id', ''),
                outputs.get('PrivateSubnet2Id', '')
            ],
            "public_subnets": [
                outputs.get('PublicSubnet1Id', ''),
                outputs.get('PublicSubnet2Id', '')
            ],
            "lambda_security_group": outputs.get('LambdaSecurityGroupId', ''),
            "aurora_security_group": outputs.get('AuroraSecurityGroupId', '')
        }
    }
    
    config_path = os.path.join(SCRIPT_DIR, 'deployment-config.json')
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=2)
    print(f"  [OK] Generated {config_path}")
    
    print("[OK] Configuration files generated")


def main():
    """Main initialization function."""
    print("=" * 60)
    print("Aurora PostgreSQL Database Initialization")
    print("=" * 60)
    print(f"Stack: {STACK_NAME}")
    print(f"Region: {AWS_REGION}")
    print()
    
    # Get CloudFormation outputs
    print("Reading CloudFormation outputs...")
    outputs = get_stack_outputs()
    
    resource_arn = outputs.get('AuroraResourceArn')
    secret_arn = outputs.get('DatabaseSecretArn')
    database = outputs.get('DatabaseName', 'timely_unicorn')
    
    print(f"  Cluster: {outputs.get('AuroraClusterEndpoint')}")
    print(f"  Database: {database}")
    print()
    
    # Enable pgvector
    enable_pgvector(resource_arn, secret_arn, database)
    print()
    
    # Load schema
    load_schema(resource_arn, secret_arn, database)
    print()
    
    # Load data
    load_all_data(resource_arn, secret_arn, database)
    print()
    
    # Create views
    create_views(resource_arn, secret_arn, database)
    print()
    
    # Generate config files
    generate_config_files(outputs)
    print()
    
    print("=" * 60)
    print("Database initialization complete!")
    print("=" * 60)


if __name__ == '__main__':
    main()
