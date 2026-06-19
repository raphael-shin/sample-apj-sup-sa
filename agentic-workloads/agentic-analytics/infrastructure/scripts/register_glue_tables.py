#!/usr/bin/env python3
"""
Manually register Aurora PostgreSQL tables in AWS Glue Data Catalog.
"""
import boto3
import json
import os

AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')
DATABASE_NAME = 'timely_unicorn'
JDBC_CONNECTION = 'agentic-analytics-aurora-connection-v2'
AURORA_ENDPOINT = 'agentic-analytics-cluster.cluster-xxxxxxxxxxxx.us-west-2.rds.amazonaws.com'

# Paths
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

glue_client = boto3.client('glue', region_name=AWS_REGION)

# Table definitions based on schema.sql
TABLES = {
    'subscription_plans': {
        'columns': [
            {'Name': 'plan_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'plan_name', 'Type': 'string', 'Comment': 'Unique plan name'},
            {'Name': 'user_limit', 'Type': 'int', 'Comment': 'Maximum users allowed'},
            {'Name': 'storage_limit_gb', 'Type': 'decimal(10,2)', 'Comment': 'Storage limit in GB'},
            {'Name': 'monthly_price', 'Type': 'decimal(10,2)', 'Comment': 'Monthly subscription price'},
            {'Name': 'is_custom', 'Type': 'boolean', 'Comment': 'Whether this is a custom plan'},
            {'Name': 'description', 'Type': 'string', 'Comment': 'Plan description'},
            {'Name': 'is_active', 'Type': 'boolean', 'Comment': 'Whether plan is active'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Subscription plan definitions for unicorn rental businesses'
    },
    'accounts': {
        'columns': [
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'plan_id', 'Type': 'string', 'Comment': 'Foreign key to subscription_plans'},
            {'Name': 'account_name', 'Type': 'string', 'Comment': 'Business account name'},
            {'Name': 'status', 'Type': 'string', 'Comment': 'Account status: active, suspended, terminated'},
            {'Name': 'current_storage_usage_gb', 'Type': 'decimal(10,2)', 'Comment': 'Current storage usage'},
            {'Name': 'current_user_count', 'Type': 'int', 'Comment': 'Current number of users'},
            {'Name': 'billing_email', 'Type': 'string', 'Comment': 'Billing contact email'},
            {'Name': 'billing_address_line1', 'Type': 'string', 'Comment': 'Billing address line 1'},
            {'Name': 'billing_address_line2', 'Type': 'string', 'Comment': 'Billing address line 2'},
            {'Name': 'billing_city', 'Type': 'string', 'Comment': 'Billing city'},
            {'Name': 'billing_state_province', 'Type': 'string', 'Comment': 'Billing state/province'},
            {'Name': 'billing_postal_code', 'Type': 'string', 'Comment': 'Billing postal code'},
            {'Name': 'billing_country', 'Type': 'string', 'Comment': 'Billing country'},
            {'Name': 'next_billing_date', 'Type': 'date', 'Comment': 'Next billing date'},
            {'Name': 'trial_end_date', 'Type': 'date', 'Comment': 'Trial period end date'},
            {'Name': 'activated_at', 'Type': 'timestamp', 'Comment': 'Account activation timestamp'},
            {'Name': 'suspended_at', 'Type': 'timestamp', 'Comment': 'Account suspension timestamp'},
            {'Name': 'terminated_at', 'Type': 'timestamp', 'Comment': 'Account termination timestamp'},
            {'Name': 'industry', 'Type': 'string', 'Comment': 'Business industry'},
            {'Name': 'employee_count', 'Type': 'int', 'Comment': 'Number of employees'},
            {'Name': 'website', 'Type': 'string', 'Comment': 'Business website'},
            {'Name': 'billing_cycle', 'Type': 'string', 'Comment': 'Billing cycle: monthly, quarterly, annual'},
            {'Name': 'headquarters_address_line1', 'Type': 'string', 'Comment': 'HQ address line 1'},
            {'Name': 'headquarters_address_line2', 'Type': 'string', 'Comment': 'HQ address line 2'},
            {'Name': 'headquarters_city', 'Type': 'string', 'Comment': 'HQ city'},
            {'Name': 'headquarters_state_province', 'Type': 'string', 'Comment': 'HQ state/province'},
            {'Name': 'headquarters_postal_code', 'Type': 'string', 'Comment': 'HQ postal code'},
            {'Name': 'headquarters_country', 'Type': 'string', 'Comment': 'HQ country'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Unicorn rental business accounts'
    },
    'customers': {
        'columns': [
            {'Name': 'customer_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'customer_type', 'Type': 'string', 'Comment': 'Customer type: individual, organization'},
            {'Name': 'first_name', 'Type': 'string', 'Comment': 'Customer first name'},
            {'Name': 'last_name', 'Type': 'string', 'Comment': 'Customer last name'},
            {'Name': 'organization_name', 'Type': 'string', 'Comment': 'Organization name if applicable'},
            {'Name': 'email', 'Type': 'string', 'Comment': 'Customer email'},
            {'Name': 'phone_number', 'Type': 'string', 'Comment': 'Customer phone number'},
            {'Name': 'address_line1', 'Type': 'string', 'Comment': 'Address line 1'},
            {'Name': 'address_line2', 'Type': 'string', 'Comment': 'Address line 2'},
            {'Name': 'city', 'Type': 'string', 'Comment': 'City'},
            {'Name': 'state_province', 'Type': 'string', 'Comment': 'State/province'},
            {'Name': 'postal_code', 'Type': 'string', 'Comment': 'Postal code'},
            {'Name': 'country', 'Type': 'string', 'Comment': 'Country'},
            {'Name': 'billing_preference', 'Type': 'string', 'Comment': 'Billing preference: email, mail, both'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
            {'Name': 'department', 'Type': 'string', 'Comment': 'Department if organization'},
            {'Name': 'title', 'Type': 'string', 'Comment': 'Job title'},
        ],
        'description': 'Customers who rent unicorns'
    },
    'users': {
        'columns': [
            {'Name': 'user_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'username', 'Type': 'string', 'Comment': 'Unique username'},
            {'Name': 'email', 'Type': 'string', 'Comment': 'Unique email'},
            {'Name': 'password_hash', 'Type': 'string', 'Comment': 'Hashed password'},
            {'Name': 'first_name', 'Type': 'string', 'Comment': 'User first name'},
            {'Name': 'last_name', 'Type': 'string', 'Comment': 'User last name'},
            {'Name': 'role', 'Type': 'string', 'Comment': 'User role: admin, staff, readonly'},
            {'Name': 'phone_number', 'Type': 'string', 'Comment': 'Phone number'},
            {'Name': 'is_active', 'Type': 'boolean', 'Comment': 'Whether user is active'},
            {'Name': 'last_login_at', 'Type': 'timestamp', 'Comment': 'Last login timestamp'},
            {'Name': 'failed_login_attempts', 'Type': 'int', 'Comment': 'Failed login attempt count'},
            {'Name': 'locked_until', 'Type': 'timestamp', 'Comment': 'Account locked until timestamp'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Staff and admin users for rental businesses'
    },
    'unicorns': {
        'columns': [
            {'Name': 'unicorn_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'unicorn_uid', 'Type': 'string', 'Comment': 'Unique unicorn identifier'},
            {'Name': 'name', 'Type': 'string', 'Comment': 'Unicorn name'},
            {'Name': 'friendly_name', 'Type': 'string', 'Comment': 'Friendly display name'},
            {'Name': 'year_of_making', 'Type': 'int', 'Comment': 'Year unicorn was made'},
            {'Name': 'breed', 'Type': 'string', 'Comment': 'Unicorn breed'},
            {'Name': 'color', 'Type': 'string', 'Comment': 'Unicorn color'},
            {'Name': 'horn_length_cm', 'Type': 'decimal(5,2)', 'Comment': 'Horn length in cm'},
            {'Name': 'horn_material', 'Type': 'string', 'Comment': 'Horn material'},
            {'Name': 'seat_capacity', 'Type': 'int', 'Comment': 'Number of seats'},
            {'Name': 'magic_abilities', 'Type': 'string', 'Comment': 'Magic abilities description'},
            {'Name': 'max_speed_kmh', 'Type': 'decimal(5,2)', 'Comment': 'Maximum speed in km/h'},
            {'Name': 'fuel_type', 'Type': 'string', 'Comment': 'Fuel type'},
            {'Name': 'fuel_capacity', 'Type': 'decimal(10,2)', 'Comment': 'Fuel capacity'},
            {'Name': 'hourly_rate', 'Type': 'decimal(10,2)', 'Comment': 'Hourly rental rate'},
            {'Name': 'last_service_date', 'Type': 'date', 'Comment': 'Last service date'},
            {'Name': 'next_service_due', 'Type': 'date', 'Comment': 'Next service due date'},
            {'Name': 'purchase_date', 'Type': 'date', 'Comment': 'Purchase date'},
            {'Name': 'purchase_price', 'Type': 'decimal(10,2)', 'Comment': 'Purchase price'},
            {'Name': 'is_active', 'Type': 'boolean', 'Comment': 'Whether unicorn is active'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
            {'Name': 'is_available', 'Type': 'boolean', 'Comment': 'Whether unicorn is available'},
        ],
        'description': 'Unicorn fleet inventory'
    },
    'unicorn_availability': {
        'columns': [
            {'Name': 'availability_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'unicorn_id', 'Type': 'string', 'Comment': 'Foreign key to unicorns'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'status', 'Type': 'string', 'Comment': 'Status: available, maintenance, repair, cleaning, reserved, out_of_service'},
            {'Name': 'reason', 'Type': 'string', 'Comment': 'Reason for status'},
            {'Name': 'expected_available_at', 'Type': 'timestamp', 'Comment': 'Expected availability timestamp'},
            {'Name': 'updated_by', 'Type': 'string', 'Comment': 'Foreign key to users'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Unicorn availability status tracking'
    },
    'bookings': {
        'columns': [
            {'Name': 'booking_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'customer_id', 'Type': 'string', 'Comment': 'Foreign key to customers'},
            {'Name': 'unicorn_id', 'Type': 'string', 'Comment': 'Foreign key to unicorns'},
            {'Name': 'user_id', 'Type': 'string', 'Comment': 'Foreign key to users (staff who created)'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'booking_reference', 'Type': 'string', 'Comment': 'Unique booking reference'},
            {'Name': 'start_datetime', 'Type': 'timestamp', 'Comment': 'Booking start time'},
            {'Name': 'end_datetime', 'Type': 'timestamp', 'Comment': 'Booking end time'},
            {'Name': 'actual_start_datetime', 'Type': 'timestamp', 'Comment': 'Actual start time'},
            {'Name': 'actual_end_datetime', 'Type': 'timestamp', 'Comment': 'Actual end time'},
            {'Name': 'base_hourly_rate', 'Type': 'decimal(10,2)', 'Comment': 'Base hourly rate'},
            {'Name': 'total_cost', 'Type': 'decimal(10,2)', 'Comment': 'Total booking cost'},
            {'Name': 'special_requests', 'Type': 'string', 'Comment': 'Special requests'},
            {'Name': 'pickup_location', 'Type': 'string', 'Comment': 'Pickup location'},
            {'Name': 'dropoff_location', 'Type': 'string', 'Comment': 'Dropoff location'},
            {'Name': 'cancellation_reason', 'Type': 'string', 'Comment': 'Cancellation reason if cancelled'},
            {'Name': 'is_recurring', 'Type': 'boolean', 'Comment': 'Whether booking is recurring'},
            {'Name': 'recurrence_pattern', 'Type': 'string', 'Comment': 'Recurrence pattern'},
            {'Name': 'return_inspection_notes', 'Type': 'string', 'Comment': 'Return inspection notes'},
            {'Name': 'late_return_hours', 'Type': 'decimal(5,2)', 'Comment': 'Hours returned late'},
            {'Name': 'damage_assessment', 'Type': 'string', 'Comment': 'Damage assessment notes'},
            {'Name': 'damage_cost_estimate', 'Type': 'decimal(10,2)', 'Comment': 'Estimated damage cost'},
            {'Name': 'is_completed', 'Type': 'boolean', 'Comment': 'Whether booking is completed'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Unicorn rental bookings'
    },
    'transactions': {
        'columns': [
            {'Name': 'transaction_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'customer_id', 'Type': 'string', 'Comment': 'Foreign key to customers'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'booking_id', 'Type': 'string', 'Comment': 'Foreign key to bookings'},
            {'Name': 'parent_transaction_id', 'Type': 'string', 'Comment': 'Parent transaction for refunds'},
            {'Name': 'transaction_type', 'Type': 'string', 'Comment': 'Type: booking_fee, subscription, storage_overage, refund, adjustment'},
            {'Name': 'amount', 'Type': 'decimal(10,2)', 'Comment': 'Transaction amount'},
            {'Name': 'currency', 'Type': 'string', 'Comment': 'Currency code'},
            {'Name': 'status', 'Type': 'string', 'Comment': 'Status: pending, processing, completed, failed, refunded'},
            {'Name': 'payment_method', 'Type': 'string', 'Comment': 'Payment method: credit_card, bank_transfer, paypal, invoice'},
            {'Name': 'payment_reference', 'Type': 'string', 'Comment': 'Payment reference'},
            {'Name': 'tax_amount', 'Type': 'decimal(10,2)', 'Comment': 'Tax amount'},
            {'Name': 'tax_rate', 'Type': 'decimal(5,4)', 'Comment': 'Tax rate'},
            {'Name': 'description', 'Type': 'string', 'Comment': 'Transaction description'},
            {'Name': 'processed_at', 'Type': 'timestamp', 'Comment': 'Processing timestamp'},
            {'Name': 'refunded_at', 'Type': 'timestamp', 'Comment': 'Refund timestamp'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Financial transactions'
    },
    'subscription_tracker': {
        'columns': [
            {'Name': 'tracker_id', 'Type': 'string', 'Comment': 'UUID primary key'},
            {'Name': 'account_id', 'Type': 'string', 'Comment': 'Foreign key to accounts'},
            {'Name': 'datetime', 'Type': 'timestamp', 'Comment': 'Tracking timestamp'},
            {'Name': 'plan_id', 'Type': 'string', 'Comment': 'Foreign key to subscription_plans'},
            {'Name': 'monthly_price', 'Type': 'decimal(10,2)', 'Comment': 'Monthly price at time of tracking'},
            {'Name': 'hourly_price', 'Type': 'decimal(10,6)', 'Comment': 'Calculated hourly price'},
            {'Name': 'created_at', 'Type': 'timestamp', 'Comment': 'Record creation timestamp'},
            {'Name': 'updated_at', 'Type': 'timestamp', 'Comment': 'Record update timestamp'},
        ],
        'description': 'Subscription history tracking'
    },
}


def ensure_database_exists():
    """Create the Glue database if it doesn't exist."""
    try:
        glue_client.get_database(Name=DATABASE_NAME)
        print(f"Database '{DATABASE_NAME}' already exists")
    except glue_client.exceptions.EntityNotFoundException:
        print(f"Creating database '{DATABASE_NAME}'...")
        glue_client.create_database(
            DatabaseInput={
                'Name': DATABASE_NAME,
                'Description': 'Timely Unicorn Rental Management System database'
            }
        )
        print(f"  [OK] Database created")


def create_table(table_name, table_def):
    """Create or update a table in the Glue catalog."""
    location = f"jdbc:postgresql://{AURORA_ENDPOINT}:5432/{DATABASE_NAME}/public/{table_name}"
    
    table_input = {
        'Name': table_name,
        'Description': table_def['description'],
        'StorageDescriptor': {
            'Columns': table_def['columns'],
            'Location': location,
            'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
            'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
            'SerdeInfo': {
                'SerializationLibrary': 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe'
            },
        },
        'TableType': 'EXTERNAL_TABLE',
        'Parameters': {
            'classification': 'postgresql',
            'connectionName': JDBC_CONNECTION,
            'typeOfData': 'table',
        }
    }
    
    try:
        # Try to update existing table
        glue_client.update_table(
            DatabaseName=DATABASE_NAME,
            TableInput=table_input
        )
        print(f"  [OK] Updated table: {table_name}")
    except glue_client.exceptions.EntityNotFoundException:
        # Create new table
        glue_client.create_table(
            DatabaseName=DATABASE_NAME,
            TableInput=table_input
        )
        print(f"  [OK] Created table: {table_name}")


def export_metadata():
    """Export catalog metadata to JSON with relationships and enum values."""
    print("\nExporting metadata...")
    
    # Define foreign key relationships
    RELATIONSHIPS = {
        'accounts': [
            {'column': 'plan_id', 'references': {'table': 'subscription_plans', 'column': 'plan_id'}}
        ],
        'customers': [
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}}
        ],
        'users': [
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}}
        ],
        'unicorns': [
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}}
        ],
        'unicorn_availability': [
            {'column': 'unicorn_id', 'references': {'table': 'unicorns', 'column': 'unicorn_id'}},
            {'column': 'updated_by', 'references': {'table': 'users', 'column': 'user_id'}}
        ],
        'bookings': [
            {'column': 'customer_id', 'references': {'table': 'customers', 'column': 'customer_id'}},
            {'column': 'unicorn_id', 'references': {'table': 'unicorns', 'column': 'unicorn_id'}},
            {'column': 'user_id', 'references': {'table': 'users', 'column': 'user_id'}},
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}}
        ],
        'transactions': [
            {'column': 'customer_id', 'references': {'table': 'customers', 'column': 'customer_id'}},
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}},
            {'column': 'booking_id', 'references': {'table': 'bookings', 'column': 'booking_id'}},
            {'column': 'parent_transaction_id', 'references': {'table': 'transactions', 'column': 'transaction_id'}}
        ],
        'subscription_tracker': [
            {'column': 'account_id', 'references': {'table': 'accounts', 'column': 'account_id'}},
            {'column': 'plan_id', 'references': {'table': 'subscription_plans', 'column': 'plan_id'}}
        ]
    }
    
    # Define enum values for columns
    ENUM_VALUES = {
        'customer_type': ['individual', 'organization'],
        'billing_preference': ['email', 'mail', 'both'],
        'billing_cycle': ['monthly', 'quarterly', 'annual'],
        'status': {
            'accounts': ['active', 'suspended', 'terminated'],
            'unicorn_availability': ['available', 'maintenance', 'repair', 'cleaning', 'reserved', 'out_of_service'],
            'transactions': ['pending', 'processing', 'completed', 'failed', 'refunded']
        },
        'role': ['admin', 'staff', 'readonly'],
        'transaction_type': ['booking_fee', 'subscription', 'storage_overage', 'refund', 'adjustment'],
        'payment_method': ['credit_card', 'bank_transfer', 'paypal', 'invoice']
    }
    
    metadata = {
        "database": DATABASE_NAME,
        "connection": {
            "type": "postgresql",
            "host": AURORA_ENDPOINT,
            "port": 5432,
            "database": DATABASE_NAME
        },
        "tables": []
    }
    
    paginator = glue_client.get_paginator('get_tables')
    for page in paginator.paginate(DatabaseName=DATABASE_NAME):
        for table in page['TableList']:
            table_name = table['Name']
            table_info = {
                "name": table_name,
                "description": table.get('Description', ''),
                "columns": [],
                "primary_key": f"{table_name[:-1]}_id" if table_name.endswith('s') else f"{table_name}_id",
                "foreign_keys": RELATIONSHIPS.get(table_name, [])
            }
            
            storage_desc = table.get('StorageDescriptor', {})
            for col in storage_desc.get('Columns', []):
                col_name = col['Name']
                col_info = {
                    "name": col_name,
                    "type": col['Type'],
                    "comment": col.get('Comment', ''),
                    "nullable": col_name not in ['created_at', 'updated_at'] and '_id' not in col_name
                }
                
                # Add enum values if applicable
                if col_name in ENUM_VALUES:
                    enum_val = ENUM_VALUES[col_name]
                    if isinstance(enum_val, dict):
                        if table_name in enum_val:
                            col_info['enum_values'] = enum_val[table_name]
                    else:
                        col_info['enum_values'] = enum_val
                
                table_info["columns"].append(col_info)
            
            metadata["tables"].append(table_info)
    
    # Sort tables by dependency order for loading
    load_order = ['subscription_plans', 'accounts', 'users', 'customers', 'unicorns', 
                  'unicorn_availability', 'bookings', 'transactions', 'subscription_tracker']
    metadata["tables"].sort(key=lambda t: load_order.index(t['name']) if t['name'] in load_order else 999)
    metadata["load_order"] = load_order
    
    output_path = os.path.join(SCRIPT_DIR, 'glue-catalog-metadata.json')
    with open(output_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"  [OK] Exported {len(metadata['tables'])} tables to {output_path}")
    return metadata


def main():
    print("=" * 60)
    print("Registering Tables in AWS Glue Data Catalog")
    print("=" * 60)
    print(f"Database: {DATABASE_NAME}")
    print(f"Region: {AWS_REGION}")
    print()
    
    # Ensure database exists
    ensure_database_exists()
    print()
    
    # Register tables
    print("Registering tables...")
    for table_name, table_def in TABLES.items():
        create_table(table_name, table_def)
    print()
    
    # Export metadata
    export_metadata()
    
    print()
    print("=" * 60)
    print("Done!")
    print("=" * 60)


if __name__ == '__main__':
    main()
